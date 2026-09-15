# -*- coding: utf-8 -*-
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

TEMPLATE_DIR = Path(__file__).parent / "templates"
CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]


class TestRenderPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

    @filter.command("测图片")
    async def test_render(self, event: AstrMessageEvent):
        # 假数据
        items = [
            {"title": "关于开展2026年辅修双学位、辅修第二专业、微专业报名注册/注销工作的通知",
             "date": "2026-09-14", "category": "重要"},
            {"title": "关于做好2027届毕业生一次性求职补贴审核发放工作的通知",
             "date": "2026-09-14", "category": "重要"},
            {"title": "化学化工学院秦玉升教授团队在 JACS 发表最新研究成果",
             "date": "2026-09-14", "category": "科研竞赛"},
            {"title": "关于举办2026年大学生新文科实践创新大赛的通知",
             "date": "2026-09-08", "category": "科研竞赛"},
        ]

        # 按分类分组
        groups = defaultdict(list)
        for it in items:
            groups[it["category"]].append(it)
        for cat in groups:
            groups[cat].sort(key=lambda x: x["date"], reverse=True)

        # 渲染
        tmpl = (TEMPLATE_DIR / "news.html").read_text(encoding="utf-8")
        data = {
            "days": 3,
            "total": len(items),
            "now": datetime.now().strftime("%m-%d %H:%M"),
            "groups": {c: groups.get(c, []) for c in CATEGORY_ORDER},
            "max_per": 8,
        }
        img_url = await self.html_render(tmpl, data)

        yield event.image_result(img_url)