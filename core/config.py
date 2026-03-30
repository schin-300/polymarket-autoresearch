"""Strategy config — YAML + env var overrides."""

import os, yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class StrategyConfig:
    # Paper trading
    paper_bankroll: float = 10_000.0
    slippage_buy: float = 0.005   # +0.5% for buys
    slippage_sell: float = 0.005 # -0.5% for sells
    tick_size: float = 0.001

    # Signal thresholds
    min_edge: float = 0.02        # min edge per trade (2%)
    min_volume: float = 10_000.0  # min market volume
    min_confidence: float = 0.70  # min confidence score

    # Risk
    max_drawdown: float = 0.25   # pause strategy if dd > 25%
    max_positions: int = 5

    # Scanning
    scan_interval_seconds: int = 300    # full universe scan
    deep_scan_interval: int = 30       # active market deep scan
    fast_scan_interval: int = 5         # flash crash / orderbook

    # Arb scanner
    arb_min_edge: float = 0.01
    arb_min_volume: float = 10_000.0
    arb_check_neg_risk: bool = True
    arb_check_complete_set: bool = True

    # Crypto threshold
    crypto_min_edge: float = 0.05
    crypto_min_volume: float = 50_000.0

    # Calibration bias
    cal_min_bias_edge: float = 0.05

    # News agent
    news_min_price_move: float = 0.05
    news_min_volume: float = 25_000.0

    # Whale follow
    wf_min_follow_size: float = 100.0
    wf_min_repeat_count: int = 3
    wf_leaderboard_limit: int = 20
    wf_period: str = "MONTH"
    wf_category: str = "OVERALL"

    # Flash crash
    flash_drop_threshold: float = 0.30


@dataclass
class ArbScannerConfig(StrategyConfig):
    enabled: bool = True


@dataclass
class CryptoThresholdConfig(StrategyConfig):
    enabled: bool = True
    assets: list = field(default_factory=lambda: ["BTC", "ETH", "SOL", "XRP"])


@dataclass
class CalibrationBiasConfig(StrategyConfig):
    enabled: bool = True
    domains: list = field(default_factory=lambda: ["politics", "sports", "crypto"])
    min_bias_edge: float = 0.05
    favorite_range: tuple = (0.60, 0.95)
    horizon_weight_days: float = 7.0


@dataclass
class NewsAgentConfig(StrategyConfig):
    enabled: bool = True
    min_price_move: float = 0.05
    min_volume: float = 25_000.0
    mean_reversion_threshold: float = 0.10


@dataclass
class WhaleFollowConfig(StrategyConfig):
    enabled: bool = True
    min_follow_size: float = 100.0
    min_repeat_count: int = 3
    leaderboard_limit: int = 20
    period: str = "MONTH"
    category: str = "OVERALL"


@dataclass
class FlashCrashConfig(StrategyConfig):
    enabled: bool = True
    drop_threshold: float = 0.30
    lookback_seconds: int = 10
    take_profit: float = 0.10
    stop_loss: float = 0.05
    min_size: float = 5.0
    assets: list = field(default_factory=lambda: ["BTC", "ETH"])


def load(config_path: str = "config.yaml") -> dict:
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def override_from_env(cfg: StrategyConfig, prefix: str = "POLY_") -> StrategyConfig:
    """Override StrategyConfig fields from POLY_* env vars."""
    for field_name in cfg.__dataclass_fields__:
        env_key = f"{prefix}{field_name.upper()}"
        val = os.environ.get(env_key)
        if val is None:
            continue
        ft = cfg.__dataclass_fields__[field_name].type
        if ft == bool:
            val = val.lower() in ("1", "true", "yes")
        elif ft in (int, float):
            val = ft(val)
        setattr(cfg, field_name, val)
    return cfg
