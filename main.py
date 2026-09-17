# -*- coding: utf-8 -*-
import asyncio
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

from . import db, spider

TEMPLATE_DIR = Path(__file__).parent / "templates"
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
UMO_FILE = DATA_DIR / "umo.json"

CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]
CATEGORY_LIMIT = {"重要": 20, "科研竞赛": 15, "研究生": 5, "其他": 20}
CATEGORY_CLASS = {
    "重要": "important",
    "科研竞赛": "research",
    "研究生": "graduate",
    "其他": "other",
}
NEW_DAYS = 3

# ===== 定时配置 =====
FETCH_HOUR = 11
FETCH_MINUTE = 50      # 11:50 爬取
PUSH_HOUR = 11
PUSH_MINUTE = 59       # 11:59 推送
NEW_ITEM_LIMIT = 5     # 推送文案里最多列几条新增

_umo_lock = asyncio.Lock()


def load_umo() -> list[str]:
    if UMO_FILE.exists():
        try:
            return json.loads(UMO_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[ytunews] 读取 umo 失败: {e}")
    return []


async def save_umo(umo: str) -> None:
    async with _umo_lock:
        lst = load_umo()
        if umo not in lst:
            lst.append(umo)
            UMO_FILE.write_text(
                json.dumps(lst, ensure_ascii=False, indent=2), encoding="utf-8"
            )


def normalize_date(d) -> Optional[date]:
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


def build_ordered(items: list[dict]):
    today = date.today()
    new_cutoff = today - timedelta(days=NEW_DAYS)
    month_cutoff = today - timedelta(days=30)

    groups: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        groups[it.get("category", "其他")].append(it)

    for cat, lst in groups.items():
        lst.sort(key=lambda x: x.get("date") or date.min, reverse=True)
        lst = lst[: CATEGORY_LIMIT.get(cat, 10)]
        groups[cat] = lst
        for it in lst:
            d = it.get("date")
            if not isinstance(d, date):
                it["freshness"] = "none"
            elif d >= new_cutoff:
                it["freshness"] = "new"
            elif d >= month_cutoff:
                it["freshness"] = "recent"
            else:
                it["freshness"] = "old"

    ordered: list[dict] = []
    idx = 0
    for cat in CATEGORY_ORDER:
        for it in groups.get(cat, []):
            idx += 1
            it["idx"] = idx
            it["idx_str"] = f"{idx}."
            ordered.append(it)
    return ordered, groups


def calc_base_size(n: int) -> int:
    if n <= 10:
        return 34
    if n <= 20:
        return 32
    if n <= 30:
        return 30
    return 28


def _next_run(hour: int, minute: int) -> datetime:
    """返回下一次 hour:minute 的时间点。"""
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
        self._fetch_task: Optional[asyncio.Task] = None
        self._push_task: Optional[asyncio.Task] = None

    async def initialize(self):
        logger.info("✅ 烟大新闻插件已加载")
        self._fetch_task = asyncio.create_task(self._fetch_loop())
        self._push_task = asyncio.create_task(self._push_loop())

    async def terminate(self):
        for t in (self._fetch_task, self._push_task):
            if t and not t.done():
                t.cancel()
        await asyncio.gather(
            *(t for t in (self._fetch_task, self._push_task) if t),
            return_exceptions=True,
        )
        logger.info("👋 烟大新闻插件已卸载")

    # ===== 抓取循环：每天 11:50 =====
    async def _fetch_loop(self):
        # 启动后先抓一次，保证首次有数据
        await asyncio.sleep(30)
        try:
            items = await spider.crawl_all()
            inserted = db.save_news(items)
            logger.info(f"[ytunews] 首次抓取 {len(items)} 条，新写入 {inserted} 条")
        except asyncio.CancelledError:
            raise
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
                logger.info(f"[ytunews] 抓取 {len(items)} 条，新写入 {inserted} 条")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning(f"[ytunews] 抓取异常: {e}")

    # ===== 推送循环：每天 11:59 =====
    async def _push_loop(self):
        while True:
            target = _next_run(PUSH_HOUR, PUSH_MINUTE)
            wait = (target - datetime.now()).total_seconds()
            logger.info(f"[ytunews] 下次推送：{target:%Y-%m-%d %H:%M:%S}")
            await asyncio.sleep(wait)

            try:
                await self._do_push()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[ytunews] 推送异常: {e}")

    async def _do_push(self):
        img_url = await self._render_news_image()
        if not img_url:
            logger.warning("[ytunews] 无内容可推送")
            return

        now = datetime.now()
        today = now.strftime("%m月%d日")

        # 统计范围：昨天 11:59 ~ 今天 11:59
        since = (now - timedelta(days=1)).replace(
            hour=PUSH_HOUR, minute=PUSH_MINUTE, second=0, microsecond=0
        )
        new_items = db.query_new_since(since, limit=NEW_ITEM_LIMIT)
        new_count = db.count_new_since(since)

        lines = [
            f"📢 烟大新闻（{today}）"
            f"· 统计时间 {since:%m月%d日 %H:%M}"
            f"-{now:%m月%d日 %H:%M}"
            f"· 新增 {new_count} 条",
            "",
        ]

        if new_items:
            lines.append("🆕 新增：")
            for i, it in enumerate(new_items, 1):
                title = it["title"]
                if len(title) > 30:
                    title = title[:30] + "…"
                lines.append(f"{i}. {title}（{it['site']}）")
                lines.append(f"   {it['url']}")
            if new_count > len(new_items):
                lines.append(f"   …等共 {new_count} 条")
            lines.append("")

        lines.append("📋 全部新闻见图片 ↓")
        text = "\n".join(lines)
        chain = MessageChain().message(text).url_image(img_url)

        umos = load_umo()
        if not umos:
            logger.info("[ytunews] 无订阅者，跳过推送")
            return
        for umo in umos:
            try:
                await self.context.send_message(umo, chain)
                logger.info(f"[ytunews] 已推送到 {umo}")
            except Exception as e:
                logger.error(f"[ytunews] 推送失败 {umo}: {e}")

    async def _render_news_image(self, days: Optional[int] = None) -> Optional[str]:
        items = db.query_news(days)
        for it in items:
            it["date"] = normalize_date(it.get("date"))

        if not items:
            return None

        ordered, groups = build_ordered(items)

        # 给模板准备字符串日期
        for cat, lst in groups.items():
            for it in lst:
                d = it.get("date")
                it["date_str"] = d.strftime("%Y-%m-%d") if isinstance(d, date) else ""

        tmpl = (TEMPLATE_DIR / "news.html").read_text(encoding="utf-8")
        data = {
            "title": "不含学工系统新闻",
            "total": len(ordered),
            "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "groups": [(c, groups.get(c, [])) for c in CATEGORY_ORDER],
            "cat_classes": CATEGORY_CLASS,
            "base_size": calc_base_size(len(ordered)),
            "footer_note": "数据来源于烟台大学官网，仅供参考",
            "douyin_id": "47780260687",
        }
        return await self.html_render(tmpl, data)

    @filter.command("新闻")
    async def news(self, event: AstrMessageEvent):
        await save_umo(event.unified_msg_origin)
        img_url = await self._render_news_image()
        if not img_url:
            yield event.plain_result("暂无新闻。")
            return
        yield event.image_result(img_url)

    @filter.command("测试推送")
    async def test_push(self, event: AstrMessageEvent):
        await self._do_push()
        yield event.plain_result("已触发一次推送，去群里看看。")
