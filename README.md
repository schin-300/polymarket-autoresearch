# PolyMarket Autoresearch

Autonomous experiment loop for Polymarket paper trading — inspired by Karpathy's
[autoresearch](https://github.com/karpathy/autoresearch) pattern, adapted for
prediction markets.

**Paper trading only. No real wallet. No real orders.**

## Quick Start

```bash
git clone https://github.com/YOUR_HANDLE/polymarket-autoresearch.git
cd polymarket-autoresearch
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Run the experiment loop
python scripts/run_autonomous.py --cycles 5 --interval 60

# Or run the live dashboard
python scripts/dashboard.py
```

## What It Does

```
┌─ Karpathy Loop ─────────────────────────────────────────────┐
│  1. Scan Polymarket for opportunities                     │
│  2. Generate signals from 5 strategies                      │
│  3. Execute paper trades (virtual $10k bankroll)           │
│  4. Score each strategy                                    │
│  5. Keep what works, discard what doesn't                  │
│  6. Auto-improve: pause/disable underperformers            │
│  7. Log to results.tsv, repeat                            │
└────────────────────────────────────────────────────────────┘
```

## Strategies

| Strategy | Edge Type | Paper Trading |
|---|---|---|
| `arb_scanner` | Neg-risk + complete-set mispricing | Yes |
| `crypto_threshold` | BTC/ETH/SOL fair-value vs market price | Yes |
| `calibration_bias` | Favorite-longshot exploit | Yes |
| `news_agent` | Price movement + mean-reversion | Yes |
| `whale_follow` | Public wallet signal layer | Signal only |
| `flash_crash` | Orderbook crash detection | Needs WS |

## Architecture

```
polymarket-autoresearch/
├── SPEC.md                   # Full system specification
├── program.md                # Strategy instructions (editable — the agent prompt)
├── core/
│   ├── market_data.py        # Polymarket API: read-only
│   ├── paper_trader.py       # Virtual execution engine
│   ├── portfolio.py          # SQLite persistence
│   ├── evaluator.py          # Scorecards, calibration, reports
│   └── config.py            # YAML + env var config
├── strategies/
│   ├── base.py              # Abstract strategy interface
│   ├── arb_scanner.py       # Neg-risk + complete-set arb
│   ├── crypto_threshold.py   # Black-Scholes fair value model
│   ├── calibration_bias.py   # Favorite-longshot bias hunter
│   ├── news_agent.py        # Price movement signals
│   ├── whale_follow.py       # Public wallet tracker
│   └── flash_crash.py       # Orderbook crash detection
├── experiments/
│   ├── loop.py              # Karpathy-style ratchet loop
│   └── self_improve.py       # Strategy router + performance memory
├── dashboard/
│   └── tui.py               # ASCII art live dashboard
└── scripts/
    ├── run_autonomous.py     # Main entry point
    ├── dashboard.py          # Live TUI
    └── replay.py            # Historical backtesting
```

## Key Patterns

### Karpathy Loop
- `program.md` is the editable instruction file — modify strategy directives here
- `experiments/results.tsv` logs every experiment round (gitignored)
- Ratchet: keep commits that improve composite score, revert those that don't

### Self-Improve Rules
- Sharpe < 0 over 50 trades → archive strategy
- Win rate < 40% over 30 trades → pause
- Drawdown > 25% → pause
- Strategy 3x worse than best over 20 rounds → disable
- All strategies poor → suggest variant with stricter thresholds

### Paper Trading
- Starting bankroll: $10,000 virtual USD
- Simulated fills: mid ± 0.5% slippage
- Resolution: actual Polymarket outcomes checked on each cycle
- No wallet, no private keys, no real transactions ever

## Dashboard Preview

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  PORTFOLIO                      BANKROLL         ROI      SHARPE    MAX DD  ║
║  Portfolio................ $10,000.00        +0.00%      0.00      +0.0%  ║
║
║  PnL: │░░░░░░░░░░░░░░░░░░░░░│ +$0.00
║  Win Rate: │░░░░░░░░░░░░░░░░░░░░░│ 0 trades
║
║  STRATEGIES                      STATUS     TRADES   WINRATE        PNL    SHARPE
║  ● arb_scanner                   active         0      —        $0.0000      —
║  ● crypto_threshold              active         0      —        $0.0000      —
║  ● calibration_bias              active         0      —        $0.0000      —
║  ○ news_agent                    active         0      —        $0.0000      —
║  ○ whale_follow                  active         0      —        $0.0000      —
║  ○ flash_crash                  active         0      —        $0.0000      —
║
║  Paper Mode — No Real Orders — $10,000 Virtual Bankroll
╚══════════════════════════════════════════════════════════════════════════════╝
```

## Evaluation Metrics

| Metric | Description | Target |
|---|---|---|
| `net_pnl` | Sum of realized PnL | > $0 |
| `win_rate` | Wins / total trades | > 52% |
| `sharpe` | Annualized risk-adjusted return | > 1.0 |
| `max_drawdown` | Peak-to-trough | < 20% |
| `brier_score` | Calibration quality (lower=better) | < 0.25 |
| `avg_edge` | Mean edge per trade | > 0.02 |

## Environment Variables

```bash
POLY_PAPER_BANKROLL=10000        # starting paper bankroll
POLY_MIN_EDGE=0.02               # minimum edge per trade
POLY_MIN_VOLUME=10000            # minimum market volume
POLY_MIN_CONFIDENCE=0.70         # minimum signal confidence
POLY_SLIPPAGE_BUY=0.005          # buy slippage (0.5%)
POLY_SLIPPAGE_SELL=0.005        # sell slippage
POLY_SCAN_INTERVAL=300           # full universe scan interval (seconds)
```

## Credits

- Karpathy [autoresearch](https://github.com/karpathy/autoresearch) for the experiment loop pattern
- [polymarket-trading-bot](https://github.com/discountry/polymarket-trading-bot) for flash crash strategy reference
- [polybot](https://github.com/ent0n29/polybot) for complete-set arbitrage strategy spec
- Polymarket public APIs (Gamma, CLOB, Data) — all read-only, no wallet needed
