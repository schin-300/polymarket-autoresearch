"""
Terminal TUI for polymarket-autoresearch.
ASCII-art dashboard — no web, no browser, just your terminal.
"""

import sys, time, logging, threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.panel import Panel
    from rich.layout import Layout
    from rich.text import Text
    from rich.tree import Tree
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from core.paper_trader import PaperTrader
from core.evaluator import Evaluator
from core.portfolio import _ensure_db, get_performance_by_strategy

console = Console() if RICH_AVAILABLE else None


# ── ASCII Art helpers ──────────────────────────────────────────────────────────

LOGO = r"""
  ██████╗  ██████╗ ██████╗ ████████╗ ██████╗  ██████╗ █████╗
  ██╔══██╗██╔═══██╗██╔══██╗╚══██╔══╝██╔═══██╗██╔════╝██╔══██╗
  ██████╔╝██║   ██║██████╔╝   ██║   ██║   ██║██║     ███████║
  ██╔═══╝ ██║   ██║██╔══██╗   ██║   ██║   ██║██║     ██╔══██║
  ██║     ╚██████╔╝██║  ██║   ██║   ╚██████╔╝╚██████╗██║  ██║
  ╚═╝      ╚═════╝ ╚═╝  ╚═╝   ╚═╝    ╚═════╝  ╚═════╝╚═╝  ╚═╝
         Autoresearch — Polymarket Paper Trading Engine
"""


def border(text: str, char: str = "═", width: int = 78) -> str:
    lines = text.strip().split("\n")
    out = []
    out.append("╔" + char * width + "╗")
    for line in lines:
        padded = line[:width]
        out.append("║" + padded.ljust(width) + "║")
    out.append("╚" + char * width + "╝")
    return "\n".join(out)


def panel(title: str, content: str, width: int = 78) -> str:
    lines = content.strip().split("\n")
    out = []
    title_bar = f"╡ {title} ╞"
    out.append("┌" + "─" * (width - 2) + "┐")
    out.append("│" + title_bar.ljust(width - 2) + "│")
    for line in lines:
        padded = line[:width - 2]
        out.append("│" + padded.ljust(width - 2) + "│")
    out.append("└" + "─" * (width - 2) + "┘")
    return "\n".join(out)


def pnl_bar(pnl: float, width: int = 20) -> str:
    """ASCII bar showing PnL."""
    if pnl >= 0:
        filled = int(min(pnl / 100, 1.0) * width)
        return "│" + "█" * filled + "░" * (width - filled) + f"│ +${pnl:.2f}"
    else:
        filled = int(min(abs(pnl) / 100, 1.0) * width)
        return "│" + "▓" * filled + "░" * (width - filled) + f"│ ${pnl:.2f}"


def winrate_bar(wins: int, losses: int, width: int = 20) -> str:
    total = wins + losses
    if total == 0:
        return "│" + "░" * width + "│ 0 trades"
    wr = wins / total
    filled = int(wr * width)
    return "│" + "█" * filled + "░" * (width - filled) + f"│ {wr:.0%} ({wins}W/{losses}L)"


def status_dot(active: bool) -> str:
    return "●" if active else "○"


def strategy_row(name: str, sc, width: int = 78) -> list[str]:
    """Generate status lines for one strategy."""
    status_icon = status_dot(sc.status == "active")
    status_str = f"{status_icon} {name.upper()}"
    if sc.status != "active":
        status_str += f" [{sc.status}: {sc.pause_reason[:30]}]"

    pnl_str = f"${sc.total_pnl:+.4f}"
    wr_str = f"{sc.win_rate:.1%}" if sc.total_trades > 0 else "—"
    sharpe_str = f"{sc.sharpe:.2f}" if sc.total_trades > 0 else "—"
    edge_str = f"{sc.avg_edge:.3f}" if sc.avg_edge != 0 else "—"
    brier_str = f"{sc.brier_score:.3f}" if sc.total_trades > 0 else "—"

    return [
        status_str,
        f"  trades={sc.total_trades:4d}  pnl={pnl_str:>10}  wr={wr_str:>6}  "
        f"sharpe={sharpe_str:>6}  edge={edge_str:>7}  brier={brier_str:>7}",
    ]


