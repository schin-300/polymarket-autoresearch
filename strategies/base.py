"""Abstract base for all strategies."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import logging

log = logging.getLogger(__name__)


@dataclass
class Signal:
    strategy: str
    market_slug: str
    signal_type: str          # "buy" | "sell" | "close"
    token_id: str
    outcome: str              # "Yes" | "No"
    price: float
    size: float               # shares
    edge: float               # expected edge over fair value
    confidence: float         # 0..1
    reason: str                # human-readable reason
    metadata: dict = field(default_factory=dict)


@dataclass
class StrategyResult:
    signals: list[Signal] = field(default_factory=list)
    scan_duration_ms: float = 0.0
    markets_scanned: int = 0
    error: Optional[str] = None


class BaseStrategy(ABC):
    """Every strategy inherits from this."""

    name: str = "base"
    enabled: bool = True

    def __init__(self, trader, cfg):
        self.trader = trader
        self.cfg = cfg
        self._scan_count = 0
        self._last_signal_time = 0.0

    @abstractmethod
    def scan(self) -> StrategyResult:
        """Main scan — returns signals to execute."""
        raise NotImplementedError

    @abstractmethod
    def reset(self):
        """Clear per-session state."""
        pass

    def execute_signals(self, signals: list[Signal]) -> list:
        """Default execution: fill all signals."""
        filled = []
        for sig in signals:
            if sig.signal_type in ("buy", "sell"):
                pos = self.trader.fill_at_mid(
                    strategy=self.name,
                    market_slug=sig.market_slug,
                    token_id=sig.token_id,
                    side=sig.signal_type.upper(),
                    price=sig.price,
                    size=sig.size,
                    outcome_label=sig.outcome,
                    edge=sig.edge,
                    confidence=sig.confidence,
                )
                # Skip phantom fills (already have position)
                if pos.position_id != "skipped":
                    filled.append(pos)
            elif sig.signal_type == "close":
                pass  # resolve logic handled separately
        return filled

    @property
    def scan_count(self) -> int:
        return self._scan_count

    def __repr__(self) -> str:
        return f"<Strategy {self.name} enabled={self.enabled}>"
