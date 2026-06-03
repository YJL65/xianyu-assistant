import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.local"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def feishu_sign(timestamp: str, secret: str) -> str:
    signing_key = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(signing_key, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def main() -> int:
    load_env(ENV_FILE)
    webhook_url = os.environ.get("FEISHU_WEBHOOK_URL")
    secret = os.environ.get("FEISHU_WEBHOOK_SECRET")

    if not webhook_url:
        print("FEISHU_WEBHOOK_URL is missing", file=sys.stderr)
        return 2

    payload = {
        "msg_type": "text",
        "content": {
            "text": "闲鱼服务提醒：飞书机器人接入测试成功。",
        },
    }

    if secret:
        timestamp = str(int(time.time()))
        payload["timestamp"] = timestamp
        payload["sign"] = feishu_sign(timestamp, secret)

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        webhook_url,
        data=body,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(exc.read().decode("utf-8", errors="replace"), file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Failed to send Feishu test message: {exc}", file=sys.stderr)
        return 1

    code = result.get("code")
    if code not in (0, None):
        print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
        return 1

    print("Feishu test message sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
