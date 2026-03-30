#!/usr/bin/env python3
"""Live TUI dashboard — run alongside the loop or standalone."""

import sys, time, argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.paper_trader import PaperTrader
from core.config import StrategyConfig
from core.evaluator import Evaluator
from dashboard.tui import Dashboard, render_dashboard, RICH_AVAILABLE, console


def main():
    parser = argparse.ArgumentParser(description="Polymarket Autoresearch Dashboard")
    parser.add_argument("--refresh", type=float, default=5.0,
                        help="Refresh interval in seconds (default: 5)")
    args = parser.parse_args()

    cfg = StrategyConfig()
    trader = PaperTrader(cfg)
    evaluator = Evaluator()

    # Demo mode: show what it looks like with sample data
    # In real use, you'd pass the shared trader/evaluator instances
    print("\033[2J\033[H")
    txt = render_dashboard(trader, evaluator, cycle_num=0)
    print(txt)

    print("\n[Dashboard running — Ctrl+C to stop]")
    print("Note: This shows an empty portfolio. Run run_autonomous.py to start trading.\n")

    try:
        while True:
            time.sleep(args.refresh)
            print("\033[2J\033[H")
            txt = render_dashboard(trader, evaluator, cycle_num=0)
            print(txt)
    except KeyboardInterrupt:
        print("\nDashboard stopped.")


if __name__ == "__main__":
    main()
