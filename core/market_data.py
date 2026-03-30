"""Polymarket market data fetcher — all read-only, no wallet needed."""

import json, time, logging
from datetime import datetime, timezone
from typing import Optional

import requests

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"
HEADERS = {"User-Agent": "polymarket-autoresearch/0.1"}


def _get(url: str, params=None, timeout=15) -> dict | list:
    r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _parse(val) -> list:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return [val]
    return val or []


def fmt_pct(p: float) -> str:
    return f"{p * 100:.1f}%"


def fmt_vol(v: float) -> str:
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.0f}"


# ── Gamma ──────────────────────────────────────────────────────────────────────

def list_markets(
    limit: int = 200,
    active: bool = True,
    closed: bool = False,
    order: str = "volume",
    ascending: bool = False,
) -> list[dict]:
    return _get(
        f"{GAMMA}/markets",
        {"limit": limit, "active": active, "closed": closed,
         "order": order, "ascending": ascending},
    )


def list_events(
    limit: int = 200,
    active: bool = True,
    closed: bool = False,
    neg_risk: bool = False,
) -> list[dict]:
    params = {"limit": limit, "active": active, "closed": closed}
    if neg_risk:
        # Use filter on market negRisk via market-level query
        pass
    return _get(f"{GAMMA}/events", params)


def get_neg_risk_events(limit: int = 200) -> list[dict]:
    """Get neg-risk events with market details."""
    events = _get(
        f"{GAMMA}/events",
        {"limit": limit, "active": True, "closed": False},
    )
    return [e for e in events if e.get("negRisk")]


def search_markets(q: str, limit: int = 10) -> list[dict]:
    data = _get(f"{GAMMA}/public-search", {"q": q, "limit": limit})
    out = []
    for evt in data.get("events", []):
        for m in evt.get("markets", []):
            m["_event_title"] = evt.get("title")
            m["_event_volume"] = evt.get("volume")
            out.append(m)
    return out


def get_market_by_slug(slug: str) -> Optional[dict]:
    ms = _get(f"{GAMMA}/markets", {"slug": slug})
    return ms[0] if ms else None


# ── CLOB ───────────────────────────────────────────────────────────────────────

def get_price(token_id: str) -> dict:
    try:
        buy = _get(f"{CLOB}/price", {"token_id": token_id, "side": "buy"}, timeout=10)
        mid = _get(f"{CLOB}/midpoint", {"token_id": token_id}, timeout=10)
        spr = _get(f"{CLOB}/spread", {"token_id": token_id}, timeout=10)
        return {"buy": float(buy.get("price", 0)), "mid": float(mid.get("mid", 0)),
                "spread": float(spr.get("spread", 0))}
    except Exception as e:
        log.warning("get_price failed for %s: %s", token_id, e)
        return {}


def get_orderbook(token_id: str) -> dict:
    try:
        return _get(f"{CLOB}/book", {"token_id": token_id}, timeout=10)
    except Exception as e:
        log.warning("get_orderbook failed for %s: %s", token_id, e)
        return {"bids": [], "asks": [], "last_trade_price": None}


def get_prices_history(condition_id: str, interval: str = "all",
                       fidelity: int = 50) -> list[dict]:
    try:
        data = _get(
            f"{CLOB}/prices-history",
            {"market": condition_id, "interval": interval, "fidelity": fidelity},
            timeout=20,
        )
        return data.get("history", [])
    except Exception as e:
        log.warning("prices_history failed for %s: %s", condition_id, e)
        return []


def get_clob_markets(limit: int = 100) -> list[dict]:
    try:
        data = _get(f"{CLOB}/markets", {"limit": limit})
        return data.get("data", [])
    except Exception as e:
        log.warning("clob_markets failed: %s", e)
        return []


# ── Data API ──────────────────────────────────────────────────────────────────

def get_leaderboard(category: str = "OVERALL", period: str = "MONTH",
                     order_by: str = "PNL", limit: int = 10) -> list[dict]:
    return _get(
        f"{DATA}/v1/leaderboard",
        {"category": category, "timePeriod": period, "orderBy": order_by,
         "limit": limit},
        timeout=20,
    )


def get_trades(user: str = None, market: str = None,
               limit: int = 100) -> list[dict]:
    params = {"limit": limit}
    if user:
        params["user"] = user
    if market:
        params["market"] = market
    return _get(f"{DATA}/trades", params, timeout=20)


def get_top_holders(condition_ids: list[str], limit: int = 10) -> list[dict]:
    return _get(
        f"{DATA}/holders",
        {"market": ",".join(condition_ids), "limit": limit},
        timeout=20,
    )


# ── Market helpers ────────────────────────────────────────────────────────────

def parse_market_prices(m: dict) -> dict:
    prices = _parse(m.get("outcomePrices"))
    outcomes = _parse(m.get("outcomes"))
    tokens = _parse(m.get("clobTokenIds"))
    return {
        "prices": [float(p) for p in prices] if prices else [],
        "outcomes": outcomes,
        "clobTokenIds": tokens,
    }


def neg_risk_check(event: dict) -> dict:
    """Analyze a neg-risk event for arbitrage opportunity."""
    markets = event.get("markets", [])
    if len(markets) < 2:
        return {}

    rows = []
    for m in markets:
        prices = _parse(m.get("outcomePrices"))
        outcomes = _parse(m.get("outcomes"))
        if prices and len(prices) >= 1:
            rows.append({
                "market_id": m.get("id"),
                "slug": m.get("slug"),
                "question": m.get("question"),
                "yes_price": float(prices[0]),
                "no_price": float(prices[1]) if len(prices) > 1 else 1.0 - float(prices[0]),
                "outcomes": outcomes,
                "clobTokenIds": _parse(m.get("clobTokenIds")),
                "volume": float(m.get("volume") or 0),
            })

    if not rows:
        return {}

    sum_yes = sum(r["yes_price"] for r in rows)
    sum_no = sum(r["no_price"] for r in rows)
    dev_from_one = abs(sum_yes - 1.0)

    return {
        "event_slug": event.get("slug"),
        "event_title": event.get("title"),
        "neg_risk": event.get("negRisk"),
        "neg_risk_augmented": event.get("negRiskAugmented"),
        "volume": float(event.get("volume") or 0),
        "liquidity": float(event.get("liquidity") or 0),
        "market_count": len(rows),
        "sum_yes": sum_yes,
        "sum_no": sum_no,
        "deviation": dev_from_one,
        "arb_edge_long": max(0, 1.0 - sum_yes),   # buy all YES if sum < 1
        "arb_edge_short": max(0, sum_yes - 1.0),  # sell all YES if sum > 1
        "markets": rows,
    }


def complete_set_edge(market: dict) -> float:
    """For binary up/down markets: complete-set edge = 1 - (bid_up + bid_dn)."""
    prices = _parse(market.get("outcomePrices"))
    if not prices or len(prices) < 2:
        return 0.0
    return max(0.0, 1.0 - float(prices[0]) - float(prices[1]))


if __name__ == "__main__":
    # Quick smoke test
    markets = list_markets(limit=5)
    print(f"Markets fetched: {len(markets)}")
    for m in markets[:2]:
        print(f"  {m.get('question', '?')[:60]} | {m.get('outcomePrices')}")
