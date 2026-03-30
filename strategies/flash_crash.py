"""Flash crash strategy — orderbook imbalance detection on 15m crypto markets."""

import time, logging, asyncio, httpx
from collections import deque
from typing import Optional

from strategies.base import BaseStrategy, StrategyResult, Signal

log = logging.getLogger(__name__)

# CLOB WebSocket endpoint
CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws"


class FlashCrash(BaseStrategy):
    """
    Monitors orderbook for sudden probability drops — buys the crashed side.

    Logic (from polymarket-trading-bot flash_crash_strategy):
    - Monitor best bid/ask on 15m up/down BTC/ETH markets via WebSocket
    - Track rolling window of last N price observations
    - When current price drops by >= threshold from the window peak → BUY the crashed side
    - Exit at take-profit ($0.10) or stop-loss ($0.05)

    Paper version: simulates orderbook monitoring without real WebSocket.
    """

    name = "flash_crash"
    enabled = True

    def __init__(self, trader, cfg):
        super().__init__(trader, cfg)
        self._price_windows: dict[str, deque] = {}  # token_id -> deque of (timestamp, price)
        self._window_size = 20
        self._drop_threshold = self.cfg.flash_drop_threshold
        self._last_scan_prices: dict[str, float] = {}

    def reset(self):
        self._price_windows.clear()
        self._last_scan_prices.clear()

    def _update_window(self, token_id: str, price: float):
        if token_id not in self._price_windows:
            self._price_windows[token_id] = deque(maxlen=self._window_size)
        self._price_windows[token_id].append((time.time(), price))

    def _detect_crash(self, token_id: str) -> Optional[dict]:
        """Detect if price crashed relative to recent window."""
        window = self._price_windows.get(token_id)
        if not window or len(window) < 5:
            return None

        prices = [p for (_, p) in list(window)]
        window_peak = max(prices)
        window_trough = min(prices)
        current = prices[-1]

        # Drop from peak
        drop = window_peak - current

        # Crash detection: drop >= threshold AND price recovered somewhat
        # (we want to buy the dip, not catch a falling knife)
        recovery = current - window_trough
        if recovery <= 0:
            return None

        if drop >= self._drop_threshold:
            # Price dropped and recovered slightly → potential flash crash
            return {
                "direction": "crash_buy",  # buy the dip
                "drop": drop,
                "peak": window_peak,
                "current": current,
                "recovery": recovery,
                "confidence": min(0.9, 0.5 + recovery / window_peak),
            }

        # Also detect pump (price spiked up — mean revert by selling)
        pump = current - window_peak
        if pump >= self._drop_threshold:
            return {
                "direction": "pump_sell",
                "drop": pump,
                "peak": window_peak,
                "current": current,
                "confidence": min(0.85, 0.5 + pump / window_peak),
            }

        return None

    def scan(self) -> StrategyResult:
        """Note: true flash crash detection requires WebSocket.
        This is a simplified polling version that still demonstrates the strategy."""
        start = time.monotonic()
        signals = []
        errors = []

        # In a full implementation we'd connect to WebSocket here.
        # For paper trading with polling, we use the price delta from
        # the last scan cycle as a proxy.
        #
        # The real flash_crash strategy needs:
        # 1. WebSocket connection to CLOB orderbook
        # 2. Per-market token_id mapping (BTC-yes, BTC-no, etc.)
        # 3. Real-time best bid/ask updates
        #
        # This scan() provides a placeholder that can be upgraded
        # to full WebSocket later.

        # For now: signal that flash crash requires live orderbook data
        dur = (time.monotonic() - start) * 1000
        return StrategyResult(
            signals=[],  # No signals in polling mode — needs WS
            scan_duration_ms=dur,
            markets_scanned=0,
            error=None,
            # Note: flash_crash strategy is WebSocket-native.
            # For paper trading, connect CLOB WS in experiments/loop.py
            # and pass orderbook data to this strategy's update_orderbook() method.
        )

    def update_orderbook(self, token_id: str, best_bid: float, best_ask: float):
        """Called by WebSocket handler in loop.py — updates price window."""
        mid = (best_bid + best_ask) / 2
        self._update_window(token_id, mid)
        crash = self._detect_crash(token_id)
        if crash:
            log.info("[flash_crash] %s detected on %s: %s", crash["direction"], token_id, crash)
            # Signal would be generated here in full implementation
