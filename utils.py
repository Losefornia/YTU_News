import logging
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.yaml"
BIND_FILE = BASE_DIR / "bind_sessions.json"

# ========== 统一日志 ==========
logger = logging.getLogger("YTU_News")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    fmt = logging.Formatter(
        "[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    handler.setFormatter(fmt)
    logger.addHandler(handler)

# ========== 会话持久化 ==========
def load_bind_sessions() -> set:
    """加载已绑定推送的会话ID"""
    if not BIND_FILE.exists():
        return set()
    try:
        with open(BIND_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()

def save_bind_sessions(sessions: set):
    """保存绑定的会话ID"""
    try:
        with open(BIND_FILE, "w", encoding="utf-8") as f:
            json.dump(list(sessions), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存绑定会话失败: {e}")
