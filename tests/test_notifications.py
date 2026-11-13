from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tbqrfly.workflow.project_info import ProjectInfo
from tbqrfly.workflow.run import run_platform_pipeline


class NotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = SimpleNamespace(
            platform_config=lambda env, platform: {"zealot_channel_key": "channel"},
            zealot={"base_url": "https://example.invalid", "token": "token"},
            feishu_ready=True,
            feishu={"app_id": "id", "app_secret": "secret", "webhook_url": "webhook"},
            message_template="{install_url}",
            wait_timeout=0,
        )
        self.info = ProjectInfo("Demo", "1.0", "1", "abcdef", "abc", "main", "today")

    def run_pipeline(self) -> None:
        run_platform_pipeline(
            self.config, env="dev", platform="android", info=self.info, summary="fix"
        )

    def test_successful_upload_survives_notification_failures(self) -> None:
        # Exercise the full platform boundary: no false release-failure notification.
        for target in (
            "render_placeholders",
            "FeishuClient",
            "tempfile.TemporaryDirectory",
            "FeishuClient.send_qrcode",
        ):
            with self.subTest(target=target), patch(
                "tbqrfly.workflow.run.find_reusable", return_value=None
            ), patch("tbqrfly.workflow.run.run_build"), patch(
                "tbqrfly.workflow.run.find_after_build", return_value=Path(__file__)
            ), patch("tbqrfly.workflow.notify.ZealotClient") as zealot, patch(
                "tbqrfly.workflow.run.notify_failure"
            ) as failure, patch(
                f"tbqrfly.workflow.notify.{target}",
                side_effect=RuntimeError("sensitive-detail"),
            ), redirect_stdout(io.StringIO()) as output:
                zealot.return_value.upload.return_value = ("install-url", "qr-url")
                self.run_pipeline()
                zealot.return_value.upload.assert_called_once()
                failure.assert_not_called()
                self.assertIn("发布结果不受影响", output.getvalue())
                self.assertIn("install-url", output.getvalue())
                self.assertNotIn("sensitive-detail", output.getvalue())

    def test_upload_failure_still_fails_and_notifies(self) -> None:
        with patch("tbqrfly.workflow.run.find_reusable", return_value=None), patch(
            "tbqrfly.workflow.run.run_build"
        ), patch(
            "tbqrfly.workflow.run.find_after_build", return_value=Path(__file__)
        ), patch("tbqrfly.workflow.notify.ZealotClient") as zealot, patch(
            "tbqrfly.workflow.run.notify_failure"
        ) as failure, patch("tbqrfly.workflow.notify.FeishuClient") as feishu:
            zealot.return_value.upload.side_effect = RuntimeError("Zealot 上传失败")
            with self.assertRaisesRegex(RuntimeError, "Zealot 上传失败"):
                self.run_pipeline()
            failure.assert_called_once()
            feishu.assert_not_called()

    def test_successful_notification_and_missing_credentials(self) -> None:
        for ready in (True, False):
            with self.subTest(ready=ready), patch(
                "tbqrfly.workflow.run.find_reusable", return_value=None
            ), patch("tbqrfly.workflow.run.run_build"), patch(
                "tbqrfly.workflow.run.find_after_build", return_value=Path(__file__)
            ), patch("tbqrfly.workflow.notify.ZealotClient") as zealot, patch(
                "tbqrfly.workflow.notify.FeishuClient"
            ) as feishu:
                self.config.feishu_ready = ready
                zealot.return_value.upload.return_value = ("install-url", "qr-url")
                self.run_pipeline()
                self.assertEqual(feishu.return_value.send_qrcode.call_count, int(ready))


if __name__ == "__main__":
    unittest.main()
