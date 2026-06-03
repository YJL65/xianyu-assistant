from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env.local"


def load_env(path: Path = ENV_FILE) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip().lstrip("\ufeff")] = value.strip()


@dataclass(frozen=True)
class Settings:
    feishu_webhook_url: str
    feishu_webhook_secret: str
    openai_base_url: str
    openai_api_key: str
    openai_model: str
    db_path: Path
    log_path: Path
    mode: str
    xianyu_cookie: str
    xianyu_vendor_path: Path
    node_exe: str

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_base_url and self.openai_api_key and self.openai_model)


def get_settings() -> Settings:
    load_env()
    return Settings(
        feishu_webhook_url=os.environ.get("FEISHU_WEBHOOK_URL", "").strip(),
        feishu_webhook_secret=os.environ.get("FEISHU_WEBHOOK_SECRET", "").strip(),
        openai_base_url=os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/"),
        openai_api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
        openai_model=os.environ.get("OPENAI_MODEL", "").strip(),
        db_path=ROOT / os.environ.get("APP_DB_PATH", "data/assistant.sqlite3"),
        log_path=ROOT / os.environ.get("APP_LOG_PATH", "logs/assistant.log"),
        mode=os.environ.get("APP_MODE", "mock").strip() or "mock",
        xianyu_cookie=os.environ.get("XIANYU_COOKIE", "").strip(),
        xianyu_vendor_path=ROOT / os.environ.get("XIANYU_VENDOR_PATH", "vendor/XianYuApis"),
        node_exe=os.environ.get("APP_NODE_EXE", "").strip(),
    )


def upsert_env_value(key: str, value: str, path: Path = ENV_FILE) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    updated = False
    output = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            output.append(f"{key}={value}")
            updated = True
        else:
            output.append(line)
    if not updated:
        output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
