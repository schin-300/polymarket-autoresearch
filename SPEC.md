# PolyMarket Autoresearch — SPEC

## What It Is

An autonomous experiment loop for Polymarket prediction trading — inspired by Karpathy's
autoresearch but for trading strategies instead of LLM training.

**Core pattern:** try a strategy, measure it, keep what works, discard what doesn't, repeat.

All trading is **paper / fake money**. Real wallet never touches the system.

---

## Architecture

```
polymarket-autoresearch/
├── SPEC.md                          # This file
├── program.md                       # Strategy instructions for the loop (editable)
├── README.md
├── pyproject.toml
│
├── core/
│   ├── market_data.py              # Polymarket API: fetch markets, books, trades, prices
│   ├── paper_trader.py             # Paper execution engine (virtual $10,000 bankroll)
│   ├── portfolio.py                # Portfolio tracker: positions, PnL, history
│   ├── evaluator.py                # Evaluation engine: scorecards, calibration, reports
│   └── config.py                   # Strategy config + threshold overrides
│
├── strategies/
│   ├── base.py                     # Abstract base: run(), signals(), reset()
│   ├── arb_scanner.py              # Neg-risk + related-market mispricing scanner
│   ├── crypto_threshold.py          # BTC/ETH/SOL threshold model (fair-value vs market)
│   ├── calibration_bias.py         # Domain-specific bias: favorite-longshot, horizon effects
│   ├── news_agent.py               # News/event signal → probability delta → trade
│   ├── whale_follow.py             # Public wallet tracker → signal layer
│   └── flash_crash.py              # Short-term orderbook imbalance → momentum signals
│
├── dashboard/
│   └── tui.py                      # Rich/ASCII terminal dashboard (paper trading display)
│
├── experiments/
│   ├── loop.py                     # Autoresearch experiment loop (karpathy-style ratchet)
│   └── self_improve.py             # Strategy router + performance memory
│
├── scripts/
│   ├── run_autonomous.py           # main() — runs the full experiment loop
│   ├── dashboard.py                # Live TUI runner
│   └── replay.py                   # Replay historical paper trades for backtesting
│
└── experiments/
    ├── results.tsv                 # Autoresearch log (never git-tracked live runs)
    └── state.json                  # Strategy state snapshot
```

---

## Strategies

### 1. arb_scanner (Arbitrage Scanner)
- Scans neg-risk events: sum of all outcome prices vs 1.0
- Scans related markets for cross-market contradictions
- Scans complete-set cost on up/down pairs
- Threshold: edge >= 1% → signal
- Paper action: simulate buy all legs

### 2. crypto_threshold (Crypto Threshold Model)
- BTC/ETH/SOL threshold markets (weekly, daily, 15m)
- Fair value from: spot reference, realized vol, time-to-expiry
- Compare model vs market price → edge signal
- Threshold: edge >= 5% + min vol

### 3. calibration_bias (Calibration Bias Hunter)
- Exploits favorite-longshot: buy outcomes near 70-90% when market underprices them
- Horizon effect: short-dated markets are better calibrated
- Domain-specific: political underconfidence, crypto calibration patterns
- Threshold: recalibrated_prob - market_price >= 0.05

### 4. news_agent (News Signal Agent)
- Watches Polymarket trending markets
- Detects price movement after news events
- Compares pre-news vs post-news probability
- Trades on mean-reversion or momentum depending on signal type

### 5. whale_follow (Whale Follower)
- Tracks top leaderboard wallets publicly
- Detects position accumulation signals
- Only follows on high-conviction setups (size + repetition)
- Signal only, not a standalone strategy

### 6. flash_crash (Flash Crash)
- 15m/1h BTC/ETH/SOL up-down markets
- Detects rapid probability drop in orderbook
- Buys the crashed side, exits on mean-reversion
- Configurable drop threshold (default 0.30)

---

## Evaluation Framework (Scorecard)

Each strategy gets scored on:

| Metric | Formula | Target |
|---|---|---|
| net_pnl | sum of realized PnL | > 0 |
| win_rate | wins / total_trades | > 50% |
| sharpe | mean(rpnl) / std(rpnl) * sqrt(252) | > 1.0 |
| max_dd | peak - trough | < -20% |
| calibration | predicted_prob vs actual outcome | Brier score < 0.25 |
| edge_decay | pnl_trend over time | stable or improving |
| signal_rate | signals / total_scans | > 1% |
| avg_edge | mean(edge_per_trade) | > 0.05 |

---

## Karpathy-Style Autoresearch Loop

Each experiment round:
1. Read program.md for current strategy priorities
2. Check results.tsv for recent history
3. Agent proposes a change to a strategy file
4. Run 15-minute experiment cycle
5. Score results against baseline
6. If improved: keep commit; if not: git reset
7. Log result to results.tsv
8. Auto-router picks best strategy for next scan cycle
9. Self-improve: if strategy X is 3x worse than Y over 20 rounds → reduce allocation

---

## Self-Improve Rules

- If a strategy has drawdown > 20% in a single session → pause it, log reason
- If win_rate < 40% over 30 trades → reduce signal threshold, log change
- If signal_rate < 0.5% over 100 scans → disable strategy, log reason
- If sharpe < 0 over 50 trades → archive strategy, try variant
- If calibration Brier > 0.30 → recalibrate probability model

---

## Data Sources

- Gamma API: markets, events, search (no wallet)
- CLOB API: orderbooks, prices, midpoint (no wallet)
- Data API: leaderboard, trades, holders (no wallet)
- WebSocket: real-time orderbook (polymarket-trading-bot approach)

## Paper Trading Rules

- Starting bankroll: $10,000 virtual USD
- No real orders placed ever
- Simulated fills at: best_bid + 0.001 (for buys) / best_ask - 0.001 (for sells)
- Slippage: +0.5% for buys, -0.5% for sells
- Resolution: use actual Polymarket market outcomes (check closed markets)
- PnL calculated on closed positions only

## Git Strategy

- `main` branch: production-ready strategies only
- `experiments/` branch: live experiment state
- `results.tsv` is gitignored (sensitive live data)
- Each strategy improvement is a separate commit

## Auth/Credentials

- No real wallet ever
- No private keys ever
- If real trading is enabled later: ask user first, explicit opt-in
