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

# 模板保留在插件目录（重装会跟着更新）
TEMPLATE_DIR = Path(__file__).parent / "templates"

# 数据目录改到 plugin_data（重装不丢）
DATA_DIR = StarTools.get_data_dir("astrbot_plugin_ytunews")
UMO_FILE = DATA_DIR / "umo.json"

CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]
CATEGORY_LIMIT = {"重要": 20, "科研竞赛": 15, "研究生": 5, "其他": 20}
NEW_DAYS = 3

# ===== 抓取配置：早 8 点到晚 8 点，每 3 小时一次 =====
FETCH_TIMES = [(8, 0), (11, 0), (14, 0), (17, 0), (20, 0)]
CLEANUP_DAYS = 180

# 订阅文件并发锁
_umo_lock = asyncio.Lock()


# ==================== 工具函数 ====================

def load_umo():
    if UMO_FILE.exists():
        try:
            return json.loads(UMO_FILE.read_text(encoding="utf-8"))
        except Exception:
            return []
    return []


async def save_umo_async(umo):
    """加锁保护，避免并发写丢订阅者。"""
    async with _umo_lock:
        lst = load_umo()
        if umo not in lst:
            lst.append(umo)
            try:
                UMO_FILE.write_text(
                    json.dumps(lst, ensure_ascii=False, indent=2), encoding="utf-8"
                )
            except Exception as e:
                logger.warning(f"[ytunews] 保存订阅失败: {e}")


def normalize_date(d):
    """统一成 date 对象或 None。"""
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
    """按分类分组、组内按日期倒序，计算新鲜度。

    关键：排序 key 统一成 date 类型，None 用 date.min 代替，
    避免 date 和 str 比较导致 TypeError。
    """
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
        self._render_lock = asyncio.Lock()   # 防止并发渲染

    async def initialize(self):
        logger.info("✅ 烟大新闻插件已加载")
        self._fetch_task = asyncio.create_task(self._fetch_loop())

    async def terminate(self):
        if self._fetch_task:
            self._fetch_task.cancel()
            try:
                await self._fetch_task
            except asyncio.CancelledError:
                pass
        logger.info("👋 烟大新闻插件已卸载")

    # ==================== 抓取循环 ====================

    async def _fetch_loop(self):
        """启动后先抓一次，之后每天 8/11/14/17/20 点各抓一次。"""
        await asyncio.sleep(10)
        await self._do_fetch("首次")

        while True:
            target = _next_run_from_list(FETCH_TIMES)
            wait = (target - datetime.now()).total_seconds()
            logger.info(f"[ytunews] 下次抓取：{target:%Y-%m-%d %H:%M:%S}")
            await asyncio.sleep(wait)
            await self._do_fetch("定时")

    async def _do_fetch(self, tag: str):
        """抓取 + 入库 + 清理，统一异常处理。"""
        try:
            items = await spider.crawl_all()
            inserted = db.save_news(items)
            deleted = db.cleanup_old(days=CLEANUP_DAYS)
            logger.info(
                f"[ytunews] {tag}抓取 {len(items)} 条，"
                f"新写入 {inserted} 条，清理 {deleted} 条"
            )
        except Exception as e:
            logger.warning(f"[ytunews] {tag}抓取异常: {e}")

    # ==================== 渲染 ====================

    async def _render_news_image(self, days: int = None):
        """渲染新闻图片。加锁防止并发渲染，异常时返回 None。"""
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

            # 渲染前把 date 转成字符串，避免 JSON 序列化失败
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

    @filter.command("新闻")
    async def news(self, event: AstrMessageEvent):
        await save_umo_async(event.unified_msg_origin)

        # 库为空时自动抓一次
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

    @filter.command("搜索")
    async def search(self, event: AstrMessageEvent, keyword: str = None):
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
        """只推当前会话，不遍历订阅者。"""
        img_url = await self._render_news_image()
        if not img_url:
            yield event.plain_result("暂无新闻可推送。")
            return
        yield event.image_result(img_url)
