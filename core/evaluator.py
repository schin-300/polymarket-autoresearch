"""Evaluation engine — scorecards, calibration tracking, reports."""

import math, statistics
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict

from core.portfolio import get_performance_by_strategy, get_recent_signals


@dataclass
class StrategyScorecard:
    strategy: str
    # Raw counts
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    # PnL
    total_pnl: float = 0.0
    mean_pnl: float = 0.0
    std_pnl: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    roi: float = 0.0
    # Calibration
    brier_score: float = 1.0
    calibration_error: float = 0.0
    # Edge
    avg_edge: float = 0.0
    edge_decay: float = 0.0  # trend of edge over time
    # Signal
    signal_rate: float = 0.0
    signal_count: int = 0
    # Status
    status: str = "active"  # active | paused | archived
    pause_reason: str = ""
    last_updated: float = 0.0


class Evaluator:
    """Scores and tracks strategy performance over time."""

    def __init__(self):
        self.scorecards: dict[str, StrategyScorecard] = {}
        self._snapshots: list[dict] = []  # in-memory time series

    def score(self, strategy: str, stats: dict,
              signals: list[dict] = None, resolved_trades: list[dict] = None) -> StrategyScorecard:
        """Compute scorecard for a strategy."""
        sc = self.scorecards.get(strategy, StrategyScorecard(strategy=strategy))

        sc.total_trades = stats.get("total_trades", 0)
        sc.wins = stats.get("wins", 0)
        sc.losses = stats.get("losses", 0)
        sc.total_pnl = stats.get("total_pnl", 0)
        sc.mean_pnl = stats.get("mean_pnl", 0)
        sc.sharpe = stats.get("sharpe", 0)
        sc.max_drawdown = stats.get("max_drawdown", 0)
        sc.roi = stats.get("roi", 0)
        sc.unrealized_pnl = stats.get("unrealized_pnl", 0)

        # Edge from resolved trades
        if resolved_trades:
            edges = [t.get("edge", 0) for t in resolved_trades if t.get("edge")]
            sc.avg_edge = statistics.mean(edges) if edges else 0.0
            # Simple edge decay: last 5 avg edge vs first 5 avg edge
            if len(edges) >= 10:
                early = statistics.mean(edges[:5])
                late = statistics.mean(edges[-5:])
                sc.edge_decay = late - early  # negative = decaying

            # Brier score: calibration quality
            brier_sum = 0.0
            for t in resolved_trades:
                p = t.get("confidence", 0.5)
                correct = 1 if t.get("resolution_correct") else 0
                brier_sum += (p - correct) ** 2
            sc.brier_score = brier_sum / len(resolved_trades) if resolved_trades else 1.0

        # Signal rate
        if signals:
            sc.signal_count = len(signals)
            acted = sum(1 for s in signals if s.get("acted"))
            sc.signal_rate = acted / len(signals) if signals else 0.0

        self._apply_rules(sc)
        self.scorecards[strategy] = sc
        return sc

    def _apply_rules(self, sc: StrategyScorecard):
        """Self-improve rules — decide when to pause/archive."""
        if sc.max_drawdown < -0.25:
            sc.status = "paused"
            sc.pause_reason = f"drawdown {sc.max_drawdown:.1%} > 25%"
        elif sc.win_rate < 0.40 and sc.total_trades >= 20:
            sc.status = "paused"
            sc.pause_reason = f"win_rate {sc.win_rate:.1%} < 40%"
        elif sc.sharpe < 0 and sc.total_trades >= 50:
            sc.status = "archived"
            sc.pause_reason = f"sharpe {sc.sharpe:.2f} < 0 over 50+ trades"
        elif sc.brier_score > 0.30 and sc.total_trades >= 30:
            sc.status = "paused"
            sc.pause_reason = f"brier {sc.brier_score:.3f} > 0.30 — poor calibration"
        elif sc.signal_rate < 0.005 and sc.total_trades >= 100:
            sc.status = "archived"
            sc.pause_reason = f"signal_rate {sc.signal_rate:.3f} < 0.5%"
        else:
            sc.status = "active"
            sc.pause_reason = ""

    def get_best_strategy(self) -> Optional[StrategyScorecard]:
        """Return strategy with highest risk-adjusted return."""
        active = [sc for sc in self.scorecards.values() if sc.status == "active"]
        if not active:
            return None
        return max(active, key=lambda sc: sc.sharpe if sc.sharpe > 0 else sc.sharpe - 10)

    def rank_strategies(self) -> list[StrategyScorecard]:
        """Rank all strategies by composite score."""
        ranked = sorted(
            self.scorecards.values(),
            key=lambda sc: (
                sc.sharpe if sc.sharpe > 0 else sc.sharpe - 10,
                sc.win_rate,
                -sc.max_drawdown,
                -sc.brier_score,
            ),
            reverse=True,
        )
        return ranked

    def summary(self) -> str:
        """ASCII summary report."""
        lines = []
        lines.append("=" * 70)
        lines.append(f"{'STRATEGY PERFORMANCE SUMMARY':^70}")
        lines.append("=" * 70)
        ranked = self.rank_strategies()
        header = f"{'Strategy':<20} {'Status':<10} {'Trades':<7} {'WinRate':<8} {'PnL':>9} {'Sharpe':>7} {'MaxDD':>7} {'Brier':>7} {'AvgEdge':>8}"
        lines.append(header)
        lines.append("-" * 70)
        for sc in ranked:
            lines.append(
                f"{sc.strategy:<20} "
                f"{sc.status:<10} "
                f"{sc.total_trades:<7} "
                f"{sc.win_rate:>7.1%} "
                f"${sc.total_pnl:>8.2f} "
                f"{sc.sharpe:>7.2f} "
                f"{sc.max_drawdown:>7.1%} "
                f"{sc.brier_score:>7.3f} "
                f"{sc.avg_edge:>8.3f}"
            )
        best = self.get_best_strategy()
        if best:
            lines.append("-" * 70)
            lines.append(f"  BEST: {best.strategy} (sharpe={best.sharpe:.2f}, pnl=${best.total_pnl:.2f})")
        lines.append("=" * 70)
        return "\n".join(lines)

    def per_strategy_report(self, strategy: str) -> str:
        """Detailed report for one strategy."""
        sc = self.scorecards.get(strategy)
        if not sc:
            return f"No data for strategy: {strategy}"
        lines = []
        lines.append(f"\n{'─' * 60}")
        lines.append(f"  Strategy: {strategy}")
        lines.append(f"{'─' * 60}")
        lines.append(f"  Status:   {sc.status} {sc.pause_reason}")
        lines.append(f"  Trades:   {sc.total_trades} | {sc.wins}W / {sc.losses}L")
        lines.append(f"  Win rate: {sc.win_rate:.1%}")
        lines.append(f"  PnL:      ${sc.total_pnl:+.4f}")
        lines.append(f"  ROI:      {sc.roi:+.2%}")
        lines.append(f"  Sharpe:   {sc.sharpe:.2f}")
        lines.append(f"  Max DD:   {sc.max_drawdown:+.1%}")
        lines.append(f"  Brier:    {sc.brier_score:.3f} {'✓' if sc.brier_score < 0.25 else '✗'}")
        lines.append(f"  Avg Edge: {sc.avg_edge:.4f}")
        lines.append(f"  Signal rate: {sc.signal_rate:.3f}")
        lines.append(f"  Edge decay: {sc.edge_decay:+.4f}")
        return "\n".join(lines)
