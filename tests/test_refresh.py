from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tbqrfly.cli import main
from tbqrfly.config import ConfigError, load_runtime_config
from tbqrfly.refresh import _atomic_write, plan_refresh, refresh_config


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "pubspec.yaml").write_text("name: demo\nversion: 1.0.0+1\n")
        self.directory = self.root / ".tbqrfly"
        self.directory.mkdir()

    def write(self, name, value):
        path = self.directory / name
        path.write_text(json.dumps(value))
        return path

    def read(self, name):
        return json.loads((self.directory / name).read_text())

    def old_run(self):
        self.write(
            "builds.json",
            {
                "app_name": "Custom App",
                "default_env": "staging",
                "pre_commands": ["custom pre"],
                "wait_timeout": 0,
                "builds": {
                    "staging": {
                        "ios": {
                            "build_command": "custom build",
                            "zealot_channel_key": "secret-channel",
                            "export_options": "custom export.plist",
                            "custom_field": {"keep": True},
                        }
                    }
                },
                "artifacts": {"ios": ["custom/*.ipa"]},
                "custom_root": "keep",
            },
        )
        self.write(
            "zealot.json", {"base_url": "${PRIVATE_URL}", "token": "private-token"}
        )
        self.write(
            "feishu.json", {"app_secret": "private-secret", "qrcode_edge_px": 1024}
        )
        self.write(
            "llm.json",
            {"enabled": False, "command": "custom llm", "prompt": "custom.md"},
        )
        (self.directory / "message.md").write_text("custom message")
        (self.directory / "prompt.md").write_text("custom prompt")

    def test_preserves_custom_values_and_adds_missing_fields(self):
        self.old_run()
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        with redirect_stdout(io.StringIO()) as output:
            self.assertEqual(refresh_config(self.root), 0)
        builds = self.read("builds.json")
        self.assertEqual(builds["pre_commands"], ["custom pre"])
        self.assertEqual(builds["next_commands"], [])
        self.assertEqual(builds["wait_timeout"], 0)
        self.assertEqual(builds["app_name"], "Custom App")
        self.assertEqual(builds["custom_root"], "keep")
        self.assertEqual(list(builds["builds"]), ["staging"])
        self.assertEqual(list(builds["builds"]["staging"]), ["ios"])
        platform = builds["builds"]["staging"]["ios"]
        self.assertEqual(platform["next_commands"], [])
        self.assertEqual(platform["build_command"], "custom build")
        self.assertEqual(platform["custom_field"], {"keep": True})
        self.assertEqual(builds["artifacts"]["ios"], ["custom/*.ipa"])
        self.assertEqual(self.read("zealot.json")["base_url"], "${PRIVATE_URL}")
        self.assertEqual(self.read("llm.json")["enabled"], False)
        self.assertEqual((self.directory / "message.md").read_text(), "custom message")
        self.assertEqual((self.directory / "prompt.md").read_text(), "custom prompt")
        self.assertNotIn("private-token", output.getvalue())
        self.assertNotIn("private-secret", output.getvalue())
        backup = next((self.directory / "backups").iterdir())
        for name in ("builds.json", "feishu.json"):
            self.assertEqual((backup / name).read_bytes(), before[name])
            self.assertEqual((backup / name).stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        after = {
            path.name: path.read_bytes()
            for path in self.directory.iterdir()
            if path.is_file()
        }
        refresh_config(self.root)
        self.assertEqual(len(list((self.directory / "backups").iterdir())), 1)
        self.assertEqual(
            after,
            {
                path.name: path.read_bytes()
                for path in self.directory.iterdir()
                if path.is_file()
            },
        )

    def test_dry_run_does_not_write_or_create_backups(self):
        self.old_run()
        before = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        self.assertEqual(main(["refresh", "--project", str(self.root), "--dry-run"]), 0)
        self.assertEqual(
            before, {path.name: path.read_bytes() for path in self.directory.iterdir()}
        )

    def test_beta_does_not_restore_removed_android_or_add_run_config(self):
        self.write(
            "beta.json",
            {
                "builds": {
                    "ios": {
                        "build_command": "custom ipa",
                        "artifacts": ["custom.ipa"],
                        "publish": {
                            "provider": "testflight",
                            "key_id": "${MY_KEY}",
                            "bundle_id": "custom",
                        },
                        "pre_commands": [],
                        "next_commands": ["custom post"],
                    }
                },
                "notify": False,
            },
        )
        refresh_config(self.root)
        beta = self.read("beta.json")
        self.assertEqual(list(beta["builds"]), ["ios"])
        self.assertEqual(beta["builds"]["ios"]["next_commands"], ["custom post"])
        self.assertEqual(beta["builds"]["ios"]["publish"]["key_id"], "${MY_KEY}")
        self.assertFalse(beta["notify"])
        self.assertFalse((self.directory / "builds.json").exists())
        self.assertFalse((self.directory / "zealot.json").exists())
        self.assertFalse((self.directory / "llm.json").exists())

    def test_explicit_target_can_add_a_new_configuration_file(self):
        self.old_run()
        refresh_config(self.root, target="beta")
        self.assertEqual(set(self.read("beta.json")["builds"]), {"ios", "android"})
        self.assertEqual(self.read("beta.json")["app_name"], "demo")
        self.assertEqual(self.read("feishu.json")["app_secret"], "private-secret")
        backup = next((self.directory / "backups").iterdir())
        self.assertIn(
            "beta.json",
            json.loads((backup / "manifest.json").read_text())["created_files"],
        )

    def test_legacy_compatibility_keys_are_not_renamed_or_shadowed(self):
        self.old_run()
        self.write(
            "zealot.json", {"zealot_base_url": "${URL}", "zealot_token": "legacy-token"}
        )
        self.write(
            "feishu.json",
            {
                "feishu_app_id": "legacy-id",
                "feishu_app_secret": "legacy-secret",
                "feishu_webhook_url": "legacy-hook",
            },
        )
        refresh_config(self.root)
        self.assertNotIn("base_url", self.read("zealot.json"))
        self.assertEqual(self.read("zealot.json")["zealot_token"], "legacy-token")
        self.assertNotIn("app_id", self.read("feishu.json"))
        config = load_runtime_config(self.root)
        self.assertEqual(config.zealot["token"], "legacy-token")
        self.assertEqual(config.feishu["app_id"], "legacy-id")

    def test_incompatible_structure_or_invalid_json_aborts_all_updates(self):
        for invalid in (
            b'{"builds": []}',
            b'{"builds": {"dev": {"ios": null}}}',
            b"invalid json",
            b"[]",
        ):
            with self.subTest(invalid=invalid):
                self.old_run()
                (self.directory / "builds.json").write_bytes(invalid)
                original = (self.directory / "feishu.json").read_bytes()
                with self.assertRaises(ConfigError):
                    refresh_config(self.root)
                self.assertEqual(
                    (self.directory / "feishu.json").read_bytes(), original
                )
                self.assertFalse((self.directory / "backups").exists())

    def test_write_failure_rolls_back_original_bytes(self):
        self.old_run()
        original = {path.name: path.read_bytes() for path in self.directory.iterdir()}
        failed = False

        def write(path, content):
            nonlocal failed
            if path == self.directory / "feishu.json" and not failed:
                failed = True
                raise OSError("simulated write failure")
            _atomic_write(path, content)

        with patch("tbqrfly.refresh._atomic_write", side_effect=write):
            with self.assertRaisesRegex(RuntimeError, "已恢复"):
                refresh_config(self.root)
        self.assertEqual(
            original,
            {
                path.name: path.read_bytes()
                for path in self.directory.iterdir()
                if path.is_file()
            },
        )

    def test_arrays_empty_values_and_custom_publishers_are_preserved(self):
        self.write(
            "beta.json",
            {
                "app_name": "",
                "next_commands": [],
                "custom": None,
                "builds": {
                    "custom": {"build_command": "anything"},
                    "ios": {
                        "build_command": "",
                        "artifacts": [],
                        "publish": {"provider": "custom", "custom_secret": "keep"},
                    },
                },
            },
        )
        refresh_config(self.root)
        beta = self.read("beta.json")
        self.assertEqual(beta["app_name"], "")
        self.assertIsNone(beta["custom"])
        self.assertEqual(beta["builds"]["ios"]["artifacts"], [])
        self.assertEqual(
            beta["builds"]["ios"]["publish"],
            {"provider": "custom", "custom_secret": "keep"},
        )
        self.assertEqual(
            beta["builds"]["custom"],
            {"build_command": "anything", "pre_commands": [], "next_commands": []},
        )

    def test_unknown_fields_are_not_deleted_when_template_removes_them(self):
        self.old_run()
        payload = self.read("feishu.json")
        payload["retired_or_custom"] = "keep"
        self.write("feishu.json", payload)
        refresh_config(self.root)
        self.assertEqual(self.read("feishu.json")["retired_or_custom"], "keep")

    def test_new_files_are_removed_if_a_later_write_fails(self):
        self.write("beta.json", {"builds": {"ios": {"build_command": "custom"}}})
        before = (self.directory / "beta.json").read_bytes()

        def fail_feishu(path, content):
            if path == self.directory / "feishu.json":
                raise OSError("simulated failure")
            _atomic_write(path, content)

        with patch("tbqrfly.refresh._atomic_write", side_effect=fail_feishu):
            with self.assertRaises(RuntimeError):
                refresh_config(self.root, target="run")
        self.assertEqual((self.directory / "beta.json").read_bytes(), before)
        self.assertFalse((self.directory / "feishu.json").exists())
        self.assertFalse((self.directory / "builds.json").exists())

    def test_duplicate_keys_are_rejected_without_rewriting(self):
        path = self.directory / "beta.json"
        original = b'{"app_name":"first","app_name":"second","builds":{}}'
        path.write_bytes(original)
        with self.assertRaises(ConfigError):
            refresh_config(self.root)
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse((self.directory / "backups").exists())

    def test_refresh_requires_existing_config_directory(self):
        self.directory.rmdir()
        with self.assertRaises(ConfigError):
            plan_refresh(self.root)


if __name__ == "__main__":
    unittest.main()
