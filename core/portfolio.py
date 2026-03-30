"""Portfolio tracker — persists state, history, journal."""

import json, logging, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DB_PATH = Path("data/portfolio.db")


def _ensure_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            trade_id TEXT PRIMARY KEY,
            strategy TEXT,
            market_slug TEXT,
            side TEXT,
            price REAL,
            size REAL,
            pnl REAL,
            edge REAL,
            confidence REAL,
            timestamp REAL,
            resolved INTEGER,
            resolution_correct INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bankroll REAL,
            total_pnl REAL,
            sharpe REAL,
            max_drawdown REAL,
            win_rate REAL,
            total_trades INTEGER,
            open_positions INTEGER,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT,
            market_slug TEXT,
            signal_type TEXT,
            price REAL,
            edge REAL,
            confidence REAL,
            acted BOOLEAN,
            timestamp REAL
        )
    """)
    conn.commit()
    return conn


def persist_trade(conn: sqlite3.Connection, trade: dict):
    c = conn.cursor()
    c.execute("""
        INSERT OR REPLACE INTO trades
        (trade_id, strategy, market_slug, side, price, size, pnl, edge,
         confidence, timestamp, resolved, resolution_correct)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        trade.get("trade_id"),
        trade.get("strategy"),
        trade.get("market_slug"),
        trade.get("side"),
        trade.get("price"),
        trade.get("size"),
        trade.get("pnl", 0),
        trade.get("edge", 0),
        trade.get("confidence", 0),
        trade.get("timestamp", 0),
        int(trade.get("resolved", False)),
        int(trade.get("resolution_correct")) if trade.get("resolution_correct") is not None else None,
    ))
    conn.commit()


def persist_signal(conn: sqlite3.Connection, signal: dict):
    c = conn.cursor()
    c.execute("""
        INSERT INTO signals
        (strategy, market_slug, signal_type, price, edge, confidence, acted, timestamp)
        VALUES (?,?,?,?,?,?,?,?)
    """, (
        signal.get("strategy"),
        signal.get("market_slug"),
        signal.get("signal_type"),
        signal.get("price"),
        signal.get("edge"),
        signal.get("confidence"),
        int(signal.get("acted", False)),
        signal.get("timestamp", 0),
    ))
    conn.commit()


def persist_snapshot(conn: sqlite3.Connection, stats: dict):
    c = conn.cursor()
    c.execute("""
        INSERT INTO snapshots
        (bankroll, total_pnl, sharpe, max_drawdown, win_rate, total_trades, open_positions)
        VALUES (?,?,?,?,?,?,?)
    """, (
        stats.get("bankroll", 0),
        stats.get("total_pnl", 0),
        stats.get("sharpe", 0),
        stats.get("max_drawdown", 0),
        stats.get("win_rate", 0),
        stats.get("total_trades", 0),
        stats.get("open_positions", 0),
    ))
    conn.commit()


def get_recent_signals(conn: sqlite3.Connection, strategy: str = None,
                       limit: int = 100) -> list[dict]:
    c = conn.cursor()
    if strategy:
        rows = c.execute(
            "SELECT * FROM signals WHERE strategy=? ORDER BY timestamp DESC LIMIT ?",
            (strategy, limit)
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM signals ORDER BY timestamp DESC LIMIT ?",
            (limit,)
        ).fetchall()
    cols = [d[0] for d in c.description] if c.description else []
    return [dict(zip(cols, r)) for r in rows]


def get_performance_by_strategy(conn: sqlite3.Connection) -> dict:
    """Aggregate PnL, win rate, sharpe per strategy."""
    c = conn.cursor()
    rows = c.execute("""
        SELECT strategy,
               COUNT(*) as trades,
               SUM(pnl) as total_pnl,
               AVG(pnl) as mean_pnl,
               MAX(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) * 1.0 / COUNT(*) as win_rate,
               AVG(edge) as avg_edge
        FROM trades
        WHERE resolved = 1
        GROUP BY strategy
    """).fetchall()
    cols = [d[0] for d in c.description] if c.description else []
    return [dict(zip(cols, r)) for r in rows]
