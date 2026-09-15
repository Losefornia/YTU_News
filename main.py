# -*- coding: utf-8 -*-
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star
from astrbot.api import logger

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from db import init_db, save_news, query_news
from spider import crawl_all

TEMPLATE_DIR = Path(__file__).parent / "templates"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
UMO_FILE = DATA_DIR / "umo.json"

CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]
CATEGORY_LIMIT = {"重要": 10, "科研竞赛": 10, "研究生": 5, "其他": 10}
NEW_DAYS = 3


def load_umo():
    if UMO_FILE.exists():
        try:
            return json.loads(UMO_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


def save_umo(umo):
    lst = load_umo()
    if umo not in lst:
        lst.append(umo)
        UMO_FILE.write_text(json.dumps(lst, ensure_ascii=False, indent=2),
                            encoding="utf-8")


def build_ordered(items):
    today = datetime.now().date()
    new_cutoff = today - timedelta(days=NEW_DAYS)

    groups = defaultdict(list)
    for it in items:
        groups[it.get("category", "其他")].append(it)

    for cat in groups:
        groups[cat].sort(key=lambda x: x["date"] or "", reverse=True)
        limit = CATEGORY_LIMIT.get(cat, 10)
        groups[cat] = groups[cat][:limit]
        for it in groups[cat]:
            if it["date"]:
                try:
                    d = datetime.fromisoformat(it["date"]).date()
                    it["is_new"] = d >= new_cutoff
                except ValueError:
                    it["is_new"] = False
            else:
                it["is_new"] = False

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
        return 22
    if n <= 20:
        return 20
    if n <= 30:
        return 18
    return 16


class YtuNewsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        init_db()

        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self.scheduler.add_job(
            self.daily_job,
            CronTrigger(hour=20, minute=42),   # ← 定时时间改这里
            id="ytunews_daily",
            replace_existing=True,
        )
        self.scheduler.start()

    async def daily_job(self):
        try:
            items = await crawl_all()
            inserted = save_news(items)
            logger.info(f"[ytunews] 抓取 {len(items)} 条，新写入 {inserted} 条")
        except Exception as e:
            logger.error(f"[ytunews] 抓取失败: {e}")
            return

        img_url = await self._render_news_image()
        if not img_url:
            logger.warning("[ytunews] 渲染失败，跳过推送")
            return

        umos = load_umo()
        for umo in umos:
            try:
                chain = MessageChain().message("📢 烟大新闻").url_image(img_url)
                await self.context.send_message(umo, chain)
                logger.info(f"[ytunews] 推送到 {umo}")
            except Exception as e:
                logger.error(f"[ytunews] 推送失败 {umo}: {e}")

    async def _render_news_image(self):
        items = query_news(3)
        for it in items:
            if it["date"]:
                try:
                    it["date"] = datetime.fromisoformat(it["date"]).date()
                except ValueError:
                    it["date"] = None
            else:
                it["date"] = None

        if not items:
            return None

        ordered, groups = build_ordered(items)
        tmpl = (TEMPLATE_DIR / "news.html").read_text(encoding="utf-8")
        data = {
            "days": 3,
            "total": len(ordered),
            "now": datetime.now().strftime("%m-%d %H:%M"),
            "groups": {c: groups.get(c, []) for c in CATEGORY_ORDER},
            "base_size": calc_base_size(len(ordered)),
        }
        return await self.html_render(tmpl, data)

    @filter.command("新闻")
    async def news(self, event: AstrMessageEvent):
        save_umo(event.unified_msg_origin)
        img_url = await self._render_news_image()
        if not img_url:
            yield event.plain_result("最近没有新闻。")
            return
        yield event.image_result(img_url)

    @filter.command("测试推送")
    async def test_push(self, event: AstrMessageEvent):
        await self.daily_job()
        yield event.plain_result("已触发一次推送，去群里看看。")

    @filter.command("仅管理员推送")
    async def admin_only_push(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin
        img_url = await self._render_news_image()
        if not img_url:
            yield event.plain_result("最近没有新闻。")
            return
        try:
            chain = MessageChain().message("📢 烟大新闻（管理员）").url_image(img_url)
            await self.context.send_message(umo, chain)
            yield event.plain_result("已推送给当前会话。")
        except Exception as e:
            yield event.plain_result(f"推送失败：{e}")
