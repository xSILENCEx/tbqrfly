from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tbqrfly.config import load_runtime_config, render_placeholders
from tbqrfly.init_cmd import (
    _generate_export_options_plist,
    _parse_ios_signing,
    detect_app_name,
    ensure_export_options,
    run_init,
)
from tbqrfly.vscode_tasks import TBQRFLY_TASK_LABEL, ensure_vscode_tbqrfly_task
from tbqrfly.workflow.pre_commands import run_pre_commands


class TbqrflyTests(unittest.TestCase):
    def test_render_placeholders(self) -> None:
        text = render_placeholders(
            "{app_name} {platform} {version}+{buildNumber} {env} {commit}\n{summary}",
            {
                "app_name": "Demo",
                "platform": "Android",
                "version": "1.0.0",
                "buildNumber": "2",
                "env": "dev",
                "commit": "abc",
                "summary": "fix",
            },
        )
        self.assertIn("Demo Android 1.0.0+2", text)
        self.assertIn("fix", text)

    def test_init_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pubspec.yaml").write_text(
                "name: demo_app\nversion: 1.2.3+4\n", encoding="utf-8"
            )
            self.assertEqual(detect_app_name(root), "demo_app")
            code = run_init(root, force=True)
            # flutter/git may be missing in CI sandbox -> fatal allowed with force still writes
            self.assertTrue((root / ".tbqrfly" / "builds.json").is_file())
            builds = json.loads((root / ".tbqrfly" / "builds.json").read_text(encoding="utf-8"))
            self.assertEqual(builds["app_name"], "demo_app")
            # Fill required fields so load works even if doctor failed.
            zealot = {
                "base_url": "https://zealot.example.com",
                "token": "t",
            }
            (root / ".tbqrfly" / "zealot.json").write_text(
                json.dumps(zealot), encoding="utf-8"
            )
            for env in builds["builds"].values():
                for platform in env.values():
                    platform["zealot_channel_key"] = "channel"
            (root / ".tbqrfly" / "builds.json").write_text(
                json.dumps(builds, indent=2), encoding="utf-8"
            )
            config = load_runtime_config(root)
            self.assertEqual(config.app_name, "demo_app")
            self.assertIn("dev", config.env_names())
            self.assertEqual(code in {0, 2}, True)

    def test_generate_export_options_from_team(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ios_proj = root / "ios" / "Runner.xcodeproj"
            ios_proj.mkdir(parents=True)
            (ios_proj / "project.pbxproj").write_text(
                """
				DEVELOPMENT_TEAM = ABCDE12345;
				PRODUCT_BUNDLE_IDENTIFIER = com.example.app;
				CODE_SIGN_STYLE = Automatic;
""",
                encoding="utf-8",
            )
            team, style = _parse_ios_signing(root)
            self.assertEqual(team, "ABCDE12345")
            self.assertEqual(style, "automatic")
            directory = root / ".tbqrfly"
            directory.mkdir()
            rel = ensure_export_options(root, directory)
            self.assertEqual(rel, ".tbqrfly/ExportOptions.plist")
            content = (directory / "ExportOptions.plist").read_text(encoding="utf-8")
            self.assertIn("ABCDE12345", content)
            self.assertIn("ad-hoc", content)
            self.assertIn("teamID", _generate_export_options_plist("X"))

    def test_vscode_task_create_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            msg1 = ensure_vscode_tbqrfly_task(root)
            tasks_path = root / ".vscode" / "tasks.json"
            self.assertTrue(tasks_path.is_file())
            self.assertIn("已写入", msg1)
            payload = json.loads(tasks_path.read_text(encoding="utf-8"))
            labels = [t.get("label") for t in payload["tasks"]]
            self.assertEqual(labels.count(TBQRFLY_TASK_LABEL), 1)
            msg2 = ensure_vscode_tbqrfly_task(root)
            self.assertIn("已存在", msg2)
            payload2 = json.loads(tasks_path.read_text(encoding="utf-8"))
            labels2 = [t.get("label") for t in payload2["tasks"]]
            self.assertEqual(labels2.count(TBQRFLY_TASK_LABEL), 1)

    def test_default_build_commands_without_flavor(self) -> None:
        from importlib import resources

        raw = resources.files("tbqrfly").joinpath("templates", "builds.json").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("--flavor", raw)
        self.assertIn('"pre_commands": []', raw)

    def test_pre_commands_empty_skips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_pre_commands(Path(tmp), [])


if __name__ == "__main__":
    unittest.main()
