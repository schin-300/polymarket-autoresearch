# PolyMarket Autoresearch — Program.md

## Mission

Find and validate profitable Polymarket paper-trading strategies using autonomous experiment loops.
This is a scientific process: form a hypothesis, test it, measure it, keep what works.

## Current Priority Order

1. **crypto_threshold** — BTC/ETH threshold markets are the most modelable. Focus here first.
2. **arb_scanner** — neg-risk and complete-set arb is mechanical edge, low prediction risk.
3. **calibration_bias** — favorite-longshot exploit. Test on political + sports markets.
4. **flash_crash** — 15m markets have high volume + fast signal turnaround.
5. **whale_follow** — signal layer only, not standalone.

## What We're Optimizing

**Primary metric:** net_pnl + calibration_combined
**Secondary:** sharpe_ratio > 1.0, max_drawdown < 20%, win_rate > 52%

## Strategy Constraints

- All trades are PAPER ONLY. Never import wallet keys.
- Minimum edge per trade: 0.02 (2%)
- Minimum market volume: $10,000
- Minimum signal confidence: 70%
- Max one open position per strategy per market
- Stop paper-trading a strategy if: drawdown > 25% OR win_rate < 40% over 20 trades

## Scanning Schedule

- Full universe scan: every 5 minutes
- Active market deep-scan (crypto threshold): every 30 seconds
- Whale follower: every 60 seconds
- Flash crash: every 5 seconds (orderbook monitoring)

## Self-Improve Directives

- If a strategy scores sharpe < 0 for 50+ trades: archive it, propose a variant
- If signal_rate < 0.5% for 100+ scans: disable strategy, log why
- If edge_per_trade is declining 3 consecutive periods: adjust signal thresholds
- If a strategy has sharpe > 2.0 consistently: increase its scan frequency and allocation
- Favor strategies that work on small bankroll ($10k paper) because they scale

## Experiment Log Format (results.tsv)

```
date\tstrategy\trounds\trades\twins\tlosses\tnet_pnl\tsharpe\tmax_dd\tcalibration\tavg_edge\tsignal_rate\tnote
```

## What to Try First

1. BTC 15m up/down markets: complete-set arb (bid_up + bid_dn < 1.0)
2. Weekly BTC threshold markets: compare market price vs model fair value
3. Political neg-risk events: test if favorite is underpriced vs implied odds
4. High-volume sports markets near expiry: short-term momentum after large trades

## NEVER STOP

Once the experiment loop has begun, do NOT pause to ask if you should continue.
Just keep scanning, scoring, and improving. Wake up to a log of experiments.
