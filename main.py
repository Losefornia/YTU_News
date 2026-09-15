# -*- coding: utf-8 -*-
import asyncio
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger

from . import db
from . import spider

TEMPLATE_DIR = Path(__file__).parent / "templates"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
UMO_FILE = DATA_DIR / "umo.json"

CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]
CATEGORY_LIMIT = {"重要": 10, "科研竞赛": 10, "研究生": 5, "其他": 10}
NEW_DAYS = 3
FETCH_INTERVAL = 24 * 3600          # 每 24 小时抓一次
PUSH_HOUR = 21                       # 每天 20 点推送
PUSH_MINUTE = 04                     # 20:42


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
        UMO_FILE.write_text(
            json.dumps(lst, ensure_ascii=False, indent=2), encoding="utf-8"
        )


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


@register("astrbot_plugin_ytunews", "youwas936-design", "烟大新闻", "1.0.0", "")
class YtuNewsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        db.init_db()
        self._fetch_task = None
        self._push_task = None

    async def initialize(self):
        logger.info("✅ 烟大新闻插件已加载")
        self._fetch_task = asyncio.create_task(self._fetch_loop())
        self._push_task = asyncio.create_task(self._push_loop())

    async def terminate(self):
        if self._fetch_task:
            self._fetch_task.cancel()
        if self._push_task:
            self._push_task.cancel()
        logger.info("👋 烟大新闻插件已卸载")

    # ---------- 抓取循环 ----------
    async def _fetch_loop(self):
        await asyncio.sleep(30)          # 启动后等 30 秒再抓
        while True:
            try:
                items = await spider.crawl_all()
                inserted = db.save_news(items)
                logger.info(f"[ytunews] 抓取 {len(items)} 条，新写入 {inserted} 条")
            except Exception as e:
                logger.warning(f"[ytunews] 抓取异常: {e}")
            await asyncio.sleep(FETCH_INTERVAL)

    # ---------- 推送循环 ----------
    async def _push_loop(self):
        while True:
            now = datetime.now()
            target = now.replace(
                hour=PUSH_HOUR, minute=PUSH_MINUTE, second=0, microsecond=0
            )
            if now >= target:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            await asyncio.sleep(wait)
            try:
                await self._do_push()
            except Exception as e:
                logger.error(f"[ytunews] 推送异常: {e}")

    async def _do_push(self):
        img_url = await self._render_news_image()
        if not img_url:
            logger.warning("[ytunews] 无内容可推送")
            return
        umos = load_umo()
        for umo in umos:
            try:
                chain = MessageChain().message("📢 烟大新闻").url_image(img_url)
                await self.context.send_message(umo, chain)
                logger.info(f"[ytunews] 已推送到 {umo}")
            except Exception as e:
                logger.error(f"[ytunews] 推送失败 {umo}: {e}")

    # ---------- 渲染图片 ----------
    async def _render_news_image(self, days: int = 3):
        items = db.query_news(days)
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
            "days": days,
            "total": len(ordered),
            "now": datetime.now().strftime("%m-%d %H:%M"),
            "groups": {c: groups.get(c, []) for c in CATEGORY_ORDER},
            "base_size": calc_base_size(len(ordered)),
        }
        return await self.html_render(tmpl, data)

    # ---------- 指令 ----------
    @filter.command("新闻")
    async def news(self, event: AstrMessageEvent):
        save_umo(event.unified_msg_origin)

        raw = event.message_str.replace("/新闻", "").strip()
        days = 3
        try:
            if "月" in raw:
                days = int(raw.replace("月", "")) * 30
            elif "天" in raw:
                days = int(raw.replace("天", ""))
        except ValueError:
            days = 3

        img_url = await self._render_news_image(days)
        if not img_url:
            yield event.plain_result(f"最近 {days} 天没有新闻。")
            return
        yield event.image_result(img_url)

    @filter.command("测试推送")
    async def test_push(self, event: AstrMessageEvent):
        await self._do_push()
        yield event.plain_result("已触发一次推送，去群里看看。")
