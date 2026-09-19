# -*- coding: utf-8 -*-
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, date

from astrbot.api.star import StarTools

# 数据目录改到 plugin_data，重装不丢
DATA_DIR = StarTools.get_data_dir("astrbot_plugin_ytunews")
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
            created_at TEXT,
            pushed INTEGER DEFAULT 0
        )
    """)
    # 兼容老库：没有 pushed 字段就加上
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(news)").fetchall()]
    if "pushed" not in cols:
        conn.execute("ALTER TABLE news ADD COLUMN pushed INTEGER DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_pushed ON news(pushed)")
    conn.commit()
    conn.close()


def save_news(items):
    conn = get_conn()
    before = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    for it in items:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO news "
                "(title, url, date, site, category, created_at, pushed) "
                "VALUES (?, ?, ?, ?, ?, ?, 0)",
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
            "SELECT * FROM news ORDER BY date DESC, id DESC"
        ).fetchall()
    else:
        cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT * FROM news WHERE date >= ? ORDER BY date DESC, id DESC",
            (cutoff,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def count_all():
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
    conn.close()
    return n


def get_all_urls():
    """一次性查出所有已存在的 URL，供列表页提前停止用。"""
    conn = get_conn()
    rows = conn.execute("SELECT url FROM news").fetchall()
    conn.close()
    return {r["url"] for r in rows}


def cleanup_old(days: int = 180):
    """清理超过 N 天的旧新闻，date 为 NULL 的保留。"""
    cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
    conn = get_conn()
    n = conn.execute(
        "DELETE FROM news WHERE date IS NOT NULL AND date < ?", (cutoff,)
    ).rowcount
    conn.commit()
    conn.close()
    return n


def search_news(keyword, limit=10):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM news WHERE title LIKE ? ORDER BY date DESC, id DESC LIMIT ?",
        (f"%{keyword}%", limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def site_stats():
    conn = get_conn()
    rows = conn.execute(
        "SELECT site, COUNT(*) as total, MAX(date) as last_date "
        "FROM news GROUP BY site ORDER BY total DESC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
