#!/usr/bin/env python3
"""
Replay historical Polymarket closed markets and simulate paper trades on them.
This lets you backtest strategies against known outcomes.
"""

import sys, json, logging
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.market_data import list_markets, neg_risk_check, get_neg_risk_events
from core.paper_trader import PaperTrader
from core.config import StrategyConfig
from strategies.arb_scanner import ArbScanner
from strategies.calibration_bias import CalibrationBias

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def get_closed_markets(limit: int = 500) -> list[dict]:
    """Fetch resolved/closed markets for backtesting."""
    return list_markets(limit=limit, active=False, closed=True, order="volume")


def replay(strategy_class, trader: PaperTrader, cfg, n_markets: int = 200):
    """Run a strategy against historical closed markets."""
    strat = strategy_class(trader, cfg)
    closed = get_closed_markets(limit=n_markets)

    log.info("Replaying %d closed markets with %s", len(closed), strategy_class.name)

    # For each closed market, simulate a trade based on the signal
    # and resolve based on actual outcome
    for mkt in closed:
        slug = mkt.get("slug", "")
        question = mkt.get("question", "")
        vol = float(mkt.get("volume") or 0)
        if vol < 1000:
            continue

        # Skip unresolved
        resolved = mkt.get("resolved")
        if not resolved:
            continue

        # Get outcomes and prices
        outcomes = mkt.get("outcomes", [])
        prices = mkt.get("outcomePrices", [])

        if not outcomes or not prices:
            continue

        try:
            prices = [float(p) for p in prices]
        except Exception:
            continue

        # Winning outcome
        winning_idx = 0
        try:
            winning_idx = max(range(len(prices)), key=lambda i: prices[i] if prices[i] else 0)
        except Exception:
            pass

        winning_outcome = outcomes[winning_idx]

        # Generate a signal from the strategy
        # (simplified — real replay would cache scan results)
        result = strat.scan()
        for sig in result.signals:
            if sig.market_slug == slug:
                # Execute and resolve
                pos = trader.fill_at_mid(
                    strategy=strat.name,
                    market_slug=slug,
                    token_id=sig.token_id,
                    side=sig.signal_type,
                    price=sig.price,
                    size=sig.size,
                    outcome_label=sig.outcome,
                    edge=sig.edge,
                    confidence=sig.confidence,
                )
                # Resolve immediately (we know the outcome)
                won = (pos.outcome == winning_outcome)
                trader.resolve_position(pos.position_id, won)

    return trader.stats


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Replay paper trades on historical markets")
    parser.add_argument("--markets", type=int, default=200)
    args = parser.parse_args()

    cfg = StrategyConfig()
    trader = PaperTrader(cfg)

    print("\n=== ARB SCANNER REPLAY ===")
    stats1 = replay(ArbScanner, trader, cfg, n_markets=args.markets)
    print(f"Bankroll: ${stats1['bankroll']:.2f}")
    print(f"Total PnL: ${stats1['total_pnl']:.4f}")
    print(f"Trades: {stats1['total_trades']}")

    print("\n=== CALIBRATION BIAS REPLAY ===")
    # Reset trader
    trader2 = PaperTrader(cfg)
    stats2 = replay(CalibrationBias, trader2, cfg, n_markets=args.markets)
    print(f"Bankroll: ${stats2['bankroll']:.2f}")
    print(f"Total PnL: ${stats2['total_pnl']:.4f}")
    print(f"Trades: {stats2['total_trades']}")


if __name__ == "__main__":
    main()
