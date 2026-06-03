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
        unknown = "\u672a\u77e5"
        unrecognized = "\u672a\u8bc6\u522b"
        text = (
            "\u95f2\u9c7c\u670d\u52a1\u63d0\u9192\uff1a\u6709\u65b0\u7684\u54a8\u8be2\u9700\u8981\u63a5\u624b\n\n"
            f"\u4e70\u5bb6\uff1a{buyer_name or unknown}\n"
            f"\u5546\u54c1\uff1a{listing_title or unrecognized}\n\n"
            f"\u9700\u6c42\u6458\u8981\uff1a\n{summary}\n\n"
            f"\u5bf9\u8bdd\u6458\u5f55\uff1a\n{raw_dialogue}"
        )
        return self.send_text(text[:18000])

    def send_new_inquiry(
        self,
        buyer_name: str,
        listing_title: str,
        first_message: str,
    ) -> dict:
        unknown = "\u672a\u77e5"
        unrecognized = "\u672a\u8bc6\u522b"
        empty = "\u7a7a"
        text = (
            "\u95f2\u9c7c\u670d\u52a1\u63d0\u9192\uff1a\u6709\u65b0\u7684\u4e70\u5bb6\u54a8\u8be2\n\n"
            f"\u4e70\u5bb6\uff1a{buyer_name or unknown}\n"
            f"\u5546\u54c1\uff1a{listing_title or unrecognized}\n"
            f"\u9996\u6761\u6d88\u606f\uff1a{first_message or empty}\n\n"
            "\u52a9\u624b\u4f1a\u7ee7\u7eed\u56de\u590d\u4e70\u5bb6\uff1b\u540e\u7eed\u9ed8\u8ba4\u4e0d\u91cd\u590d\u901a\u77e5\u3002"
        )
        return self.send_text(text[:4000])

    def send_first_ai_exchange(
        self,
        buyer_name: str,
        listing_title: str,
        dialogue: str,
    ) -> dict:
        unknown = "\u672a\u77e5"
        unrecognized = "\u672a\u8bc6\u522b"
        empty = "\u7a7a"
        text = (
            "\u95f2\u9c7c\u670d\u52a1\u63d0\u9192\uff1a\u65b0\u7684\u4e70\u5bb6\u54a8\u8be2\n\n"
            f"\u4e70\u5bb6\uff1a{buyer_name or unknown}\n"
            f"\u5546\u54c1\uff1a{listing_title or unrecognized}\n\n"
            "\u524d\u4e24\u8f6e\u5bf9\u8bdd\uff1a\n"
            f"{dialogue or empty}"
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
