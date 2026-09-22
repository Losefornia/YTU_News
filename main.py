# -*- coding: utf-8 -*-
import asyncio
import json
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timedelta, date

from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain, Image
from astrbot.api.star import Context, Star, register, StarTools
from astrbot.api import logger

from . import db
from . import spider

TEMPLATE_DIR = Path(__file__).parent / "templates"
DATA_DIR = StarTools.get_data_dir("astrbot_plugin_ytunews")
SUB_FILE = DATA_DIR / "subs.json"

CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]
CATEGORY_LIMIT = {"重要": 20, "科研竞赛": 15, "研究生": 5, "其他": 20}
NEW_DAYS = 3

# ===== 抓取：早 8 点到晚 8 点，每 3 小时 =====
FETCH_TIMES = [(8, 0), (11, 0), (14, 0), (17, 0), (20, 0)]
CLEANUP_DAYS = 180

# ===== 定时推送：每天中午 12 点 =====
PUSH_TIMES = [(12, 0)]
PUSH_LIMIT = 10

# ===== 群聊白名单 =====
ALLOWED_GROUPS = [
    "default_1905605993:GroupMessage:C4DA56E7167E4824E8E2307771CF8EAA",   # 新闻群
    "default_1905605993:GroupMessage:E13B720565366E510BCBCA5E16BA84E0",   # 测试群
]


def is_allowed(event: AstrMessageEvent) -> bool:
    if not ALLOWED_GROUPS:
        return True
    return event.unified_msg_origin in ALLOWED_GROUPS


_sub_lock = asyncio.Lock()
_subs_cache = None


# ==================== 订阅读写 ====================

def load_subs():
    global _subs_cache
    if _subs_cache is not None:
        return _subs_cache
    if SUB_FILE.exists():
        try:
            data = json.loads(SUB_FILE.read_text(encoding="utf-8"))
            _subs_cache = data if isinstance(data, dict) else {}
        except Exception:
            _subs_cache = {}
    else:
        _subs_cache = {}
    return _subs_cache


def save_subs(data):
    global _subs_cache
    _subs_cache = data
    try:
        SUB_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[ytunews] 保存订阅失败: {e}")


# ==================== 发送重试 ====================

async def _send_with_retry(context, umo, chain, retries=2, delay=2):
    for i in range(retries):
        try:
            await context.send_message(umo, chain)
            return True
        except Exception as e:
            if i == retries - 1:
                logger.error(f"[ytunews] 推送失败 {umo}: {e}")
                return False
            logger.warning(f"[ytunews] 推送重试 {i+1}/{retries} {umo}: {e}")
            await asyncio.sleep(delay)
    return False


# ==================== 工具函数 ====================

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


def calc_base_size(n):
    if n <= 10:
        return 34
    if n <= 20:
        return 32
    if n <= 30:
        return 30
    return 28


def _next_run_from_list(times):
    now = datetime.now()
    candidates = []
    for h, m in times:
        t = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if t <= now:
            t += timedelta(days=1)
        candidates.append(t)
    return min(candidates)


def build_ordered(items):
    today = datetime.now().date()
    new_cutoff = today - timedelta(days=NEW_DAYS)
    month_cutoff = today - timedelta(days=30)

    groups = defaultdict(list)
    for it in items:
        groups[it.get("category", "其他")].append(it)

    for cat in groups:
        groups[cat].sort(
            key=lambda x: x["date"] if isinstance(x["date"], date) else date.min,
            reverse=True,
        )
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


# ==================== 插件主体 ====================

