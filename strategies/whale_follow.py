"""Whale follower — tracks top wallets and copies high-conviction signals."""

import time, logging, json
from collections import defaultdict
from datetime import datetime, timezone

from core.market_data import (
    get_leaderboard, get_trades, get_top_holders,
    fmt_vol, fmt_pct, _parse,
)
from strategies.base import BaseStrategy, StrategyResult, Signal

log = logging.getLogger(__name__)


class WhaleFollow(BaseStrategy):
    """
    Tracks top Polymarket trader wallets and detects conviction signals.

    Signal logic:
    - Fetch top leaderboard wallets (by PnL, monthly)
    - Watch for repeated trades in same direction on same market
    - High conviction = 3+ repeats OR large size (> $500)
    - Only generates signal, not a standalone strategy

    This is a signal layer — combine with other strategies for confirmation.
    """

    name = "whale_follow"
    enabled = True

    def __init__(self, trader, cfg):
        super().__init__(trader, cfg)
        self._wallet_trades: dict[str, list[dict]] = defaultdict(list)
        self._last_fetch = 0.0
        self._cache_ttl = 60  # refresh whale data every 60s
        self._leaderboard_wallets: list[dict] = []

    def reset(self):
        self._wallet_trades.clear()

        # Track repeated trades per (wallet, market, side)
        self._repeated_trades: dict[str, int] = defaultdict(int)

    def _fetch_whale_trades(self) -> list[dict]:
        """Fetch recent large trades from top wallets."""
        all_trades = []

        # Get leaderboard
        try:
            board = get_leaderboard(
                category=self.cfg.wf_category,
                period=self.cfg.wf_period,
                order_by="PNL",
                limit=self.cfg.wf_leaderboard_limit,
            )
            self._leaderboard_wallets = board
        except Exception as e:
            log.warning("whale_follow: leaderboard fetch failed: %s", e)
            return []

        # Get recent trades from each top wallet
        for entry in board:
            wallet = entry.get("proxyWallet")
            if not wallet:
                continue
            try:
                trades = get_trades(user=wallet, limit=20)
                for t in trades:
                    t["_wallet"] = wallet
                    t["_wallet_pnl"] = entry.get("pnl", 0)
                    t["_wallet_rank"] = entry.get("rank", 0)
                all_trades.extend(trades)
            except Exception as e:
                log.debug("whale_follow: trades fetch failed for %s: %s", wallet[:10], e)

        return all_trades

    def _detect_conviction(self, trades: list[dict], market_slug: str,
                           side: str) -> dict:
        """Detect if a whale has high conviction in a direction."""
        relevant = [t for t in trades
                    if t.get("market_slug") == market_slug
                    and t.get("side") == side]

        if not relevant:
            return {"conviction": 0, "count": 0, "avg_size": 0, "total_size": 0}

        sizes = [t.get("size", 0) for t in relevant]
        count = len(relevant)
        total_size = sum(sizes)
        avg_size = total_size / count if count > 0 else 0

        # Conviction score: weighted combination of repeat count and size
        repeat_score = min(count / self.cfg.wf_min_repeat_count, 1.0)
        size_score = min(avg_size / 500.0, 1.0)  # $500 = max size signal
        conviction = (repeat_score * 0.6 + size_score * 0.4)
        conviction = min(conviction, 1.0)

        return {
            "conviction": conviction,
            "count": count,
            "avg_size": avg_size,
            "total_size": total_size,
        }

    def scan(self) -> StrategyResult:
        start = time.monotonic()
        signals = []
        errors = []
        scanned = 0

        now = time.time()
        if now - self._last_fetch > self._cache_ttl:
            self._all_trades = self._fetch_whale_trades()
            self._last_fetch = now
        else:
            self._all_trades = getattr(self, "_all_trades", [])

        # Group trades by (market_slug, side)
        trade_map: dict[tuple, list[dict]] = defaultdict(list)
        for t in self._all_trades:
            slug = t.get("title", "")
            side = t.get("side", "")
            if slug and side:
                trade_map[(slug, side)].append(t)

        for (slug, side), trades in trade_map.items():
            scanned += 1

            # Compute conviction
            conv = self._detect_conviction(trades, slug, side)
            if conv["conviction"] < 0.6:  # minimum conviction threshold
                continue

            # Only act on high conviction
            tokens = []  # Would need market data to get token_id
            # This is why it's a signal layer, not standalone
            signals.append(Signal(
                strategy=self.name,
                market_slug=slug,
                signal_type="buy" if side == "BUY" else "sell",
                token_id="",  # Whale follow doesn't know token — need market lookup
                outcome="Yes" if side == "BUY" else "No",
                price=trades[0].get("price", 0.5) if trades else 0.5,
                size=conv["avg_size"],
                edge=conv["conviction"] * 0.02,  # edge proportional to conviction
                confidence=conv["conviction"],
                reason=(
                    f"whale signal: rank #{trades[0].get('_wallet_rank','?')} "
                    f"wallet repeated {conv['count']}x size=${conv['avg_size']:.0f} "
                    f"conviction={conv['conviction']:.2f}"
                ),
                metadata={
                    "wallet_rank": trades[0].get("_wallet_rank"),
                    "wallet_pnl": trades[0].get("_wallet_pnl"),
                    "repeat_count": conv["count"],
                    "avg_size": conv["avg_size"],
                    "total_size": conv["total_size"],
                },
            ))

        dur = (time.monotonic() - start) * 1000
        return StrategyResult(
            signals=signals,
            scan_duration_ms=dur,
            markets_scanned=scanned,
            error="; ".join(errors) if errors else None,
        )
