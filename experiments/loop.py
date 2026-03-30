"""Autoresearch experiment loop — Karpathy-style ratchet for Polymarket strategies."""

import time, logging, json, subprocess, os
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional

from core.paper_trader import PaperTrader
from core.portfolio import _ensure_db, persist_trade, persist_signal, persist_snapshot
from core.evaluator import Evaluator
from core.config import (
    StrategyConfig, ArbScannerConfig, CryptoThresholdConfig,
    CalibrationBiasConfig, NewsAgentConfig, WhaleFollowConfig, FlashCrashConfig,
    override_from_env,
)
from strategies.arb_scanner import ArbScanner
from strategies.crypto_threshold import CryptoThreshold
from strategies.calibration_bias import CalibrationBias
from strategies.news_agent import NewsAgent
from strategies.whale_follow import WhaleFollow
from strategies.flash_crash import FlashCrash

log = logging.getLogger(__name__)

RESULTS_TSV = Path("experiments/results.tsv")
STATE_JSON = Path("experiments/state.json")


def _load_state() -> dict:
    if STATE_JSON.exists():
        return json.loads(STATE_JSON.read_text())
    return {}


def _save_state(state: dict):
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(state, indent=2))


def _log_result(row: dict):
    """Append one experiment result to results.tsv."""
    RESULTS_TSV.parent.mkdir(parents=True, exist_ok=True)
    header = "date\tstrategy\trounds\ttrades\twins\tlosses\tnet_pnl\tsharpe\tmax_dd\tcalibration\tavg_edge\tsignal_rate\tnote"
    line = "\t".join(str(row.get(k, "")) for k in [
        "date", "strategy", "rounds", "trades", "wins", "losses",
        "net_pnl", "sharpe", "max_dd", "calibration", "avg_edge",
        "signal_rate", "note"
    ])
    if not RESULTS_TSV.exists():
        RESULTS_TSV.write_text(header + "\n")
    RESULTS_TSV.open("a").write(line + "\n")


@dataclass
class ExperimentState:
    """State for one experiment round."""
    round_num: int = 0
    best_score: float = float("-inf")
    best_strategy: str = ""
    total_signals: int = 0
    total_trades: int = 0
    round_start: float = 0.0


