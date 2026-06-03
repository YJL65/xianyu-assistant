from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass


class FeishuError(RuntimeError):
    pass


def sign(timestamp: str, secret: str) -> str:
    signing_key = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(signing_key, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


@dataclass
class FeishuNotifier:
    webhook_url: str
    secret: str = ""
    timeout_seconds: int = 15

    def send_text(self, text: str) -> dict:
        payload = {
            "msg_type": "text",
            "content": {"text": text},
        }
        return self._send(payload)

    def send_intake_summary(
        self,
        buyer_name: str,
        listing_title: str,
        summary: str,
        raw_dialogue: str,
    ) -> dict:
        text = (
            "闲鱼服务提醒：有新的咨询需要接手\n\n"
            f"买家：{buyer_name or '未知'}\n"
            f"商品：{listing_title or '未识别'}\n\n"
            f"需求摘要：\n{summary}\n\n"
            f"对话摘录：\n{raw_dialogue}"
        )
        return self.send_text(text[:18000])

    def send_new_inquiry(
        self,
        buyer_name: str,
        listing_title: str,
        first_message: str,
    ) -> dict:
        text = (
            "闲鱼服务提醒：有新的买家咨询\n\n"
            f"买家：{buyer_name or '未知'}\n"
            f"商品：{listing_title or '未识别'}\n"
            f"首条消息：{first_message or '空'}\n\n"
            "助手会继续回复买家；后续默认不重复通知。"
        )
        return self.send_text(text[:4000])

    def send_first_ai_exchange(
        self,
        buyer_name: str,
        listing_title: str,
        buyer_message: str,
        assistant_reply: str,
    ) -> dict:
        text = (
            "闲鱼服务提醒：新的买家咨询\n\n"
            f"买家：{buyer_name or '未知'}\n"
            f"商品：{listing_title or '未识别'}\n\n"
            "首次对话：\n"
            f"买家：{buyer_message or '空'}\n"
            f"模型：{assistant_reply or '空'}"
        )
        return self.send_text(text[:4000])

    def send_buyer_message(
        self,
        buyer_name: str,
        listing_title: str,
        latest_message: str,
        raw_dialogue: str = "",
        ai_advice: str = "",
    ) -> dict:
        unknown = "\u672a\u77e5"
        unrecognized = "\u672a\u8bc6\u522b"
        empty = "\u7a7a"
        text = (
            "\u95f2\u9c7c\u4e70\u5bb6\u6d88\u606f\n\n"
            f"\u4e70\u5bb6\uff1a{buyer_name or unknown}\n"
            f"\u5546\u54c1\uff1a{listing_title or unrecognized}\n"
            f"\u6700\u65b0\u6d88\u606f\uff1a{latest_message or empty}\n"
        )
        if ai_advice:
            text += f"\nAI\u5ba2\u670d\u5efa\u8bae\uff1a\n{ai_advice}\n"
        if raw_dialogue:
            text += f"\n\u6700\u8fd1\u5bf9\u8bdd\uff1a\n{raw_dialogue}"
        return self.send_text(text[:8000])

    def send_customer_service_summary(
        self,
        buyer_name: str,
        listing_title: str,
        latest_message: str,
        summary: str,
        raw_dialogue: str = "",
    ) -> dict:
        unknown = "\u672a\u77e5"
        unrecognized = "\u672a\u8bc6\u522b"
        empty = "\u7a7a"
        text = (
            "\u95f2\u9c7c\u9700\u6c42\u5df2\u6536\u96c6\n\n"
            f"\u4e70\u5bb6\uff1a{buyer_name or unknown}\n"
            f"\u5546\u54c1\uff1a{listing_title or unrecognized}\n"
            f"\u6700\u65b0\u6d88\u606f\uff1a{latest_message or empty}\n\n"
            f"\u9700\u6c42\u6458\u8981\uff1a\n{summary or empty}"
        )
        if raw_dialogue:
            text += f"\n\n\u6700\u8fd1\u5bf9\u8bdd\uff1a\n{raw_dialogue}"
        return self.send_text(text[:12000])

    def _send(self, payload: dict) -> dict:
        if not self.webhook_url:
            raise FeishuError("FEISHU_WEBHOOK_URL is not configured")

        if self.secret:
            timestamp = str(int(time.time()))
            payload = {
                **payload,
                "timestamp": timestamp,
                "sign": sign(timestamp, self.secret),
            }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.webhook_url,
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise FeishuError(details) from exc
        except Exception as exc:
            raise FeishuError(str(exc)) from exc

        code = result.get("code")
        if code not in (0, None):
            raise FeishuError(json.dumps(result, ensure_ascii=False))
        return result
