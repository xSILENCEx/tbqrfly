from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tbqrfly.config import load_runtime_config, render_placeholders
from tbqrfly.init_cmd import detect_app_name, run_init


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


if __name__ == "__main__":
    unittest.main()