class AutoresearchLoop:
    """
    The Karpathy Loop, adapted for Polymarket paper trading:

    1. Read program.md for current priorities
    2. Run each active strategy scan cycle
    3. Execute signals on paper trader
    4. Score results against baseline
    5. If improved: commit; if not: revert
    6. Log to results.tsv
    7. Self-improve: reduce allocation to underperforming strategies
    8. Repeat

    Key difference from Karpathy: our "metric to beat" is composite:
    (sharpe * 0.4 + win_rate * 0.3 + calibration * 0.3)
    """

    def __init__(self, cfg: StrategyConfig, cycle_minutes: int = 15,
                 dry: bool = False):
        self.cfg = cfg
        self.cycle_minutes = cycle_minutes
        self.dry = dry
        self.trader = PaperTrader(cfg)
        self.evaluator = Evaluator()
        self.conn = _ensure_db()

        # Build strategies
        self.strategies = {
            "arb_scanner": ArbScanner(self.trader, self._arb_cfg()),
            "crypto_threshold": CryptoThreshold(self.trader, self._ct_cfg()),
            "calibration_bias": CalibrationBias(self.trader, self._cb_cfg()),
            "news_agent": NewsAgent(self.trader, self._na_cfg()),
            "whale_follow": WhaleFollow(self.trader, self._wf_cfg()),
            "flash_crash": FlashCrash(self.trader, self._fc_cfg()),
        }

        self.state = _load_state()
        self.exp_state = ExperimentState()

    # Config accessors
    def _arb_cfg(self) -> ArbScannerConfig:
        return ArbScannerConfig(
            enabled=self.strategies["arb_scanner"].enabled,
            min_edge=self.cfg.min_edge,
            min_volume=self.cfg.min_volume,
        )

    def _ct_cfg(self) -> CryptoThresholdConfig:
        return CryptoThresholdConfig(
            enabled=self.strategies["crypto_threshold"].enabled,
            min_edge=self.cfg.min_edge,
            min_volume=self.cfg.min_volume,
        )

    def _cb_cfg(self) -> CalibrationBiasConfig:
        return CalibrationBiasConfig(
            enabled=self.strategies["calibration_bias"].enabled,
            min_bias_edge=0.05,
        )

    def _na_cfg(self) -> NewsAgentConfig:
        return NewsAgentConfig(
            enabled=self.strategies["news_agent"].enabled,
            min_price_move=0.05,
            min_volume=25_000.0,
        )

    def _wf_cfg(self) -> WhaleFollowConfig:
        return WhaleFollowConfig(
            enabled=self.strategies["whale_follow"].enabled,
            min_follow_size=100.0,
            min_repeat_count=3,
        )

    def _fc_cfg(self) -> FlashCrashConfig:
        return FlashCrashConfig(
            enabled=self.strategies["flash_crash"].enabled,
            drop_threshold=0.30,
        )

    def _composite_score(self) -> float:
        """Primary metric to beat — composite of sharpe, win_rate, calibration."""
        stats = self.trader.stats
        sharpe = stats.get("sharpe", 0)
        wr = stats.get("win_rate", 0)
        # Use a proxy calibration from evaluator
        best = self.evaluator.get_best_strategy()
        brier = best.brier_score if best else 0.25
        cal_score = max(0, 0.25 - brier) / 0.25  # normalize: 0.25 → 0, 0 → 1

        return sharpe * 0.4 + wr * 0.3 + cal_score * 0.3

    def run_cycle(self) -> dict:
        """Run one full scan-execute-score cycle."""
        self.exp_state.round_start = time.time()
        self.exp_state.round_num += 1
        cycle_results = []

        for name, strat in self.strategies.items():
            if not strat.enabled:
                continue
            if self.evaluator.scorecards.get(name, None):
                sc = self.evaluator.scorecards[name]
                if sc.status in ("paused", "archived"):
                    log.info("Skipping %s (status=%s)", name, sc.status)
                    continue

            try:
                result = strat.scan()
            except Exception as e:
                log.error("[%s] scan error: %s", name, e)
                continue

            # Persist signals
            for sig in result.signals:
                persist_signal(self.conn, {
                    "strategy": name,
                    "market_slug": sig.market_slug,
                    "signal_type": sig.signal_type,
                    "price": sig.price,
                    "size": sig.size,
                    "edge": sig.edge,
                    "confidence": sig.confidence,
                    "acted": True,
                    "timestamp": time.time(),
                })

            # Execute signals
            try:
                filled = strat.execute_signals(result.signals)
                self.exp_state.total_signals += len(result.signals)
                self.exp_state.total_trades += len(filled)
            except Exception as e:
                log.error("[%s] execute error: %s", name, e)

            cycle_results.append({
                "strategy": name,
                "signals": len(result.signals),
                "filled": len(filled),
                "scan_ms": result.scan_duration_ms,
                "error": result.error,
            })
            log.info("[%s] scan: %d signals, %d filled, %.1fms",
                     name, len(result.signals), len(filled), result.scan_duration_ms)

        # Score each strategy
        for name, strat in self.strategies.items():
            if not strat.enabled:
                continue
            sc = self.evaluator.scorecards.get(name)
            if sc:
                self.evaluator.score(name, self.trader.stats,
                                     signals=[],
                                     resolved_trades=self.trader.trade_history)

        # Persist snapshot
        stats = self.trader.stats
        persist_snapshot(self.conn, stats)

        round_score = self._composite_score()
        prev_best = self.state.get("best_composite_score", float("-inf"))

        # Karpathy ratchet: keep if improved, revert if not
        improved = round_score > prev_best
        if improved:
            self.state["best_composite_score"] = round_score
            self.state["best_strategy"] = self.evaluator.get_best_strategy().strategy \
                if self.evaluator.get_best_strategy() else ""
            log.info("*** NEW BEST SCORE: %.4f (was %.4f) ***", round_score, prev_best)

        # Log result
        best = self.evaluator.get_best_strategy()
        _log_result({
            "date": datetime.now(timezone.utc).isoformat(),
            "strategy": best.strategy if best else "none",
            "rounds": self.exp_state.round_num,
            "trades": stats.get("total_trades", 0),
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "net_pnl": stats.get("total_pnl", 0),
            "sharpe": stats.get("sharpe", 0),
            "max_dd": stats.get("max_drawdown", 0),
            "calibration": best.brier_score if best else 0,
            "avg_edge": best.avg_edge if best else 0,
            "signal_rate": best.signal_rate if best else 0,
            "note": f"improved={improved}",
        })

        _save_state(self.state)

        cycle_time = time.time() - self.exp_state.round_start
        return {
            "round": self.exp_state.round_num,
            "score": round_score,
            "improved": improved,
            "best_strategy": self.state.get("best_strategy", ""),
            "stats": stats,
            "cycle_results": cycle_results,
            "cycle_time_s": round(cycle_time, 1),
        }

    def resolve_closed_markets(self):
        """Check for resolved markets and auto-close positions."""
        from core.market_data import list_markets
        try:
            closed = list_markets(limit=200, active=False, closed=True, order="volume")
        except Exception as e:
            log.warning("resolve check failed: %s", e)
            return

        resolved_map = {}
        for mkt in closed:
            slug = mkt.get("slug")
            if not slug:
                continue
            # Determine winning outcome
            resolved = mkt.get("resolved")
            if resolved:
                # Check market outcomes
                outcomes = mkt.get("outcomes") or []
                prices = mkt.get("outcomePrices") or []
                if outcomes and prices:
                    # Winner is the one with payout (price near 1.0)
                    try:
                        winning_idx = max(range(len(prices)),
                                         key=lambda i: float(prices[i]) if prices[i] else 0)
                        resolved_map[slug] = outcomes[winning_idx]
                    except Exception:
                        pass

        if resolved_map:
            self.trader.auto_resolve_closed_markets(resolved_map)
            log.info("Resolved %d closed markets", len(resolved_map))

    def run(self, cycles: int = None, interval_seconds: int = 300):
        """Main loop — run indefinitely or for N cycles."""
        log.info("Starting autoresearch loop (cycles=%s, interval=%ds)", cycles, interval_seconds)
        cycle_count = 0

        while True:
            try:
                self.resolve_closed_markets()
                result = self.run_cycle()

                cycle_count += 1
                if cycles and cycle_count >= cycles:
                    log.info("Completed %d cycles, exiting.", cycles)
                    break

                log.info("Round %d complete: score=%.4f improved=%s best=%s | next in %ds",
                         result["round"], result["score"], result["improved"],
                         result["best_strategy"], interval_seconds)
                time.sleep(interval_seconds)

            except KeyboardInterrupt:
                log.info("Interrupted. Final stats: %s", self.trader.stats)
                break
            except Exception as e:
                log.error("Loop error: %s", e)
                time.sleep(60)
