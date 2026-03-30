# CLAUDE.md — polymarket-autoresearch

## Project Overview

Autonomous experiment loop for Polymarket paper trading. Inspired by Karpathy's
autoresearch pattern. Paper trading only — no real wallet, no real orders.

## Key Commands

```bash
# Activate venv
source .venv/bin/activate

# Run experiment loop (5 cycles, 60s apart)
python scripts/run_autonomous.py --cycles 5 --interval 60

# Run live dashboard
python scripts/dashboard.py --refresh 5

# Backtest on historical resolved markets
python scripts/replay.py --markets 200
```

## Architecture

- `core/market_data.py` — Polymarket API (read-only)
- `core/paper_trader.py` — Virtual execution engine ($10k paper bankroll)
- `core/evaluator.py` — Scorecards, calibration tracking, reports
- `strategies/*.py` — 6 trading strategies
- `experiments/loop.py` — Karpathy-style ratchet loop
- `experiments/self_improve.py` — Strategy router + performance memory
- `dashboard/tui.py` — ASCII art live dashboard

## Strategy Priorities

1. `arb_scanner` — structural edge, low prediction risk (start here)
2. `crypto_threshold` — modelable BTC/ETH/SOL threshold markets
3. `calibration_bias` — favorite-longshot exploit
4. `news_agent` — price movement + mean-reversion
5. `whale_follow` — signal layer only
6. `flash_crash` — needs WebSocket, harder to paper-trade

## Key Files

- `SPEC.md` — full system specification (read this first)
- `program.md` — editable strategy instructions for the loop
- `experiments/results.tsv` — experiment log (DO NOT commit)
- `experiments/state.json` — current loop state

## Self-Improve Rules

- Sharpe < 0 over 50 trades → archive
- Win rate < 40% over 30 trades → pause
- Drawdown > 25% → pause
- Strategy 3x worse than best over 20 rounds → disable
- All strategies poor → generate variant suggestions

## Primary Metric

Composite: `(sharpe * 0.4) + (win_rate * 0.3) + (calibration * 0.3)`
Lower is better for brier. Target: > 0.5 composite score.

## Paper Trading Rules

- Starting bankroll: $10,000 virtual USD
- Slippage: +0.5% buys, -0.5% sells
- Resolution: actual Polymarket outcomes checked each cycle
- No wallet, no private keys, no real transactions ever

## Adding a New Strategy

1. Create `strategies/mystrategy.py` extending `BaseStrategy`
2. Implement `scan() -> StrategyResult` returning `Signal` list
3. Implement `reset()` for per-session state
4. Register in `experiments/loop.py.__init__`
5. Add config in `core/config.py`
6. Update `SPEC.md` and `program.md`
