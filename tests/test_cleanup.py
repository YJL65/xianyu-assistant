import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.cleanup import cleanup_data
from app.config import Settings
from app.models import IncomingMessage, NeedState
from app.storage import Store


def test_settings(root: Path) -> Settings:
    return Settings(
        feishu_webhook_url="",
        feishu_webhook_secret="",
        openai_base_url="",
        openai_api_key="",
        openai_model="",
        db_path=root / "data" / "assistant.sqlite3",
        log_path=root / "logs" / "assistant.log",
        mode="test",
        xianyu_cookie="",
        xianyu_vendor_path=root,
        node_exe="",
    )


class CleanupTests(unittest.TestCase):
    def test_cleanup_default_policy_deletes_records_older_than_three_days(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = test_settings(root)
            store = Store(settings.db_path)
            old_time = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
            recent_time = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
            store.add_incoming(
                IncomingMessage(
                    conversation_id="old-default",
                    buyer_id="buyer-old",
                    buyer_name="旧买家",
                    text="四天前消息",
                    message_id="old-default-msg",
                    created_at=old_time,
                )
            )
            store.add_incoming(
                IncomingMessage(
                    conversation_id="recent-default",
                    buyer_id="buyer-recent",
                    buyer_name="新买家",
                    text="两天前消息",
                    message_id="recent-default-msg",
                    created_at=recent_time,
                )
            )

            result = cleanup_data(settings, skip_debug=True)

            self.assertEqual(result.deleted_messages, 1)
            self.assertEqual(store.history("old-default"), [])
            self.assertEqual(store.history("recent-default"), [("buyer", "两天前消息")])

    def test_cleanup_deletes_old_messages_and_keeps_states_by_default(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = test_settings(root)
            store = Store(settings.db_path)
            old_time = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
            recent_time = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            store.add_incoming(
                IncomingMessage(
                    conversation_id="old-cid",
                    buyer_id="buyer-old",
                    buyer_name="旧买家",
                    text="旧消息",
                    message_id="old-msg",
                    created_at=old_time,
                )
            )
            store.add_incoming(
                IncomingMessage(
                    conversation_id="recent-cid",
                    buyer_id="buyer-recent",
                    buyer_name="新买家",
                    text="新消息",
                    message_id="recent-msg",
                    created_at=recent_time,
                )
            )
            store.save_state("old-cid", NeedState(manual_takeover=True))

            result = cleanup_data(settings, message_days=30, skip_debug=True)

            self.assertEqual(result.deleted_messages, 1)
            self.assertEqual(store.history("old-cid"), [])
            self.assertEqual(store.history("recent-cid"), [("buyer", "新消息")])
            self.assertTrue(store.state("old-cid").manual_takeover)

    def test_cleanup_dry_run_does_not_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = test_settings(root)
            store = Store(settings.db_path)
            old_time = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
            store.add_incoming(
                IncomingMessage(
                    conversation_id="cid",
                    buyer_id="buyer",
                    buyer_name="买家",
                    text="旧消息",
                    message_id="old-msg",
                    created_at=old_time,
                )
            )

            result = cleanup_data(settings, message_days=30, skip_debug=True, dry_run=True)

            self.assertEqual(result.matched_messages, 1)
            self.assertEqual(result.deleted_messages, 0)
            self.assertEqual(store.history("cid"), [("buyer", "旧消息")])

    def test_cleanup_deletes_old_debug_files_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings = test_settings(root)
            settings.db_path.parent.mkdir(parents=True, exist_ok=True)
            old_file = settings.db_path.parent / "xianyu_history_old.json"
            recent_file = settings.db_path.parent / "xianyu_recent_conversations.json"
            old_file.write_text("old", encoding="utf-8")
            recent_file.write_text("recent", encoding="utf-8")
            old_timestamp = (datetime.now(timezone.utc) - timedelta(days=10)).timestamp()
            os.utime(old_file, (old_timestamp, old_timestamp))

            result = cleanup_data(settings, message_days=30, debug_days=7)

            self.assertEqual(result.deleted_debug_files, 1)
            self.assertFalse(old_file.exists())
            self.assertTrue(recent_file.exists())


if __name__ == "__main__":
    unittest.main()
