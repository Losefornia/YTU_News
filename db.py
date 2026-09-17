# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "news.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
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
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at)")
    conn.commit()
    conn.close()


def save_news(items):
    conn = get_conn()
    before = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    for it in items:
        try:
            conn.execute(
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
        except Exception as e:
            print(f"[DB ERR] {it['url']}: {e}")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    conn.close()
    return after - before


def query_news(days: int = None):
    conn = get_conn()
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
    conn.close()
    return [dict(r) for r in rows]


def count_all():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    conn.close()
    return n


# ===== 新增：按时间点查询（用于推送"新增"） =====

def query_new_since(since: datetime, limit: int = 5):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM news WHERE created_at >= ? "
        "ORDER BY created_at DESC LIMIT ?",
        (since.isoformat(), limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_new_since(since: datetime) -> int:
    conn = get_conn()
    n = conn.execute(
        "SELECT COUNT(*) FROM news WHERE created_at >= ?",
        (since.isoformat(),),
    ).fetchone()[0]
    conn.close()
    return n