@register("astrbot_plugin_ytunews", "youwas936-design", "烟大新闻", "1.0.0", "")
class YtuNewsPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        db.init_db()
        self._fetch_task = None
        self._push_task = None
        self._render_lock = asyncio.Lock()
        self._last_refresh = {}
        self._refresh_cooldown = 5400

    async def initialize(self):
        logger.info("✅ 烟大新闻插件已加载")
        self._fetch_task = asyncio.create_task(self._fetch_loop())
        self._push_task = asyncio.create_task(self._push_loop())
        self._fetch_task.add_done_callback(self._on_task_done)
        self._push_task.add_done_callback(self._on_task_done)

    def _on_task_done(self, task):
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.error(f"[ytunews] 后台任务异常退出: {exc}", exc_info=exc)

    async def terminate(self):
        for t in (self._fetch_task, self._push_task):
            if t:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass
        await spider.close_client()
        db.close_conn()
        logger.info("👋 烟大新闻插件已卸载")

    # ==================== 抓取循环 ====================

    async def _fetch_loop(self):
        await asyncio.sleep(10)
        await self._do_fetch("首次")
        while True:
            try:
                target = _next_run_from_list(FETCH_TIMES)
                wait = (target - datetime.now()).total_seconds()
                logger.info(f"[ytunews] 下次抓取：{target:%Y-%m-%d %H:%M:%S}")
                await asyncio.sleep(wait)
                await self._do_fetch("定时")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[ytunews] 抓取循环异常: {e}")
                await asyncio.sleep(60)

    async def _do_fetch(self, tag: str):
        try:
            items = await spider.crawl_all()
            inserted = db.save_news(items)
            deleted = db.cleanup_old(days=CLEANUP_DAYS)
            if deleted:
                db.checkpoint()
            logger.info(
                f"[ytunews] {tag}抓取 {len(items)} 条，"
                f"新写入 {inserted} 条，清理 {deleted} 条"
            )
            if inserted > 0:
                asyncio.create_task(self._push_important())
        except Exception as e:
            logger.warning(f"[ytunews] {tag}抓取异常: {e}")

    # ==================== 重要通知立即推 ====================

    async def _push_important(self):
        now = datetime.now()
        last_important = db.get_last_important_push()
        since = last_important if last_important else (now - timedelta(hours=3))
        date_cutoff = (now - timedelta(days=1)).date()

        new_items = db.query_unpushed(since, date_cutoff=date_cutoff, limit=10)
        important_items = [it for it in new_items if it.get("category") == "重要"]
        if not important_items:
            return

        today = now.strftime("%m月%d日 %H:%M")
        lines = [f"⚠️ 重要通知（{today}）", ""]
        for i, it in enumerate(important_items, 1):
            title = it["title"]
            if len(title) > 30:
                title = title[:30] + "…"
            lines.append(f"{i}. {title}（{it['site']}）")
            lines.append(f"   {it['url']}")
        text = "\n".join(lines)

        subs = load_subs()
        if not subs:
            return

        async def _send_one(umo, uids):
            if ALLOWED_GROUPS and umo not in ALLOWED_GROUPS:
                return
            try:
                body_chain = MessageChain(chain=[Plain(text)])
                await _send_with_retry(self.context, umo, body_chain)

                if uids:
                    for i in range(0, len(uids), 50):
                        batch = uids[i:i + 50]
                        at_tags = " ".join(f'<qqbot-at-user id="{u}" />' for u in batch)
                        at_chain = MessageChain(chain=[Plain(at_tags)])
                        await _send_with_retry(self.context, umo, at_chain)
                        await asyncio.sleep(1)
                logger.info(f"[ytunews] 重要推送到 {umo}，@ {len(uids)} 人")
            except Exception as e:
                logger.error(f"[ytunews] 重要推送失败 {umo}: {e}")

        await asyncio.gather(*[_send_one(u, ids) for u, ids in subs.items()])
        db.mark_pushed([it["id"] for it in important_items])
        db.set_last_important_push(now)

    # ==================== 中午定时推送 ====================

    async def _push_loop(self):
        while True:
            try:
                target = _next_run_from_list(PUSH_TIMES)
                wait = (target - datetime.now()).total_seconds()
                logger.info(f"[ytunews] 下次推送：{target:%Y-%m-%d %H:%M:%S}")
                await asyncio.sleep(wait)
                await self._push_daily()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"[ytunews] 推送循环异常: {e}")
                await asyncio.sleep(60)

    async def _push_daily(self):
        now = datetime.now()
        last_push = db.get_last_push()
        since = last_push if last_push else (now - timedelta(days=1))
        date_cutoff = (now - timedelta(days=1)).date()

        new_items = db.query_unpushed(since, date_cutoff=date_cutoff, limit=PUSH_LIMIT)
        today = now.strftime("%m月%d日")

        if new_items:
            has_new = True
            new_count = db.count_unpushed(since, date_cutoff=date_cutoff)
            lines = [f"📢 烟大新闻（{today}）· 新增 {new_count} 条", ""]
            for i, it in enumerate(new_items, 1):
                title = it["title"]
                if len(title) > 30:
                    title = title[:30] + "…"
                cat = it.get("category", "其他")
                lines.append(f"{i}. [{cat}] {title}（{it['site']}）")
                lines.append(f"   {it['url']}")
            if new_count > len(new_items):
                lines.append(f"   …等共 {new_count} 条")
            lines.append("")
            lines.append("📋 完整列表见图片 ↓")
        else:
            has_new = False
            lines = [
                f"📢 烟大新闻（{today}）",
                "",
                "今日无新增通知。",
                "",
                "📋 完整列表见图片 ↓",
            ]

        text = "\n".join(lines)
        img_url = await self._render_news_image()

        subs = load_subs()
        if not subs:
            logger.info("[ytunews] 无订阅者，跳过推送")
            return

        async def _send_one(umo, uids):
            if ALLOWED_GROUPS and umo not in ALLOWED_GROUPS:
                return
            try:
                if img_url:
                    body_chain = MessageChain(chain=[Plain(text), Image.fromURL(img_url)])
                else:
                    body_chain = MessageChain(chain=[Plain(text)])
                await _send_with_retry(self.context, umo, body_chain)

                if has_new and uids:
                    for i in range(0, len(uids), 50):
                        batch = uids[i:i + 50]
                        at_tags = " ".join(f'<qqbot-at-user id="{u}" />' for u in batch)
                        at_chain = MessageChain(chain=[Plain(at_tags)])
                        await _send_with_retry(self.context, umo, at_chain)
                        await asyncio.sleep(1)

                logger.info(
                    f"[ytunews] 定时推送到 {umo}，"
                    f"{'@' if has_new else '普通'}，@ {len(uids)} 人"
                )
            except Exception as e:
                logger.error(f"[ytunews] 定时推送失败 {umo}: {e}")

        await asyncio.gather(*[_send_one(u, ids) for u, ids in subs.items()])

        db.set_last_push(now)
        if new_items:
            db.mark_pushed([it["id"] for it in new_items])

    # ==================== 渲染 ====================

    async def _render_news_image(self, days: int = None):
        async with self._render_lock:
            try:
                items = db.query_news(days)
            except Exception as e:
                logger.error(f"[ytunews] 查询新闻失败: {e}")
                return None

            if not items:
                return None

            for it in items:
                it["date"] = normalize_date(it.get("date"))

            ordered, groups = build_ordered(items)

            for cat in groups:
                for it in groups[cat]:
                    if isinstance(it["date"], date):
                        it["date"] = it["date"].strftime("%Y-%m-%d")
                    else:
                        it["date"] = ""

            try:
                tmpl = (TEMPLATE_DIR / "news.html").read_text(encoding="utf-8")
            except Exception as e:
                logger.error(f"[ytunews] 读取模板失败: {e}")
                return None

            data = {
                "title": "全部新闻",
                "total": len(ordered),
                "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
                "groups": {c: groups.get(c, []) for c in CATEGORY_ORDER},
                "base_size": calc_base_size(len(ordered)),
                "footer_note": "数据来源于烟台大学各学院官网，仅供参考",
                "douyin_id": "47780260687",
            }

            try:
                return await self.html_render(tmpl, data)
            except Exception as e:
                logger.error(f"[ytunews] 渲染失败: {e}")
                return None

    # ==================== 命令 ====================

    @filter.command("订阅")
    async def subscribe(self, event: AstrMessageEvent):
        if not is_allowed(event):
            return
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())

        async with _sub_lock:
            subs = load_subs()
            if umo not in subs:
                subs[umo] = []
            if uid in subs[umo]:
                return
            subs[umo].append(uid)
            save_subs(subs)
        yield event.plain_result("✅ 已订阅烟大新闻，有新通知会 @ 你。")

    @filter.command("取消订阅")
    async def unsubscribe(self, event: AstrMessageEvent):
        if not is_allowed(event):
            return
        umo = event.unified_msg_origin
        uid = str(event.get_sender_id())

        async with _sub_lock:
            subs = load_subs()
            if umo not in subs or uid not in subs[umo]:
                yield event.plain_result("你还没订阅。")
                return
            subs[umo].remove(uid)
            if not subs[umo]:
                del subs[umo]
            save_subs(subs)
        yield event.plain_result("已取消订阅。")

    @filter.command("新闻")
    async def news(self, event: AstrMessageEvent):
        if not is_allowed(event):
            return

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
            items = db.query_news(days=7)
            if not items:
                yield event.plain_result("暂无新闻。")
                return
            lines = ["📢 最近 7 天新闻（图片渲染失败，文字版）：", ""]
            for it in items[:15]:
                lines.append(f"· {it['title']}（{it['site']}）")
                lines.append(f"  {it['url']}")
            yield event.plain_result("\n".join(lines))
            return
        yield event.image_result(img_url)

    @filter.command("搜索")
    async def search(self, event: AstrMessageEvent, keyword: str = None):
        if not is_allowed(event):
            return
        if not keyword:
            yield event.plain_result("用法：/搜索 关键词")
            return
        rows = db.search_news(keyword, limit=10)
        if not rows:
            yield event.plain_result(f"没有找到包含「{keyword}」的新闻。")
            return
        lines = [f"🔍 包含「{keyword}」的新闻：", ""]
        for it in rows:
            lines.append(f"· {it['title']}（{it['site']} {it['date'] or '无日期'}）")
            lines.append(f"  {it['url']}")
        yield event.plain_result("\n".join(lines))

    @filter.command("刷新")
    async def refresh(self, event: AstrMessageEvent):
        if not is_allowed(event):
            return
        if not event.is_admin():
            return

        umo = event.unified_msg_origin
        now_ts = datetime.now().timestamp()
        last = self._last_refresh.get(umo, 0)
        if now_ts - last < self._refresh_cooldown:
            left = int(self._refresh_cooldown - (now_ts - last))
            m, s = divmod(left, 60)
            yield event.plain_result(f"刷新太频繁，请 {m} 分 {s} 秒后再试。")
            return
        self._last_refresh[umo] = now_ts

        yield event.plain_result("正在抓取…")
        try:
            items = await spider.crawl_all()
            inserted = db.save_news(items)
            yield event.plain_result(
                f"抓取完成：共 {len(items)} 条，新写入 {inserted} 条，"
                f"库内总计 {db.count_all()} 条"
            )
        except Exception as e:
            logger.error(f"[ytunews] 手动刷新失败: {e}")
            yield event.plain_result(f"抓取失败：{e}")

    @filter.command("统计")
    async def stats(self, event: AstrMessageEvent):
        if not is_allowed(event):
            return
        rows = db.site_stats()
        if not rows:
            yield event.plain_result("暂无数据。")
            return
        lines = ["📊 站点统计：", ""]
        for r in rows:
            lines.append(f"{r['site']}：{r['total']} 条，最近 {r['last_date'] or '无'}")
        yield event.plain_result("\n".join(lines))

    @filter.command("测试推送")
    async def test_push(self, event: AstrMessageEvent):
        if not is_allowed(event):
            return
        await self._push_daily()
        yield event.plain_result("已触发一次推送，去群里看看。")
