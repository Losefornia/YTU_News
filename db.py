# -*- coding: utf-8 -*-
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "news.db"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                date TEXT,
                site TEXT,
                category TEXT,
                created_at TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_date ON news(date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_news_site ON news(site)")


def save_news(items) -> int:
    inserted = 0
    with get_conn() as conn:
        for it in items:
            try:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO news "
                    "(title, url, date, site, category, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        it["title"],
                        it["url"],
                        it["date"].isoformat() if it.get("date") else None,
                        it["site"],
                        it.get("category", "其他"),
                        datetime.now().isoformat(),
                    ),
                )
                inserted += cur.rowcount
            except Exception as e:
                logger.warning("[DB ERR] %s: %s", it.get("url"), e)
    return inserted


def query_news(days: Optional[int] = None) -> list[dict]:
    with get_conn() as conn:
        if days is None:
            rows = conn.execute(
                "SELECT * FROM news ORDER BY date DESC"
            ).fetchall()
        else:
            cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
            rows = conn.execute(
                "SELECT * FROM news WHERE date >= ? ORDER BY date DESC",
                (cutoff,),
            ).fetchall()
    return [dict(r) for r in rows]


def count_all() -> int:
    with get_conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]


def query_new_since(since: datetime, limit: int = 5) -> list[dict]:
    """查询 since 之后写入的条目。"""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM news WHERE created_at >= ? "
            "ORDER BY created_at DESC LIMIT ?",
            (since.isoformat(), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_new_since(since: datetime) -> int:
    """统计 since 之后写入的条目数。"""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM news WHERE created_at >= ?",
            (since.isoformat(),),
        ).fetchone()[0]
