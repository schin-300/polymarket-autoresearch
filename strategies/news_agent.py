"""News signal agent — price movement detection after events."""

import time, logging, urllib.request, json
from datetime import datetime, timezone

from core.market_data import (
    list_markets, search_markets, get_prices_history,
    fmt_vol, fmt_pct, _parse,
)
from strategies.base import BaseStrategy, StrategyResult, Signal

log = logging.getLogger(__name__)


def get_recent_news() -> list[dict]:
    """Fetch recent crypto/political news via NewsAPI (free tier)."""
    try:
        # This is a placeholder — in production you'd use a real news API
        # For now, we rely on Polymarket's own market movement as the signal
        return []
    except Exception:
        return []


class NewsAgent(BaseStrategy):
    """
    Trades on probability movements following news events.

    Approach:
    1. Track trending markets by volume
    2. Detect significant price movements (price change > threshold)
    3. Distinguish momentum vs mean-reversion regimes
    4. Trade in the direction of likely correction/momentum

    Key insight: Polymarket prices react to news, but sometimes over/under-react.
    The trick is knowing which is which.
    """

    name = "news_agent"
    enabled = True

    def __init__(self, trader, cfg):
        super().__init__(trader, cfg)
        self._price_history: dict[str, list] = {}  # slug -> [(timestamp, price)]

    def reset(self):
        self._price_history.clear()

    def _track_price(self, slug: str, price: float, timestamp: float = None):
        if timestamp is None:
            timestamp = time.time()
        if slug not in self._price_history:
            self._price_history[slug] = []
        self._price_history[slug].append((timestamp, price))
        # Keep last 100 data points
        if len(self._price_history[slug]) > 100:
            self._price_history[slug] = self._price_history[slug][-100:]

    def _detect_movement(self, slug: str, current_price: float) -> dict:
        history = self._price_history.get(slug, [])
        if len(history) < 5:
            return {"regime": "unknown", "momentum": 0.0, "signal": "none"}

        prices = [h[1] for h in history[-10:]]
        current = current_price
        baseline = prices[0]

        pct_change = (current - baseline) / baseline if baseline > 0 else 0
        momentum = pct_change

        # Simple momentum vs mean-reversion:
        # If price moved > threshold in short time → likely overreaction → mean-revert
        # If price moved gradually over many data points → momentum

        threshold = self.cfg.news_min_price_move
        if abs(pct_change) < threshold:
            regime = "stable"
            signal = "none"
        elif len(prices) >= 5:
            # Check if all moves were in same direction
            signs = [1 if prices[i+1] > prices[i] else -1
                     for i in range(len(prices)-1)]
            directional_count = sum(1 for s in signs if s == (1 if momentum > 0 else -1))
            if directional_count >= len(signs) * 0.7:
                regime = "momentum"
                signal = "follow"  # ride the momentum
            else:
                regime = "overreaction"
                signal = "reverse"  # bet on mean reversion
        else:
            regime = "unknown"
            signal = "none"

        return {
            "regime": regime,
            "signal": signal,
            "momentum": momentum,
            "pct_change": pct_change,
            "baseline": baseline,
            "current": current,
        }

    def scan(self) -> StrategyResult:
        start = time.monotonic()
        signals = []
        errors = []
        scanned = 0

        try:
            markets = list_markets(limit=200, active=True, closed=False,
                                   order="volume", ascending=False)
        except Exception as e:
            log.warning("news_agent: market fetch failed: %s", e)
            return StrategyResult(signals=[], error=str(e))

        for mkt in markets:
            vol = float(mkt.get("volume") or 0)
            if vol < self.cfg.news_min_volume:
                continue

            prices = _parse(mkt.get("outcomePrices"))
            if not prices:
                continue

            try:
                p_yes = float(prices[0])
            except Exception:
                continue

            slug = mkt.get("slug", "")
            self._track_price(slug, p_yes)

            move = self._detect_movement(slug, p_yes)

            if move["signal"] == "none":
                continue

            scanned += 1
            tokens = _parse(mkt.get("clobTokenIds"))

            if move["signal"] == "reverse":
                # Mean reversion: if price jumped up, sell YES (bet it goes back down)
                # If price dropped, buy YES (bet it goes back up)
                if move["pct_change"] > 0:
                    sig = Signal(
                        strategy=self.name,
                        market_slug=slug,
                        signal_type="sell",
                        token_id=tokens[0] if tokens else "",
                        outcome="Yes",
                        price=p_yes,
                        size=20.0 / p_yes if p_yes > 0 else 0,
                        edge=abs(move["momentum"]) * 0.5,
                        confidence=min(0.85, 0.5 + abs(move["momentum"])),
                        reason=(
                            f"mean-reversion: price moved {move['pct_change']:+.1%} "
                            f"in {len(self._price_history.get(slug,[]))} ticks"
                        ),
                        metadata={"regime": move["regime"], "vol": vol,
                                  "momentum": move["momentum"]},
                    )
                else:
                    sig = Signal(
                        strategy=self.name,
                        market_slug=slug,
                        signal_type="buy",
                        token_id=tokens[0] if tokens else "",
                        outcome="Yes",
                        price=p_yes,
                        size=20.0 / p_yes if p_yes > 0 else 0,
                        edge=abs(move["momentum"]) * 0.5,
                        confidence=min(0.85, 0.5 + abs(move["momentum"])),
                        reason=(
                            f"mean-reversion: price dropped {move['pct_change']:+.1%}"
                        ),
                        metadata={"regime": move["regime"], "vol": vol,
                                  "momentum": move["momentum"]},
                    )
                signals.append(sig)

            elif move["signal"] == "follow":
                # Momentum: ride the wave
                if move["pct_change"] > 0:
                    sig = Signal(
                        strategy=self.name,
                        market_slug=slug,
                        signal_type="buy",
                        token_id=tokens[0] if tokens else "",
                        outcome="Yes",
                        price=p_yes,
                        size=20.0 / p_yes if p_yes > 0 else 0,
                        edge=abs(move["momentum"]) * 0.3,
                        confidence=min(0.75, 0.5 + abs(move["momentum"]) * 0.5),
                        reason=f"momentum follow: {move['pct_change']:+.1%}",
                        metadata={"regime": move["regime"], "vol": vol},
                    )
                else:
                    # Price dropping → buy NO (market is saying No is more likely)
                    sig = Signal(
                        strategy=self.name,
                        market_slug=slug,
                        signal_type="sell",
                        token_id=tokens[0] if tokens else "",
                        outcome="Yes",  # selling YES = betting NO
                        price=p_yes,
                        size=20.0 / p_yes if p_yes > 0 else 0,
                        edge=abs(move["momentum"]) * 0.3,
                        confidence=min(0.75, 0.5 + abs(move["momentum"]) * 0.5),
                        reason=f"momentum follow (short): {move['pct_change']:+.1%}",
                        metadata={"regime": move["regime"], "vol": vol},
                    )
                signals.append(sig)

        dur = (time.monotonic() - start) * 1000
        return StrategyResult(
            signals=signals,
            scan_duration_ms=dur,
            markets_scanned=scanned,
            error="; ".join(errors) if errors else None,
        )
