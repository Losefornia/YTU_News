# -*- coding: utf-8 -*-
import asyncio
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, date

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger

from . import db
from . import spider

TEMPLATE_DIR = Path(__file__).parent / "templates"

# 【P0-1】数据目录改到 plugin_data
DATA_DIR = StarTools.get_data_dir("astrbot_plugin_ytunews")
UMO_FILE = DATA_DIR / "umo.json"

CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]
CATEGORY_LIMIT = {"重要": 20, "科研竞赛": 15, "研究生": 5, "其他": 20}
NEW_DAYS = 3

# ===== 抓取配置 =====
FETCH_HOUR = 8
FETCH_MINUTE = 0
NEW_ITEM_LIMIT = 5

# 【P1-5】订阅文件并发锁
_umo_lock = asyncio.Lock()


def load_umo():
    if UMO_FILE.exists():
        try:
            return json.loads(UMO_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


async def save_umo_async(umo):
    """【P1-5】加锁保护，避免并发写丢订阅者。"""
    async with _umo_lock:
        lst = load_umo()
        if umo not in lst:
            lst.append(umo)
            UMO_FILE.write_text(
                json.dumps(lst, ensure_ascii=False, indent=2), encoding="utf-8"
            )


def normalize_date(d):
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    if isinstance(d, str) and d:
        try:
            return datetime.fromisoformat(d).date()
        except ValueError:
            return None
    return None


def build_ordered(items):
    today = datetime.now().date()
    new_cutoff = today - timedelta(days=NEW_DAYS)
    month_cutoff = today - timedelta(days=30)

    groups = defaultdict(list)
    for it in items:
        groups[it.get("category", "其他")].append(it)

    for cat in groups:
        groups[cat].sort(key=lambda x: x["date"] or "", reverse=True)
        limit = CATEGORY_LIMIT.get(cat, 10)
        groups[cat] = groups[cat][:limit]
        for it in groups[cat]:
            d = it.get("date")
            if not isinstance(d, date):
                it["freshness"] = "none"
            elif d >= new_cutoff:
                it["freshness"] = "new"
            elif d >= month_cutoff:
                it["freshness"] = "recent"
            else:
                it["freshness"] = "old"

    ordered = []
    idx = 0
    for cat in CATEGORY_ORDER:
        for it in groups.get(cat, []):
            idx += 1
            it["idx"] = idx
            it["idx_str"] = f"{idx}."
            ordered.append(it)
    return ordered, groups


def calc_base_size(n):
    if n <= 10:
        return 34
    if n <= 20:
        return 32
    if n <= 30:
        return 30
    return 28


def _next_run(hour: int, minute: int) -> datetime:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


@register("astrbot_plugin_ytunews", "youwas936-design", "烟大新闻", "1.0.0", "")
class YtuNewsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        db.init_db()
        self._fetch_task = None

    async def initialize(self):
        logger.info("✅ 烟大新闻插件已加载")
        self._fetch_task = asyncio.create_task(self._fetch_loop())

    async def terminate(self):
        if self._fetch_task:
            self._fetch_task.cancel()
        logger.info("👋 烟大新闻插件已卸载")

    # ===== 抓取循环 =====
    async def _fetch_loop(self):
        await asyncio.sleep(10)
        try:
            items = await spider.crawl_all()
            inserted = db.save_news(items)
            deleted = db.cleanup_old(days=180)   # 【P1-6】
            logger.info(f"[ytunews] 首次抓取 {len(items)} 条，新写入 {inserted} 条，清理 {deleted} 条")
        except Exception as e:
            logger.warning(f"[ytunews] 首次抓取异常: {e}")

        while True:
            target = _next_run(FETCH_HOUR, FETCH_MINUTE)
            wait = (target - datetime.now()).total_seconds()
            logger.info(f"[ytunews] 下次抓取：{target:%Y-%m-%d %H:%M:%S}")
            await asyncio.sleep(wait)
            try:
                items = await spider.crawl_all()
                inserted = db.save_news(items)
                deleted = db.cleanup_old(days=180)
                logger.info(f"[ytunews] 抓取 {len(items)} 条，新写入 {inserted} 条，清理 {deleted} 条")
            except Exception as e:
                logger.warning(f"[ytunews] 抓取异常: {e}")

    async def _render_news_image(self, days: int = None):
        items = db.query_news(days)
        for it in items:
            it["date"] = normalize_date(it.get("date"))

        if not items:
            return None

        ordered, groups = build_ordered(items)

        for cat in groups:
            for it in groups[cat]:
                if isinstance(it["date"], date):
                    it["date"] = it["date"].strftime("%Y-%m-%d")
                else:
                    it["date"] = ""

        tmpl = (TEMPLATE_DIR / "news.html").read_text(encoding="utf-8")
        data = {
            "title": "全部新闻",
            "total": len(ordered),
            "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "groups": {c: groups.get(c, []) for c in CATEGORY_ORDER},
            "base_size": calc_base_size(len(ordered)),
            "footer_note": "数据来源于烟台大学各学院官网，仅供参考",
            "douyin_id": "47780260687",
        }
        return await self.html_render(tmpl, data)

    # ===== 命令 =====
    @filter.command("新闻")
    async def news(self, event: AstrMessageEvent):
        await save_umo_async(event.unified_msg_origin)

        # 【P0-2】库为空时自动抓一次
        if db.count_all() == 0:
            yield event.plain_result("首次使用，正在抓取新闻，请稍候…")
            try:
                items = await spider.crawl_all()
                db.save_news(items)
                logger.info(f"[ytunews] 命令触发抓取 {len(items)} 条")
            except Exception as e:
                logger.error(f"[ytunews] 命令触发抓取失败: {e}")
                yield event.plain_result(f"抓取失败：{e}")
                return

        img_url = await self._render_news_image()
        if not img_url:
            yield event.plain_result("暂无新闻。")
            return
        yield event.image_result(img_url)

    # 【P1-7】测试推送只推当前群，不遍历订阅者
    @filter.command("测试推送")
    async def test_push(self, event: AstrMessageEvent):
        img_url = await self._render_news_image()
        if not img_url:
            yield event.plain_result("暂无新闻可推送。")
            return
        yield event.image_result(img_url)