def fmt_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def render_dashboard(
    trader: PaperTrader,
    evaluator: Evaluator,
    cycle_num: int = 0,
    cycle_score: float = 0.0,
    best_strategy: str = "",
    last_cycle_s: float = 0.0,
) -> str:
    """Render full ASCII dashboard."""
    stats = trader.stats
    ranked = evaluator.rank_strategies()

    # Header
    lines = []
    W = 78

    # ── Banner ──────────────────────────────────────────────────────────────
    logo_lines = LOGO.strip().split("\n")
    for ll in logo_lines[:3]:
        lines.append(ll[:W])
    lines.append("")
    lines.append(f"  {fmt_timestamp()}   cycle #{cycle_num}   score={cycle_score:.4f}   "
                 f"best={best_strategy}   last_cycle={last_cycle_s:.1f}s")

    # ── Portfolio Panel ──────────────────────────────────────────────────────
    lines.append("")
    bankroll = stats.get("bankroll", 10000)
    pnl = bankroll - 10000
    roi = stats.get("roi", 0)
    total_trades = stats.get("total_trades", 0)
    wins = stats.get("wins", 0)
    losses = stats.get("losses", 0)
    sharpe = stats.get("sharpe", 0)
    max_dd = stats.get("max_drawdown", 0)
    open_pos = stats.get("open_positions", 0)

    panel_lines = [
        "",
        f"  {'PORTFOLIO':<20} {'BANKROLL':>15} {'ROI':>10} {'SHARPE':>8} {'MAX DD':>8}",
        f"  {'='*58}",
        f"  {'Portfolio':.<20} ${bankroll:>14,.2f} {roi:>+9.2%} {sharpe:>+7.2f} {max_dd:>+7.1%}",
        "",
    ]

    # PnL bar
    pnl_viz = pnl_bar(pnl)
    panel_lines.append(f"  PnL: {pnl_viz}")
    panel_lines.append(f"  Win Rate: {winrate_bar(wins, losses)}")
    panel_lines.append(f"  Open Positions: {open_pos}   Total Trades: {total_trades}")
    panel_lines.append(f"  Unrealized PnL: ${stats.get('unrealized_pnl', 0):+.4f}")
    panel_lines.append("")

    # Strategy table
    panel_lines.append(f"  {'STRATEGIES':<30} {'STATUS':<10} {'TRADES':>7} {'WINRATE':>8} "
                       f"{'PNL':>10} {'SHARPE':>7}")
    panel_lines.append(f"  {'-'*72}")

    for sc in ranked:
        status_icon = status_dot(sc.status == "active")
        wr = f"{sc.win_rate:.1%}" if sc.total_trades > 0 else "  —  "
        pnl_s = f"${sc.total_pnl:+.4f}"
        shp = f"{sc.sharpe:.2f}" if sc.total_trades > 0 else "  —  "
        panel_lines.append(
            f"  {status_icon} {sc.strategy:<28} {sc.status:<10} "
            f"{sc.total_trades:>7} {wr:>8} {pnl_s:>10} {shp:>7}"
        )
        if sc.pause_reason:
            panel_lines.append(f"    ↳ {sc.pause_reason}")

    panel_lines.append("")
    panel_str = "\n".join(panel_lines)
    for pl in panel_str.split("\n"):
        lines.append(pl[:W])

    # ── Signal ticker ─────────────────────────────────────────────────────────
    if ranked:
        best = ranked[0]
        lines.append("")
        if best.status == "active":
            lines.append(f"  ★ BEST: {best.strategy.upper()} (sharpe={best.sharpe:.2f}, "
                         f"edge={best.avg_edge:.3f}, brier={best.brier_score:.3f})")
        else:
            lines.append(f"  ○ No active best strategy")

    lines.append("")
    lines.append(f"  Paper Mode — No Real Orders — $10,000 Virtual Bankroll")
    lines.append("")

    return "\n".join(lines)


