from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from .config import ENV_FILE, get_settings, upsert_env_value
from .connectors.mock import MockConnector
from .connectors.xianyu import XianyuConnector, XianyuConnectorUnavailable
from .cleanup import DEFAULT_DEBUG_DAYS, DEFAULT_MESSAGE_DAYS
from .feishu import FeishuNotifier
from .runner import AssistantRunner, run


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Xianyu service intake assistant")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("test-feishu", help="Send a Feishu test message")
    subparsers.add_parser("mock", help="Run a two-message mock Xianyu conversation")
    subparsers.add_parser("xianyu-login", help="Scan QR code and save Xianyu cookie")
    subparsers.add_parser("check-xianyu-cookie", help="Check configured Xianyu cookie")

    cleanup_parser = subparsers.add_parser("cleanup", help="Manually clean message records and debug files")
    cleanup_parser.add_argument("--days", type=int, default=DEFAULT_MESSAGE_DAYS, help="Keep message records newer than this many days")
    cleanup_parser.add_argument("--debug-days", type=int, default=DEFAULT_DEBUG_DAYS, help="Keep debug files newer than this many days")
    cleanup_parser.add_argument("--all-messages", action="store_true", help="Delete all message records")
    cleanup_parser.add_argument("--clear-states", action="store_true", help="Also delete conversation states")
    cleanup_parser.add_argument("--skip-debug", action="store_true", help="Do not delete debug files")
    cleanup_parser.add_argument("--dry-run", action="store_true", help="Only show what would be deleted")

    run_parser = subparsers.add_parser("run", help="Run assistant")
    run_parser.add_argument(
        "--connector",
        choices=["mock", "xianyu"],
        default="mock",
        help="Message connector to use",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = get_settings()

    if args.command == "test-feishu":
        notifier = FeishuNotifier(settings.feishu_webhook_url, settings.feishu_webhook_secret)
        notifier.send_text("闲鱼服务提醒：飞书机器人接入测试成功。")
        print("Feishu test message sent.")
        return 0

    if args.command == "mock":
        mock_db_path = settings.db_path.parent / "mock.sqlite3"
        if mock_db_path.exists():
            mock_db_path.unlink()
        runner = AssistantRunner(replace(settings, db_path=mock_db_path), MockConnector())
        run(runner.run_forever())
        return 0

    if args.command == "xianyu-login":
        cookie = login_and_get_cookie(settings.xianyu_vendor_path, settings.node_exe)
        upsert_env_value("XIANYU_COOKIE", cookie, ENV_FILE)
        print(f"Xianyu cookie saved to {ENV_FILE}")
        return 0

    if args.command == "check-xianyu-cookie":
        from .connectors.xianyu import parse_cookie_string

        cookies = parse_cookie_string(settings.xianyu_cookie)
        required = ["unb", "_m_h5_tk"]
        missing = [key for key in required if not cookies.get(key)]
        print("Cookie keys:", ", ".join(sorted(cookies.keys())) or "(empty)")
        if missing:
            print("Missing required keys:", ", ".join(missing))
            return 2
        print("Xianyu cookie looks usable.")
        return 0

    if args.command == "cleanup":
        from .cleanup import cleanup_data

        result = cleanup_data(
            settings,
            message_days=args.days,
            debug_days=args.debug_days,
            all_messages=args.all_messages,
            clear_states=args.clear_states,
            skip_debug=args.skip_debug,
            dry_run=args.dry_run,
        )
        print(result.report())
        if args.all_messages and not args.clear_states:
            print(
                "Note: conversation states were kept. This preserves manual takeover status, "
                "but old assistant echo filtering may be less complete after all messages are deleted."
            )
        return 0

    if args.command == "run":
        connector = MockConnector()
        if args.connector == "xianyu":
            try:
                connector = XianyuConnector(
                    settings.xianyu_cookie,
                    settings.xianyu_vendor_path,
                    settings.node_exe,
                )
            except XianyuConnectorUnavailable as exc:
                print(str(exc))
                return 2
        runner = AssistantRunner(settings, connector)
        run(runner.run_forever())
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def login_and_get_cookie(vendor_path: Path, node_exe: str) -> str:
    from .connectors.xianyu import configure_node_path, temporary_cwd

    configure_node_path(node_exe)
    import sys

    with temporary_cwd(vendor_path):
        if str(vendor_path) not in sys.path:
            sys.path.insert(0, str(vendor_path))
        from goofish_apis import qrcode_login
        from utils.goofish_utils import trans_cookies_str

        api = qrcode_login()
        cookies = {
            cookie.name: cookie.value
            for cookie in api.session.cookies
            if not cookie.domain or ".goofish.com" in cookie.domain or ".mmstat.com" in cookie.domain
        }
        if not cookies.get("unb"):
            raise RuntimeError(
                "Xianyu scan was confirmed, but the login response did not provide `unb`. "
                "A debug snapshot was written to data/xianyu_login_debug.json. "
                "Try `python -m app xianyu-login` again, or paste a complete logged-in "
                "goofish.com browser cookie into .env.local."
            )
        return trans_cookies_str(cookies)
