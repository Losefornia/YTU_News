# -*- coding: utf-8 -*-
import sqlite3
import threading
from pathlib import Path
from datetime import datetime, timedelta, date

from astrbot.api.star import StarTools

DATA_DIR = StarTools.get_data_dir("astrbot_plugin_ytunews")
DB_PATH = DATA_DIR / "news.db"

_local = threading.local()


def get_conn():
    """线程本地常驻连接。"""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, timeout=20)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA busy_timeout=5000;")
        conn.execute("PRAGMA cache_size=-8000;")
        conn.execute("PRAGMA temp_store=MEMORY;")
        conn.row_factory = sqlite3.Row
        _local.conn = conn
    return conn


def close_conn():
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        _local.conn = None


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
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(news)").fetchall()]
    if "pushed" not in cols:
        conn.execute("ALTER TABLE news ADD COLUMN pushed INTEGER DEFAULT 0")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS kv (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_created ON news(created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_pushed ON news(pushed)")
    # 复合索引：覆盖 query_unpushed / count_unpushed
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_pushed_created
        ON news(pushed, created_at)
    """)
    # 按 date 排序 / 清理
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_date ON news(date)")
    conn.commit()


# ===== kv =====

def get_last_push():
    row = get_conn().execute(
        "SELECT value FROM kv WHERE key = 'last_push'"
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["value"])
    except ValueError:
        return None


def set_last_push(dt: datetime):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO kv (key, value) VALUES ('last_push', ?)",
        (dt.isoformat(),),
    )
    conn.commit()


# ===== news 写入 =====

def save_news(items):
    """批量写入，返回新增条数。"""
    if not items:
        return 0
    rows = [
        (
            it["title"],
            it["url"],
            it["date"].isoformat() if it.get("date") else None,
            it["site"],
            it.get("category", "其他"),
            datetime.now().isoformat(),
        )
        for it in items
    ]
    conn = get_conn()
    conn.execute("BEGIN")
    cur = conn.executemany(
        "INSERT OR IGNORE INTO news "
        "(title, url, date, site, category, created_at, pushed) "
        "VALUES (?, ?, ?, ?, ?, ?, 0)",
        rows,
    )
    inserted = cur.rowcount if cur.rowcount and cur.rowcount >= 0 else 0
    conn.execute("COMMIT")
    return inserted


def mark_pushed(ids):
    if not ids:
        return
    conn = get_conn()
    placeholders = ",".join("?" * len(ids))
    conn.execute(f"UPDATE news SET pushed = 1 WHERE id IN ({placeholders})", list(ids))
    conn.commit()


# ===== news 查询 =====

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
    return [dict(r) for r in rows]


def count_all():
    return get_conn().execute("SELECT COUNT(*) FROM news").fetchone()[0]


def get_all_urls():
    rows = get_conn().execute("SELECT url FROM news").fetchall()
    return {r["url"] for r in rows}


def search_news(keyword, limit=10):
    rows = get_conn().execute(
        "SELECT * FROM news WHERE title LIKE ? ORDER BY date DESC, id DESC LIMIT ?",
        (f"%{keyword}%", limit),
    ).fetchall()
    return [dict(r) for r in rows]


def site_stats():
    rows = get_conn().execute(
        "SELECT site, COUNT(*) as total, MAX(date) as last_date "
        "FROM news GROUP BY site ORDER BY total DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def query_unpushed(since: datetime, date_cutoff: date = None, limit: int = 10):
    conn = get_conn()
    if date_cutoff:
        rows = conn.execute(
            "SELECT * FROM news WHERE created_at >= ? "
            "AND (pushed IS NULL OR pushed = 0) "
            "AND (date IS NULL OR date >= ?) "
            "ORDER BY created_at DESC LIMIT ?",
            (since.isoformat(), date_cutoff.isoformat(), limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM news WHERE created_at >= ? "
            "AND (pushed IS NULL OR pushed = 0) "
            "ORDER BY created_at DESC LIMIT ?",
            (since.isoformat(), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def count_unpushed(since: datetime, date_cutoff: date = None) -> int:
    conn = get_conn()
    if date_cutoff:
        n = conn.execute(
            "SELECT COUNT(*) FROM news WHERE created_at >= ? "
            "AND (pushed IS NULL OR pushed = 0) "
            "AND (date IS NULL OR date >= ?)",
            (since.isoformat(), date_cutoff.isoformat()),
        ).fetchone()[0]
    else:
        n = conn.execute(
            "SELECT COUNT(*) FROM news WHERE created_at >= ? "
            "AND (pushed IS NULL OR pushed = 0)",
            (since.isoformat(),),
        ).fetchone()[0]
    return n


# ===== 清理 =====

def cleanup_old(days: int = 180):
    """
    按 date 清理；date 为 NULL 的按 created_at 兜底清理。
    一次事务完成，返回删除总数。
    """
    cutoff_date = (datetime.now().date() - timedelta(days=days)).isoformat()
    cutoff_ts = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_conn()
    conn.execute("BEGIN")
    n1 = conn.execute(
        "DELETE FROM news WHERE date IS NOT NULL AND date < ?",
        (cutoff_date,),
    ).rowcount
    n2 = conn.execute(
        "DELETE FROM news WHERE date IS NULL AND created_at < ?",
        (cutoff_ts,),
    ).rowcount
    conn.execute("COMMIT")
    return (n1 or 0) + (n2 or 0)


def checkpoint():
    """回收 WAL，清理后调用。"""
    conn = get_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except Exception:
        pass
