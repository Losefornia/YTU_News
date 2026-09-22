# -*- coding: utf-8 -*-

SITES = [
    {
        "name": "教务处",
        "category": "重要",
        "list_url": "https://jwc.ytu.edu.cn/index/tzgg.htm",
        "container": "div.main_conRCb",
        "link_filter": "info/",
    },
    {
        "name": "学生工作处（武装部）",
        "category": "其他",
        "list_url": "https://stu.ytu.edu.cn/index/tzgg.htm",
        "container": "div#right_nei",
        "link_filter": "info/",
    },
    {
        "name": "科技处-科技动态",
        "category": "科研竞赛",
        "list_url": "https://kjc.ytu.edu.cn/kjdt.htm",
        "container": "body",
        "link_filter": "info/",
    },
    {
        "name": "学科竞赛网",
        "category": "科研竞赛",
        "list_url": "https://xkjs.ytu.edu.cn/index.htm",
        "container": "body",
        "link_filter": "info/",
    },
    {
        "name": "研究生处",
        "category": "研究生",
        "list_url": "https://yjs.ytu.edu.cn",
        "container": "body",
        "link_filter": "info/",
    },
    {
        "name": "校医院-通知公告",
        "category": "其他",
        "list_url": "https://hospital.ytu.edu.cn/tzgg.htm",
        "container": "body",
        "link_filter": "info/",
    },
    {
        "name": "就业信息网",
        "category": "其他",
        "type": "json",
        "api_url": "https://school.gxjy.sdei.edu.cn/ytu/school/Notice/indexList/2",
        "params": {"pageNum": 1, "pageSize": 10},
        "title_field": "noticeTitle",
        "date_field": "showtime",
        "id_field": "noticeId",
        "url_template": "https://school.gxjy.sdei.edu.cn/ytu/school/Notice/detail/{id}",
        "fileurl_field": "fileurl",
        "referer": "https://school.gxjy.sdei.edu.cn/ytu/front/NoticeList?deptId=2",
    },
]
