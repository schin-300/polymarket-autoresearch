"""Crypto threshold model — fair value estimation for BTC/ETH/SOL threshold markets."""

import time, math, logging, urllib.request
from datetime import datetime, timezone
from typing import Optional

from core.market_data import (
    list_markets, search_markets, get_orderbook,
    fmt_vol, fmt_pct, _parse,
)
from strategies.base import BaseStrategy, StrategyResult, Signal

log = logging.getLogger(__name__)


def get_btc_price() -> Optional[float]:
    """Get BTC spot price from Coinbase public API."""
    try:
        url = "https://api.coinbase.com/v2/prices/BTC-USD/spot"
        data = urllib.request.urlopen(url, timeout=10).read()
        import json
        return float(json.loads(data)["data"]["amount"])
    except Exception as e:
        log.debug("btc price fetch failed: %s", e)
        return None


def get_eth_price() -> Optional[float]:
    try:
        url = "https://api.coinbase.com/v2/prices/ETH-USD/spot"
        data = urllib.request.urlopen(url, timeout=10).read()
        import json
        return float(json.loads(data)["data"]["amount"])
    except Exception:
        return None


def get_sol_price() -> Optional[float]:
    try:
        url = "https://api.coinbase.com/v2/prices/SOL-USD/spot"
        data = urllib.request.urlopen(url, timeout=10).read()
        import json
        return float(json.loads(data)["data"]["amount"])
    except Exception:
        return None


