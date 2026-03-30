#!/usr/bin/env python3
"""Run the full autonomous experiment loop."""

import sys, os, time, logging, argparse
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from experiments.loop import AutoresearchLoop
from core.config import StrategyConfig, override_from_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("experiments/autoresearch.log"),
    ],
)
log = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Run Polymarket Autoresearch Loop")
    parser.add_argument("--cycles", type=int, default=None,
                        help="Number of cycles to run (default: infinite)")
    parser.add_argument("--interval", type=int, default=300,
                        help="Seconds between cycles (default: 300)")
    parser.add_argument("--cycle-minutes", type=int, default=15,
                        help="Experiment cycle duration in minutes (default: 15)")
    parser.add_argument("--dry", action="store_true",
                        help="Dry run — scan only, no paper trades")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("Starting Polymarket Autoresearch")
    log.info("Paper bankroll: $10,000 virtual USD")
    log.info("Cycle interval: %ds, experiment cycle: %dm",
             args.interval, args.cycle_minutes)

    cfg = StrategyConfig()
    override_from_env(cfg)

    loop = AutoresearchLoop(
        cfg=cfg,
        cycle_minutes=args.cycle_minutes,
        dry=args.dry,
    )

    try:
        loop.run(cycles=args.cycles, interval_seconds=args.interval)
    except KeyboardInterrupt:
        log.info("Stopped by user.")
        stats = loop.trader.stats
        log.info("Final stats: bankroll=$%.2f pnl=$%.4f trades=%d",
                 stats.get("bankroll"), stats.get("total_pnl"), stats.get("total_trades"))


if __name__ == "__main__":
    main()
