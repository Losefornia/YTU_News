# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta

DATA_DIR = Path("data")
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
    conn.commit()
    conn.close()


def save_news(items):
    """批量写入，url 唯一，重复跳过。返回实际新增条数"""
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
                    it["date"].isoformat() if it["date"] else None,
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


def query_news(days: int):
    """查最近 N 天的新闻，按日期倒序"""
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    conn = get_conn()
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