# -*- coding: utf-8 -*-
from datetime import datetime
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star


class TestPushPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)

    @filter.command("测试主动")
    async def test_push(self, event: AstrMessageEvent):
        umo = event.unified_msg_origin

        # 1. 先主动发文字
        try:
            await self.context.send_message(umo, MessageChain().message("🔔 文字测试"))
        except Exception as e:
            yield event.plain_result(f"❌ 文字失败：{e}")
            return

        # 2. 再主动发图片
        html = """
        <html><body style="font-family:sans-serif;background:#222;color:#fff;
        padding:40px;width:600px;">
          <h1>📢 图片测试</h1>
          <p>{{ now }}</p>
        </body></html>
        """
        img_url = await self.html_render(html, {
            "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        })
        try:
            await self.context.send_message(umo, MessageChain().url_image(img_url))
            yield event.plain_result("✅ 文字+图片都发了，去群里看看")
        except Exception as e:
            yield event.plain_result(f"❌ 图片失败：{e}")