class CryptoThreshold(BaseStrategy):
    """
    Models fair value for BTC/ETH/SOL threshold markets.

    Signal logic:
    - Fetch active threshold markets (BTC > $X, ETH > $Y, etc.)
    - Estimate fair probability from spot + time-to-expiry + vol regime
    - Compare model price vs market price → edge
    - Trade when |edge| > threshold

    Fair value heuristic:
    - For "will BTC close above $X by date D":
      P ≈ N((ln(S/K)) / (σ√T))  (Black-Scholes style, log-normal)
    - Simplified: use distance to strike, time remaining, assume σ=0.8 for crypto
    """

    name = "crypto_threshold"
    enabled = True

    def __init__(self, trader, cfg):
        super().__init__(trader, cfg)
        self._spot_prices: dict[str, float] = {}
        self._last_spot_fetch = 0.0
        self._spot_ttl = 30  # refresh spot every 30s

    def reset(self):
        self._spot_prices = {}

    def _get_spot(self, asset: str) -> Optional[float]:
        now = time.time()
        if now - self._last_spot_fetch > self._spot_ttl:
            self._spot_prices = {}
            for sym, fn in [("BTC", get_btc_price), ("ETH", get_eth_price),
                            ("SOL", get_sol_price)]:
                p = fn()
                if p:
                    self._spot_prices[sym] = p
            self._last_spot_fetch = now
        return self._spot_prices.get(asset)

    def _parse_threshold_market(self, mkt: dict) -> Optional[dict]:
        """Parse a threshold market question to extract asset + strike + direction."""
        q = mkt.get("question", "")

        # Detect asset — must have "$" near the asset name to avoid false matches
        # "Solstice" should not match SOL, "Ethereum" should not match ETH if no $
        import re
        asset = None

        # Require "$" before or after the asset keyword to be a real price threshold
        btc_match = re.search(r'(?:BITCOIN|BTC).*?\$([\d,]+)', q, re.IGNORECASE)
        if btc_match:
            asset = "BTC"
        else:
            eth_match = re.search(r'(?:ETHEREUM|ETH(?!-\w)|ETHER).*?\$([\d,]+)', q, re.IGNORECASE)
            if eth_match:
                asset = "ETH"
            else:
                # SOL/Solana: must be standalone word, followed by $
                sol_match = re.search(r'\b(SOL|SOLANA)\b.*?\$([\d,]+)', q, re.IGNORECASE)
                if sol_match:
                    asset = "SOL"
                else:
                    # Try dollar-first pattern: $50,000+ bitcoin
                    dollar_btc = re.search(r'\$([\d,]+).*?(?:BITCOIN|BTC)', q, re.IGNORECASE)
                    if dollar_btc:
                        asset = "BTC"
                    else:
                        dollar_eth = re.search(r'\$([\d,]+).*?(?:ETHEREUM|ETH)', q, re.IGNORECASE)
                        if dollar_eth:
                            asset = "ETH"

        if not asset:
            return None

        # Detect threshold value — must have "$" sign
        amounts = re.findall(r'\$([\d,]+(?:\.\d+)?)', q)
        amounts = [float(a.replace(",", "")) for a in amounts
                   if float(a.replace(",", "")) > 100]
        if not amounts:
            return None

        strike = max(amounts)

        # Detect direction: "above" = UP, "below" = DOWN
        q_lower = q.lower()
        direction = None
        if any(w in q_lower for w in ["above", "higher", "exceed", "reach", "climb", "higher than"]):
            direction = "up"
        elif any(w in q_lower for w in ["below", "lower", "drop", "fall", "dip", "under"]):
            direction = "down"

        # Time to expiry
        end_date = mkt.get("endDate") or mkt.get("expiresAt")
        return {
            "asset": asset,
            "strike": strike,
            "direction": direction,
            "end_date": end_date,
            "market": mkt,
        }

    def _bs_prob(self, S: float, K: float, T: float, sigma: float = 0.80,
                 r: float = 0.0) -> float:
        """Black-Scholes probability of option being in-the-money at expiry."""
        if T <= 0 or sigma <= 0 or S <= 0 or K <= 0:
            return 0.5
        import math
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        def norm_cdf(x):
            return 0.5 * (1 + math.erf(x / math.sqrt(2)))
        return norm_cdf(d1)

    def _fair_value(self, asset: str, strike: float, direction: str,
                    end_date_str: str) -> Optional[float]:
        """Compute model fair probability."""
        spot = self._get_spot(asset)
        if not spot:
            return None

        # Parse expiry
        try:
            end_dt = datetime.fromisoformat(end_date_str.replace("Z", "+00:00"))
            T = max((end_dt - datetime.now(timezone.utc)).total_seconds() / 31536000, 0.0001)
        except Exception:
            T = 7 / 365  # default: 1 week

        sigma = 0.80  # crypto vol assumption

        if direction == "up":
            # P(S_T > K) = 1 - N(d1) but for our approximation:
            p = self._bs_prob(spot, strike, T, sigma)
        else:
            p = self._bs_prob(spot, strike, T, sigma)
            p = 1.0 - p

        return max(0.01, min(0.99, p))

    def scan(self) -> StrategyResult:
        start = time.monotonic()
        signals = []
        errors = []
        scanned = 0

        try:
            markets = list_markets(limit=200, active=True, closed=False,
                                   order="volume", ascending=False)
        except Exception as e:
            log.warning("crypto_threshold: market fetch failed: %s", e)
            return StrategyResult(signals=[], error=str(e))

        for mkt in markets:
            vol = float(mkt.get("volume") or 0)
            if vol < self.cfg.crypto_min_volume:
                continue

            parsed = self._parse_threshold_market(mkt)
            if not parsed:
                continue

            scanned += 1
            fair_p = self._fair_value(
                parsed["asset"], parsed["strike"],
                parsed["direction"], parsed["end_date"]
            )
            if fair_p is None:
                continue

            prices = _parse(mkt.get("outcomePrices"))
            if not prices:
                continue

            market_p = float(prices[0])  # YES price
            edge = fair_p - market_p

            if abs(edge) >= self.cfg.crypto_min_edge and abs(edge) < 0.50:
                tokens = _parse(mkt.get("clobTokenIds"))
                size = 20.0 / market_p if market_p > 0 else 0
                sig = Signal(
                    strategy=self.name,
                    market_slug=mkt.get("slug", ""),
                    signal_type="buy" if edge > 0 else "sell",
                    token_id=tokens[0] if tokens else "",
                    outcome="Yes",
                    price=market_p,
                    size=size,
                    edge=abs(edge),
                    confidence=min(0.90, 0.5 + abs(edge)),
                    reason=(
                        f"{parsed['asset']} threshold {parsed['direction']} "
                        f"${parsed['strike']:.0f}: model={fair_p:.3f} "
                        f"market={market_p:.3f} edge={edge:+.3f}"
                    ),
                    metadata={
                        "asset": parsed["asset"],
                        "strike": parsed["strike"],
                        "direction": parsed["direction"],
                        "spot": self._spot_prices.get(parsed["asset"]),
                        "fair_p": fair_p,
                        "market_p": market_p,
                        "vol": vol,
                    },
                )
                signals.append(sig)

        dur = (time.monotonic() - start) * 1000
        return StrategyResult(
            signals=signals,
            scan_duration_ms=dur,
            markets_scanned=scanned,
            error="; ".join(errors) if errors else None,
        )