class Dashboard:
    """Live dashboard runner."""

    def __init__(self, trader: PaperTrader, evaluator: Evaluator):
        self.trader = trader
        self.evaluator = evaluator
        self.cycle_num = 0
        self.cycle_score = 0.0
        self.best_strategy = ""
        self.last_cycle_s = 0.0
        self._running = False
        self._lock = threading.Lock()

    def update(self, cycle_num=None, cycle_score=None, best_strategy=None,
               last_cycle_s=None):
        with self._lock:
            if cycle_num is not None:
                self.cycle_num = cycle_num
            if cycle_score is not None:
                self.cycle_score = cycle_score
            if best_strategy is not None:
                self.best_strategy = best_strategy
            if last_cycle_s is not None:
                self.last_cycle_s = last_cycle_s

    def refresh(self):
        """Print one dashboard frame."""
        with self._lock:
            txt = render_dashboard(
                self.trader,
                self.evaluator,
                self.cycle_num,
                self.cycle_score,
                self.best_strategy,
                self.last_cycle_s,
            )
        if RICH_AVAILABLE and console:
            console.clear()
            console.print(txt)
        else:
            # Plain print fallback
            print("\033[2J\033[H")  # clear screen
            print(txt)
            sys.stdout.flush()

    def start(self, interval: float = 5.0):
        """Start live refresh loop in a thread."""
        self._running = True

        def loop():
            while self._running:
                self.refresh()
                time.sleep(interval)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
        return t

    def stop(self):
        self._running = False


# ── Rich layout version ────────────────────────────────────────────────────────

def build_rich_layout(trader: PaperTrader, evaluator: Evaluator,
                      cycle_num: int = 0) -> Layout:
    """Build a rich Layout for a prettier live view."""
    layout = Layout(name="root")

    stats = trader.stats

    # Header panel
    header = Panel(
        f"[bold cyan]polymarket-autoresearch[/]  cycle #{cycle_num}  "
        f"{fmt_timestamp()}\n"
        f"[yellow]PAPER MODE — No Real Orders[/]",
        title="Status",
    )

    # Stats
    stats_table = Table(title="Portfolio", show_header=True)
    stats_table.add_column("Metric", style="cyan")
    stats_table.add_column("Value", style="green", justify="right")
    s = stats
    stats_table.add_row("Bankroll", f"${s.get('bankroll', 10000):,.2f}")
    stats_table.add_row("ROI", f"{s.get('roi', 0):+.2%}")
    stats_table.add_row("Sharpe", f"{s.get('sharpe', 0):+.2f}")
    stats_table.add_row("Max DD", f"{s.get('max_drawdown', 0):+.1%}")
    stats_table.add_row("Trades", f"{s.get('total_trades', 0)} ({s.get('wins',0)}W/{s.get('losses',0)}L)")
    stats_table.add_row("Win Rate", f"{s.get('win_rate', 0):.1%}")
    stats_table.add_row("Unrealized", f"${s.get('unrealized_pnl', 0):+.4f}")

    # Strategy table
    strat_table = Table(title="Strategies", show_header=True)
    strat_table.add_column("#", justify="right")
    strat_table.add_column("Strategy")
    strat_table.add_column("Status")
    strat_table.add_column("Trades", justify="right")
    strat_table.add_column("WinRate", justify="right")
    strat_table.add_column("PnL", justify="right")
    strat_table.add_column("Sharpe", justify="right")
    strat_table.add_column("Brier", justify="right")

    for i, sc in enumerate(evaluator.rank_strategies(), 1):
        icon = "●" if sc.status == "active" else "○"
        strat_table.add_row(
            str(i),
            f"{icon} {sc.strategy}",
            sc.status,
            str(sc.total_trades),
            f"{sc.win_rate:.1%}" if sc.total_trades > 0 else "—",
            f"${sc.total_pnl:+.4f}",
            f"{sc.sharpe:.2f}" if sc.total_trades > 0 else "—",
            f"{sc.brier_score:.3f}" if sc.total_trades > 0 else "—",
        )

    layout.split_column(
        Layout(header, name="header", size=3),
        Layout(stats_table, name="stats"),
        Layout(strat_table, name="strategies"),
    )
    return layout


if __name__ == "__main__":
    # Smoke test
    from core.paper_trader import PaperTrader
    from core.config import StrategyConfig
    from core.evaluator import Evaluator

    cfg = StrategyConfig()
    trader = PaperTrader(cfg)
    evaluator = Evaluator()

    txt = render_dashboard(trader, evaluator, cycle_num=0)
    print(txt)
