# -*- coding: utf-8 -*-
from pathlib import Path
from collections import defaultdict
from datetime import datetime

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star

TEMPLATE_DIR = Path(__file__).parent / "templates"
CATEGORY_ORDER = ["重要", "科研竞赛", "研究生", "其他"]
MAX_PER_CATEGORY = 8


class TestRenderPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

    @filter.command("测图片")
    async def test_render(self, event: AstrMessageEvent):
        items = [
            # ── 重要 ──
            {"title": "关于开展2026年辅修双学位、辅修第二专业、微专业报名注册/注销工作的通知",
             "date": "2026-09-14", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2734.htm"},
            {"title": "关于做好2027届毕业生一次性求职补贴审核发放工作的通知",
             "date": "2026-09-14", "category": "重要", "site": "学生工作处",
             "url": "https://stu.ytu.edu.cn/info/xxxx.htm"},
            {"title": "关于做好2027届毕业生生源信息审核工作的通知",
             "date": "2026-09-11", "category": "重要", "site": "学生工作处",
             "url": "https://stu.ytu.edu.cn/info/yyyy.htm"},
            {"title": "2026-2027学年第一学期本科生重修选课通知",
             "date": "2026-09-10", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2733.htm"},
            {"title": "关于公布烟台大学2026-2027学年第一学期本科生转专业录取结果并做好后续工作的通知",
             "date": "2026-09-09", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2732.htm"},
            {"title": "2026年下半年大学英语四、六级考试报名工作的通知",
             "date": "2026-09-08", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2731.htm"},
            {"title": "关于公共外语课程学分调整及课程转修相关政策的情况说明",
             "date": "2026-09-05", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2728.htm"},
            {"title": "2026-2027学年第一学期全校通选课线上部分选课通知",
             "date": "2026-09-04", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2725.htm"},
            {"title": "2026级新生选课通知",
             "date": "2026-09-04", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2726.htm"},
            {"title": "学士学位授予证明、成绩单等相关证明办理指南",
             "date": "2026-09-01", "category": "重要", "site": "教务处",
             "url": "https://jwc.ytu.edu.cn/info/1007/2723.htm"},

            # ── 科研竞赛 ──
            {"title": "化学化工学院秦玉升教授团队在 JACS 发表最新研究成果",
             "date": "2026-09-14", "category": "科研竞赛", "site": "科技处",
             "url": "https://kjc.ytu.edu.cn/info/1003/4084.htm"},
            {"title": "环境与材料工程学院公丕军团队在挥发性有机物催化治理方面取得系列重要进展",
             "date": "2026-09-11", "category": "科研竞赛", "site": "科技处",
             "url": "https://kjc.ytu.edu.cn/info/1003/4083.htm"},
            {"title": "关于举办2026年大学生新文科实践创新大赛的通知",
             "date": "2026-09-08", "category": "科研竞赛", "site": "学科竞赛网",
             "url": "https://xkjs.ytu.edu.cn/info/1046/1684.htm"},
            {"title": "关于举办山东省大学生科技赛事——山东省大学生创新发明大赛的通知",
             "date": "2026-09-08", "category": "科研竞赛", "site": "学科竞赛网",
             "url": "https://xkjs.ytu.edu.cn/info/1046/1683.htm"},
            {"title": "2026年“理解当代中国”全国大学生外语能力大赛校赛通知",
             "date": "2026-09-04", "category": "科研竞赛", "site": "学科竞赛网",
             "url": "https://xkjs.ytu.edu.cn/info/1046/1682.htm"},
            {"title": "第二十三届中国研究生数学建模竞赛报名和培训通知",
             "date": "2026-08-31", "category": "科研竞赛", "site": "学科竞赛网",
             "url": "https://xkjs.ytu.edu.cn/info/1046/1681.htm"},
            {"title": "物理与电子信息学院陈平教授团队在新型有机光电器件领域取得重要进展",
             "date": "2026-08-28", "category": "科研竞赛", "site": "科技处",
             "url": "https://kjc.ytu.edu.cn/info/1003/4075.htm"},

            # ── 研究生 ──
            {"title": "烟台大学2026年博士研究生招生英语水平考试成绩公示",
             "date": "2026-05-08", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1014/1015.htm"},
            {"title": "烟台大学2026年全日制学术学位博士研究生招生考试综合考核工作安排",
             "date": "2026-04-28", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1014/1020.htm"},
            {"title": "烟台大学2026年全日制学术学位博士研究生招生章程",
             "date": "2026-03-10", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1014/1022.htm"},
            {"title": "烟台大学2026年硕士研究生招生考试初试成绩复核结果",
             "date": "2026-03-05", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1015/1083.htm"},
            {"title": "烟台大学2026年硕士研究生招生考试初试成绩查询及成绩复核的通知",
             "date": "2026-02-27", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1015/1105.htm"},
            {"title": "2026年硕士研究生招生考试自命题科目答题纸条形码粘贴说明",
             "date": "2025-12-09", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1015/1060.htm"},
            {"title": "2026年全国硕士研究生招生考试报名烟台大学报考点（3781）网上信息确认公告",
             "date": "2025-10-27", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1015/1048.htm"},
            {"title": "烟台大学2026年硕士研究生招生章程",
             "date": "2025-09-29", "category": "研究生", "site": "研究生处",
             "url": "https://yjs.ytu.edu.cn/info/1015/1054.htm"},

            # ── 其他 ──
            {"title": "烟台大学校医院医用纯水机，UPS不间断电源采购竞争性磋商公告",
             "date": "2026-06-20", "category": "其他", "site": "校医院",
             "url": "https://hospital.ytu.edu.cn/info/1213/1347.htm"},
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
            "max_per": MAX_PER_CATEGORY,
        }
        img_url = await self.html_render(tmpl, data)

        yield event.image_result(img_url)
