"""Calibration bias strategy — exploits favorite-longshot and horizon effects."""

import time, logging
from typing import Optional

from core.market_data import (
    list_markets, get_neg_risk_events, neg_risk_check,
    fmt_vol, fmt_pct, _parse,
)
from strategies.base import BaseStrategy, StrategyResult, Signal

log = logging.getLogger(__name__)


class CalibrationBias(BaseStrategy):
    """
    Exploits systematic biases in crowd probability estimates.

    Key biases:
    1. Favorite-longshot: longshots (low prob events) are overbet → favorites underpriced
    2. Horizon effect: longer-dated markets are less well-calibrated
    3. Domain effects: political markets tend to compress toward 50%

    Strategy:
    - Identify favorite outcomes (market_p in 60-95%) where real probability
      is likely underestimated by the crowd
    - Buy "Yes" on underpriced favorites
    - Sell "No" on overpriced longshots
    """

    name = "calibration_bias"
    enabled = True

    # Favorite-longshot bias parameters
    FAVORITE_MIN = 0.60
    FAVORITE_MAX = 0.95
    LONGSHOT_MIN = 0.05
    LONGSHOT_MAX = 0.30

    # Bias magnitude (conservative estimate of systematic underpricing)
    FAVORITE_UNDERPRICE = 0.04   # favorites are ~4% underpriced on average
    LONGSHOT_OVERPRICE = 0.03    # longshots are ~3% overpriced on average

    def __init__(self, trader, cfg):
        super().__init__(trader, cfg)
        self._domain_categories = ["politics", "sports", "crypto", "finance"]
        self._last_fetch = 0.0
        self._cache_ttl = 120

    def reset(self):
        pass

    def _brier_score(self, p: float, correct: bool) -> float:
        return (p - (1 if correct else 0)) ** 2

    def scan(self) -> StrategyResult:
        start = time.monotonic()
        signals = []
        errors = []
        scanned = 0

        try:
            markets = list_markets(limit=300, active=True, closed=False,
                                   order="volume", ascending=False)
        except Exception as e:
            log.warning("calibration_bias: market fetch failed: %s", e)
            return StrategyResult(signals=[], error=str(e))

        for mkt in markets:
            vol = float(mkt.get("volume") or 0)
            if vol < 50_000:  # need decent volume for signal validity
                continue

            prices = _parse(mkt.get("outcomePrices"))
            if not prices:
                continue

            try:
                p_yes = float(prices[0])
            except Exception:
                continue

            if p_yes <= 0 or p_yes >= 1:
                continue

            category = (mkt.get("category") or "").lower()
            question = mkt.get("question", "").lower()

            # ── 1. Favorite-longshot bias ──────────────────────────────────
            # Favorites (60-95%) tend to be underpriced → buy YES
            if self.FAVORITE_MIN <= p_yes <= self.FAVORITE_MAX:
                # Recalibrate: crowd is biased toward longshots
                # Real prob ≈ p_yes + bias
                bias = self.FAVORITE_UNDERPRICE
                # Reduce bias for political (already compressed)
                if "politic" in category or "politics" in question:
                    bias *= 0.5
                fair_p = min(p_yes + bias, 0.99)
                edge = fair_p - p_yes

                if edge >= self.cfg.cal_min_bias_edge:
                    tokens = _parse(mkt.get("clobTokenIds"))
                    if tokens:
                        size = 30.0 / p_yes if p_yes > 0 else 0
                        signals.append(Signal(
                            strategy=self.name,
                            market_slug=mkt.get("slug", ""),
                            signal_type="buy",
                            token_id=tokens[0],
                            outcome="Yes",
                            price=p_yes,
                            size=size,
                            edge=edge,
                            confidence=min(0.88, 0.5 + edge),
                            reason=(
                                f"favorite-longshot bias: market={p_yes:.3f} "
                                f"recal={fair_p:.3f} edge={edge:.3f}"
                            ),
                            metadata={"category": category, "vol": vol,
                                      "p_yes": p_yes, "fair_p": fair_p},
                        ))
                        scanned += 1

            # ── 2. Longshot overpricing → sell YES on underpriced (implied NO) ─
            elif self.LONGSHOT_MIN <= p_yes <= self.LONGSHOT_MAX:
                # Longshots are overpriced → implied NO is cheap
                # Our edge: selling YES at p_yes (collecting premium)
                bias = self.LONGSHOT_OVERPRICE
                if "politic" in category:
                    bias *= 0.3  # political longshots are a different beast
                # We sell YES (short) — our edge is p_yes - fair_value
                fair_p = p_yes - bias
                edge = p_yes - fair_p  # what we collect

                if edge >= self.cfg.cal_min_bias_edge * 0.7:  # slightly lower threshold
                    tokens = _parse(mkt.get("clobTokenIds"))
                    if tokens:
                        size = 15.0 / p_yes if p_yes > 0 else 0
                        signals.append(Signal(
                            strategy=self.name,
                            market_slug=mkt.get("slug", ""),
                            signal_type="sell",
                            token_id=tokens[0],
                            outcome="Yes",
                            price=p_yes,
                            size=size,
                            edge=edge,
                            confidence=min(0.80, 0.5 + edge),
                            reason=(
                                f"longshot overprice: market={p_yes:.3f} "
                                f"fair={fair_p:.3f} edge={edge:.3f}"
                            ),
                            metadata={"category": category, "vol": vol,
                                      "p_yes": p_yes, "fair_p": fair_p},
                        ))
                        scanned += 1

        # ── 3. Neg-risk domain check ──────────────────────────────────────────
        now = time.time()
        if now - self._last_fetch > self._cache_ttl:
            self._neg_risk_events = get_neg_risk_events(limit=100)
            self._last_fetch = now

        for evt in getattr(self, "_neg_risk_events", []):
            result = neg_risk_check(evt)
            if not result:
                continue

            vol = result["volume"]
            if vol < 100_000:
                continue

            # Check if any individual outcome is in the favorite range
            for mkt in result["markets"]:
                p_yes = mkt.get("yes_price", 0)
                if not (self.FAVORITE_MIN <= p_yes <= self.FAVORITE_MAX):
                    continue

                tokens = mkt.get("clobTokenIds", [])
                if not tokens:
                    continue

                # Horizon effect: short-dated markets are better calibrated
                # So favor the bias on longer-dated ones
                end_date = evt.get("endDate", "")
                bias = self.FAVORITE_UNDERPRICE * 0.8
                fair_p = min(p_yes + bias, 0.99)
                edge = fair_p - p_yes

                if edge >= self.cfg.cal_min_bias_edge:
                    size = 25.0 / p_yes if p_yes > 0 else 0
                    signals.append(Signal(
                        strategy=self.name,
                        market_slug=mkt["slug"],
                        signal_type="buy",
                        token_id=tokens[0],
                        outcome="Yes",
                        price=p_yes,
                        size=size,
                        edge=edge,
                        confidence=min(0.85, 0.5 + edge),
                        reason=f"neg-risk favorite bias: sum_yes={result['sum_yes']:.4f}",
                        metadata={"event": result["event_title"], "vol": vol},
                    ))

        dur = (time.monotonic() - start) * 1000
        return StrategyResult(
            signals=signals,
            scan_duration_ms=dur,
            markets_scanned=scanned,
            error="; ".join(errors) if errors else None,
        )
