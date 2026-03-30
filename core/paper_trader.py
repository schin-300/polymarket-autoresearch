"""Paper trader — simulates execution on Polymarket data, no real wallet."""

import json, logging, uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from core.config import StrategyConfig
from core.market_data import get_price, get_orderbook, fmt_pct, fmt_vol

log = logging.getLogger(__name__)


class Side(Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class Position:
    position_id: str
    strategy: str
    market_slug: str
    token_id: str          # outcome token we bought
    outcome: str            # "Yes" or "No"
    side: str              # "BUY" or "SELL"
    price: float           # fill price
    size: float            # shares
    timestamp: float       # unix timestamp
    resolved: bool = False
    outcome_label: Optional[str] = None  # "Yes" or "No" — set on resolution
    resolved_price: Optional[float] = None  # what it paid out (1.0 or 0.0)

    @property
    def cost(self) -> float:
        return self.price * self.size

    @property
    def pnl(self) -> float:
        if self.resolved:
            payout = self.resolved_price * self.size
            if self.side == "BUY":
                return payout - self.cost
            else:  # SELL — we received premium already
                return self.cost - payout * self.size  # received premium, owe payout
        return 0.0


@dataclass
class PaperOrder:
    order_id: str
    strategy: str
    market_slug: str
    token_id: str
    side: str
    price: float
    size: float
    timestamp: float
    filled: bool = False
    fill_price: Optional[float] = None


@dataclass
class TradeResult:
    trade_id: str
    strategy: str
    market_slug: str
    side: str
    price: float
    size: float
    pnl: float = 0.0
    edge: float = 0.0
    timestamp: float = 0.0
    resolution_correct: Optional[bool] = None  # None = unresolved
    confidence: float = 0.0


class PaperTrader:
    """Simulates trading against live Polymarket book data."""

    def __init__(self, cfg: StrategyConfig):
        self.cfg = cfg
        self.bankroll = cfg.paper_bankroll
        self.initial_bankroll = cfg.paper_bankroll
        self.positions: list[Position] = []
        self.orders: list[PaperOrder] = []
        self.trade_history: list[TradeResult] = []
        self._price_cache: dict[str, dict] = {}

    def _simulate_fill(self, side: str, price: float, size: float,
                       book: dict) -> float:
        """Simulate fill price accounting for slippage."""
        slip = self.cfg.slippage_buy if side == "BUY" else self.cfg.slippage_sell
        slippage_cost = price * slip
        if side == "BUY":
            return price + slippage_cost
        else:
            return price - slippage_cost

    def open_order(self, strategy: str, market_slug: str, token_id: str,
                   side: str, price: float, size: float) -> Optional[PaperOrder]:
        """Simulate placing a paper limit order."""
        est_fill = self._simulate_fill(side, price, size, {})
        cost = est_fill * size
        if side == "BUY" and cost > self.bankroll:
            log.warning("[%s] insufficient bankroll for %s: need $%.2f, have $%.2f",
                        strategy, market_slug, cost, self.bankroll)
            return None

        order = PaperOrder(
            order_id=str(uuid.uuid4())[:8],
            strategy=strategy,
            market_slug=market_slug,
            token_id=token_id,
            side=side.upper(),
            price=price,
            size=size,
            timestamp=datetime.now(timezone.utc).timestamp(),
        )
        self.orders.append(order)
        return order

    def fill_order(self, order_id: str) -> Optional[Position]:
        """Fill a paper order at current market price."""
        order = next((o for o in self.orders if o.order_id == order_id), None)
        if not order:
            return None

        # Get current market price
        book = get_orderbook(order.token_id)
        best_bid = float(book.get("bids", [[0]])[0][0]) if book.get("bids") else 0
        best_ask = float(book.get("asks", [[0]])[0][0]) if book.get("asks") else 0

        if order.side == "BUY":
            fill_price = best_ask if best_ask > 0 else order.price
        else:
            fill_price = best_bid if best_bid > 0 else order.price

        fill_price = self._simulate_fill(order.side, fill_price, order.size, book)

        # Deduct cost from bankroll
        if order.side == "BUY":
            self.bankroll -= fill_price * order.size
        else:
            # SELL: receive premium now
            self.bankroll += fill_price * order.size

        position = Position(
            position_id=order.order_id,
            strategy=order.strategy,
            market_slug=order.market_slug,
            token_id=order.token_id,
            outcome=order.side,  # side represents what we bought/sold
            side=order.side,
            price=fill_price,
            size=order.size,
            timestamp=order.timestamp,
        )
        self.positions.append(position)
        order.filled = True
        order.fill_price = fill_price
        self.orders.remove(order)
        return position

    def fill_at_mid(self, strategy: str, market_slug: str, token_id: str,
                    side: str, price: float, size: float,
                    outcome_label: str, edge: float = 0,
                    confidence: float = 0.8) -> Position:
        """Direct fill at mid price — simplified for paper trading."""
        if side == "BUY":
            fill_price = price * (1 + self.cfg.slippage_buy)
            self.bankroll -= fill_price * size
        else:
            fill_price = price * (1 - self.cfg.slippage_sell)
            self.bankroll += fill_price * size

        position = Position(
            position_id=str(uuid.uuid4())[:8],
            strategy=strategy,
            market_slug=market_slug,
            token_id=token_id,
            outcome=outcome_label,
            side=side.upper(),
            price=fill_price,
            size=size,
            timestamp=datetime.now(timezone.utc).timestamp(),
        )
        self.positions.append(position)

        tr = TradeResult(
            trade_id=position.position_id,
            strategy=strategy,
            market_slug=market_slug,
            side=side,
            price=fill_price,
            size=size,
            edge=edge,
            timestamp=position.timestamp,
            confidence=confidence,
        )
        self.trade_history.append(tr)
        return position

    def resolve_position(self, position_id: str, won: bool) -> float:
        """Mark a position as resolved and compute PnL."""
        pos = next((p for p in self.positions if p.position_id == position_id), None)
        if not pos:
            return 0.0

        pos.resolved = True
        resolved_price = 1.0 if won else 0.0
        pos.resolved_price = resolved_price

        payout = resolved_price * pos.size
        if pos.side == "BUY":
            pnl = payout - pos.cost
        else:  # SELL
            pnl = pos.cost - payout * pos.size

        self.bankroll += payout
        # Update trade result
        tr = next((t for t in self.trade_history if t.trade_id == position_id), None)
        if tr:
            tr.pnl = pnl
            tr.resolution_correct = (pnl > 0)

        log.info("[%s] resolved %s %s: pnl=$%.4f | bankroll=$%.2f",
                 pos.strategy, pos.market_slug, "WIN" if won else "LOSS",
                 pnl, self.bankroll)
        return pnl

    def auto_resolve_closed_markets(self, resolved_slugs: dict[str, str]):
        """Auto-resolve all positions whose market has closed.
        resolved_slugs: {slug -> winning_outcome_label}
        """
        total_pnl = 0.0
        for pos in list(self.positions):
            if pos.resolved or pos.market_slug not in resolved_slugs:
                continue
            won_label = resolved_slugs[pos.market_slug]
            # "Yes" or "No" — compare to our outcome label
            won = (pos.outcome == won_label)
            total_pnl += self.resolve_position(pos.position_id, won)
        return total_pnl

    @property
    def unrealized_pnl(self) -> float:
        return sum(
            p.cost - (p.resolved_price or 0) * p.size
            for p in self.positions if not p.resolved
        )

    @property
    def stats(self) -> dict:
        resolved = [p for p in self.positions if p.resolved]
        pnls = [p.pnl for p in resolved]
        wins = [p for p in resolved if p.pnl > 0]
        total_cost = sum(p.cost for p in resolved)
        total_pnl = sum(pnls)

        if self.initial_bankroll > 0:
            roi = (self.bankroll - self.initial_bankroll) / self.initial_bankroll
        else:
            roi = 0.0

        import statistics
        mean_pnl = statistics.mean(pnls) if pnls else 0.0
        std_pnl = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
        sharpe = (mean_pnl / std_pnl * 16) if std_pnl > 0 else 0.0  # annualised-ish

        # Drawdown
        peak = self.initial_bankroll
        dd = 0.0
        running = self.initial_bankroll
        for pnl in pnls:
            running += pnl
            if running > peak:
                peak = running
            d = (peak - running) / peak if peak > 0 else 0
            if d > dd:
                dd = d

        return {
            "bankroll": self.bankroll,
            "roi": roi,
            "total_trades": len(resolved),
            "wins": len(wins),
            "losses": len(resolved) - len(wins),
            "win_rate": len(wins) / len(resolved) if resolved else 0.0,
            "total_pnl": total_pnl,
            "mean_pnl": mean_pnl,
            "sharpe": sharpe,
            "max_drawdown": dd,
            "unrealized_pnl": self.unrealized_pnl,
            "open_positions": len([p for p in self.positions if not p.resolved]),
        }
