# -*- coding: utf-8 -*-
import sqlite3
import threading
import time
from pathlib import Path
from datetime import datetime, timedelta, date

from astrbot.api.star import StarTools

DATA_DIR = StarTools.get_data_dir("astrbot_plugin_ytunews")
DB_PATH = DATA_DIR / "news.db"

_local = threading.local()

# 搜索计数缓存
_count_cache = {}
_COUNT_TTL = 300   # 5 分钟


def get_conn():
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
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_news_pushed_created
        ON news(pushed, created_at)
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_date ON news(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_news_title ON news(title)")
    conn.commit()


# ===== KV =====

def get_kv(key):
    row = get_conn().execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_kv(key, value):
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value)
    )
    conn.commit()


def get_last_push():
    row = get_conn().execute("SELECT value FROM kv WHERE key = 'last_push'").fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["value"])
    except ValueError:
        return None


def set_last_push(dt):
    set_kv("last_push", dt.isoformat())


def get_last_important_push():
    row = get_conn().execute(
        "SELECT value FROM kv WHERE key = 'last_important_push'"
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["value"])
    except ValueError:
        return None


def set_last_important_push(dt):
    set_kv("last_important_push", dt.isoformat())


# ===== news 写入 =====

def save_news(items):
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

def query_news(days=None):
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


def search_news(keyword, limit=10, offset=0, days=730):
    """搜索标题，支持多关键词、分页、日期范围"""
    keywords = keyword.split()
    if not keywords:
        return []
    conn = get_conn()
    conditions = " AND ".join(["title LIKE ?"] * len(keywords))
    params = [f"%{kw}%" for kw in keywords]

    if days:
        cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
        sql = (
            f"SELECT * FROM news WHERE {conditions} AND date >= ? "
            f"ORDER BY date DESC, id DESC LIMIT ? OFFSET ?"
        )
        params.extend([cutoff, limit, offset])
    else:
        sql = (
            f"SELECT * FROM news WHERE {conditions} "
            f"ORDER BY date DESC, id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])

    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def count_search(keyword, days=730):
    """统计匹配数，带缓存"""
    key = (keyword, days)
    cached = _count_cache.get(key)
    if cached:
        ts, n = cached
        if time.time() - ts < _COUNT_TTL:
            return n

    keywords = keyword.split()
    if not keywords:
        return 0
    conn = get_conn()
    conditions = " AND ".join(["title LIKE ?"] * len(keywords))
    params = [f"%{kw}%" for kw in keywords]

    if days:
        cutoff = (datetime.now().date() - timedelta(days=days)).isoformat()
        sql = f"SELECT COUNT(*) FROM news WHERE {conditions} AND date >= ?"
        params.append(cutoff)
    else:
        sql = f"SELECT COUNT(*) FROM news WHERE {conditions}"

    n = conn.execute(sql, params).fetchone()[0]
    _count_cache[key] = (time.time(), n)
    return n


def clear_search_cache():
    _count_cache.clear()


def site_stats():
    rows = get_conn().execute(
        "SELECT site, COUNT(*) as total, MAX(date) as last_date "
        "FROM news GROUP BY site ORDER BY total DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def query_unpushed(since, date_cutoff=None, limit=10):
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


def count_unpushed(since, date_cutoff=None):
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


def cleanup_old(days=180):
    cutoff_date = (datetime.now().date() - timedelta(days=days)).isoformat()
    cutoff_ts = (datetime.now() - timedelta(days=days)).isoformat()
    conn = get_conn()
    conn.execute("BEGIN")
    n1 = conn.execute(
        "DELETE FROM news WHERE date IS NOT NULL AND date < ?", (cutoff_date,)
    ).rowcount
    n2 = conn.execute(
        "DELETE FROM news WHERE date IS NULL AND created_at < ?", (cutoff_ts,)
    ).rowcount
    conn.execute("COMMIT")
    return (n1 or 0) + (n2 or 0)


def checkpoint():
    conn = get_conn()
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
    except Exception:
        pass
