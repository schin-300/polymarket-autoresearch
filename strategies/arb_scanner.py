"""Arbitrage scanner — neg-risk events, complete-set pairs, related-market contradictions."""

import time, math, logging
from typing import Optional
from core.market_data import (
    get_neg_risk_events, get_market_by_slug, list_markets,
    neg_risk_check, complete_set_edge, get_orderbook,
    fmt_vol, fmt_pct, _parse,
)
from strategies.base import BaseStrategy, StrategyResult, Signal

log = logging.getLogger(__name__)


class ArbScanner(BaseStrategy):
    """
    Scans for structural pricing inefficiencies:
    1. Neg-risk events: sum of outcome prices != 1.0
    2. Complete-set arb on binary up/down pairs
    3. Cross-market contradictions (related events with opposing prices)
    """

    name = "arb_scanner"
    enabled = True

    def __init__(self, trader, cfg):
        super().__init__(trader, cfg)
        self._neg_risk_events: list[dict] = []
        self._last_fetch = 0.0
        self._cache_ttl = 60  # refresh neg-risk list every 60s

    def reset(self):
        self._neg_risk_events = []

    def scan(self) -> StrategyResult:
        start = time.monotonic()
        signals = []
        errors = []
        total_scanned = 0

        # Refresh neg-risk event list from API
        now = time.time()
        if now - self._last_fetch > self._cache_ttl:
            try:
                self._neg_risk_events = get_neg_risk_events(limit=200)
                self._last_fetch = now
            except Exception as e:
                log.warning("Failed to fetch neg-risk events: %s", e)
                errors.append(str(e))

        # ── 1. Neg-Risk Complete-Set Arbitrage ──────────────────────────────
        # Skip neg_risk_augmented events (they already price in the edge differently)
        # Cap edge at 10% to avoid phantom arb on low-liquidity legs
        candidates = []  # (deviation, signal) pairs

        for evt in self._neg_risk_events:
            if evt.get("negRiskAugmented"):
                continue  # skip augmented events

            result = neg_risk_check(evt)
            if not result or not result.get("markets"):
                continue

            total_scanned += len(result["markets"])

            dev = result["deviation"]
            sum_yes = result["sum_yes"]
            vol = result["volume"]
            liq = result["liquidity"]

            if vol < self.cfg.arb_min_volume:
                continue

            if dev < self.cfg.arb_min_edge:
                continue

            # Skip unreasonable edges (>10%) — likely stale prices or low liquidity
            if dev > 0.10:
                continue

            # Collect all possible signals from this event
            event_signals = []
            edge = abs(dev)

            for mkt in result["markets"]:
                tokens = mkt.get("clobTokenIds", [])
                if not tokens:
                    continue
                yes_token = tokens[0]
                if not yes_token:
                    continue
                direction = "buy" if sum_yes < 1.0 else "sell"
                sig = Signal(
                    strategy=self.name,
                    market_slug=mkt["slug"],
                    signal_type=direction,
                    token_id=yes_token,
                    outcome="Yes",
                    price=mkt["yes_price"],
                    size=30.0 / mkt["yes_price"] if mkt["yes_price"] > 0 else 0,
                    edge=edge,
                    confidence=min(0.95, 0.5 + edge),
                    reason=f"neg-risk {direction}: sum_yes={sum_yes:.4f} edge={edge:.4f}",
                    metadata={"event": result["event_title"], "sum_yes": sum_yes,
                              "vol": vol, "liq": liq},
                )
                event_signals.append((edge, sig))

            if not event_signals:
                continue

            # Sort by edge and take at most top 3 legs per event
            event_signals.sort(key=lambda x: -x[0])
            candidates.extend(event_signals[:3])

        # Sort all candidates globally, take top 20 total
        candidates.sort(key=lambda x: -x[0])
        for edge_val, sig in candidates[:20]:
            signals.append(sig)

        # ── 2. Complete-set arb on explicit up/down pairs only ──────────────
        try:
            markets = list_markets(limit=200, active=True, closed=False,
                                   order="volume", ascending=False)
        except Exception as e:
            log.warning("Failed to fetch markets for arb scan: %s", e)
            markets = []

        seen_slugs = set()  # deduplicate
        for mkt in markets:
            vol = float(mkt.get("volume") or 0)
            if vol < self.cfg.arb_min_volume:
                continue

            prices = _parse(mkt.get("outcomePrices"))
            outcomes = _parse(mkt.get("outcomes"))
            slug = mkt.get("slug", "")

            # Only look for explicit "Up"/"Down" binary pairs
            # Skip neg-risk events (handled in section 1)
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            # Look for explicit up/down binary markets only
            if len(prices) >= 2 and len(outcomes) >= 2:
                outcome_str = " ".join(str(o).lower() for o in outcomes)
                is_updown = ("up" in outcome_str and "down" in outcome_str)
                if not is_updown:
                    continue

                try:
                    cs_edge = complete_set_edge(mkt)
                    if cs_edge >= self.cfg.arb_min_edge:
                        tokens = _parse(mkt.get("clobTokenIds"))
                        if len(tokens) >= 2:
                            signals.append(Signal(
                                strategy=self.name,
                                market_slug=slug,
                                signal_type="buy",
                                token_id=tokens[0],
                                outcome="Yes",
                                price=float(prices[0]),
                                size=20.0 / float(prices[0]) if float(prices[0]) > 0 else 0,
                                edge=cs_edge,
                                confidence=min(0.9, 0.5 + cs_edge),
                                reason=f"up/down complete-set arb edge={cs_edge:.4f}",
                                metadata={"vol": vol, "prices": prices},
                            ))
                            total_scanned += 1
                except Exception as e:
                    log.debug("complete_set_edge error for %s: %s", slug, e)

        dur = (time.monotonic() - start) * 1000
        return StrategyResult(
            signals=signals,
            scan_duration_ms=dur,
            markets_scanned=total_scanned,
            error="; ".join(errors) if errors else None,
        )
