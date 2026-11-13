from __future__ import annotations

import io
import json
import os
import sys
import plistlib
import subprocess
import tempfile
import unittest
import zipfile
from contextlib import ExitStack, redirect_stdout
from importlib import resources
from pathlib import Path
from unittest.mock import Mock, patch

from tbqrfly.beta_config import load_beta_config, validate_beta
from tbqrfly.beta_doctor import collect_beta_checks
from tbqrfly.beta_init import init_beta
from tbqrfly.cli import build_parser, main
from tbqrfly.clients.testflight import (
    fastlane_directory,
    prepare_fastlane,
    upload_testflight,
)
from tbqrfly.config import ConfigError
from tbqrfly.workflow.beta import (
    artifact_snapshot,
    find_beta_artifact,
    notify_beta,
    run_beta_platform,
    validate_ipa,
)
from tbqrfly.workflow.fork import spawn_platform_inline, spawn_platform_terminal
from tbqrfly.workflow.project_info import ProjectInfo
from tbqrfly.workflow.build import run_build
from tbqrfly.workflow.release_lock import ios_release_lock
from tbqrfly.workflow.run import run_platform_pipeline, run_workflow


class BetaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="beta project ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / ".tbqrfly"
        self.directory.mkdir()
        (self.root / "pubspec.yaml").write_text("name: demo\nversion: 1.2.3+4\n")
        self.key = self.root / "private key.p8"
        self.key.write_text(
            "-----BEGIN PRIVATE KEY-----\ntest fixture\n-----END PRIVATE KEY-----"
        )
        self.export = self.root / "export.plist"
        self.export.write_bytes(
            plistlib.dumps(
                {"method": "app-store-connect", "manageAppVersionAndBuildNumber": False}
            )
        )
        self.raw = json.loads(
            resources.files("tbqrfly").joinpath("templates", "beta.json").read_text()
        )
        block = self.raw["builds"]["ios"]
        block["export_options"] = str(self.export)
        block["publish"].update(
            bundle_id="com.example.demo",
            key_id="private-id",
            issuer_id="private-issuer",
            key_file=str(self.key),
        )
        self.info = ProjectInfo("Demo", "1.2.3", "4", "abcdef", "abc", "main", "today")
        self.ipa = self.root / "build/ios/ipa/demo.ipa"
        self.save()

    def save(self):
        (self.directory / "beta.json").write_text(json.dumps(self.raw))
        return load_beta_config(self.root)

    def write_ipa(self, **overrides):
        self.ipa.parent.mkdir(parents=True, exist_ok=True)
        plist = {
            "CFBundleIdentifier": "com.example.demo",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "4",
        }
        plist.update(overrides)
        with zipfile.ZipFile(self.ipa, "w") as archive:
            archive.writestr("Payload/Demo.app/Info.plist", plistlib.dumps(plist))

    def test_beta_config_is_independent_and_fixed_prod(self):
        config = self.save()
        validate_beta(config, "prod", ["ios"])
        self.assertEqual(config.env_names(), ["prod"])
        self.assertEqual(config.platforms_for("prod"), ["ios"])
        self.assertEqual(config.zealot, {})
        self.assertEqual(config.history_path.name, "beta_commit_history.json")
        self.assertEqual(config.next_commands, [])
        with self.assertRaises(ConfigError):
            validate_beta(config, "dev", ["ios"])
        self.raw["default_env"] = "dev"
        with self.assertRaises(ConfigError):
            self.save()

    def test_unavailable_provider_is_explicit(self):
        self.raw["builds"]["android"] = {"publish": {"provider": "google_play"}}
        config = self.save()
        # An unselected future platform does not block iOS.
        validate_beta(config, "prod", ["ios"])
        with self.assertRaisesRegex(ConfigError, "尚不支持.*android/google_play"):
            validate_beta(config, "prod", ["android"])

    def test_invalid_commands_and_config_values(self):
        for field, value in (
            ("notify", "false"),
            ("wait_timeout", -1),
            ("terminal", "bad"),
            ("pre_commands", "echo bad"),
            ("next_commands", [1]),
        ):
            with self.subTest(field=field):
                original = self.raw.get(field)
                self.raw[field] = value
                with self.assertRaises(ConfigError):
                    self.save()
                if original is None:
                    del self.raw[field]
                else:
                    self.raw[field] = original
        for command in (
            "flutter build ipa --flavor dev",
            "flutter build ipa --flavor=dev",
            "flutter build ipa --dart-define=FLAVOR=dev",
            "flutter build ipa --dart-define FLAVOR=dev",
            "flutter build ipa --debug",
            "flutter build ipa --profile",
            "'unterminated",
        ):
            with self.subTest(command=command):
                self.raw["builds"]["ios"]["build_command"] = command
                with self.assertRaises(ConfigError):
                    validate_beta(self.save(), "prod", ["ios"])

    def test_credentials_and_export_validation(self):
        config = self.save()
        for key in ("bundle_id", "key_id", "issuer_id", "key_file"):
            with self.subTest(key=key):
                value = config.builds["prod"]["ios"]["publish"][key]
                config.builds["prod"]["ios"]["publish"][key] = ""
                with self.assertRaises(ConfigError):
                    validate_beta(config, "prod", ["ios"])
                config.builds["prod"]["ios"]["publish"][key] = value
        for export in (
            {"method": "ad-hoc"},
            {"method": "app-store-connect", "destination": "upload"},
            {"method": "app-store-connect", "manageAppVersionAndBuildNumber": True},
        ):
            self.export.write_bytes(plistlib.dumps(export))
            with self.assertRaises(ConfigError):
                validate_beta(config, "prod", ["ios"])
        self.export.write_bytes(b"bad plist")
        with self.assertRaises(ConfigError):
            validate_beta(config, "prod", ["ios"])

    def test_env_references_and_relative_paths(self):
        self.raw["builds"]["ios"]["publish"].update(
            key_id="${ASC_KEY_ID}", key_file=self.key.name
        )
        with patch.dict("os.environ", {"ASC_KEY_ID": "resolved"}):
            config = self.save()
        validate_beta(config, "prod", ["ios"])
        self.assertEqual(config.builds["prod"]["ios"]["publish"]["key_id"], "resolved")

    def test_init_beta_preserves_other_configuration(self):
        (self.directory / "beta.json").unlink()
        (self.directory / "builds.json").write_text("existing builds")
        (self.directory / "feishu.json").write_text("existing feishu")
        self.assertEqual(init_beta(self.root), 0)
        self.assertEqual(
            (self.directory / "builds.json").read_text(), "existing builds"
        )
        self.assertEqual(
            (self.directory / "feishu.json").read_text(), "existing feishu"
        )
        self.assertFalse((self.directory / "zealot.json").exists())
        with self.assertRaises(ConfigError):
            init_beta(self.root)
        tasks = json.loads((self.root / ".vscode/tasks.json").read_text())["tasks"]
        self.assertEqual(tasks[0]["command"], "tbqrfly beta")

    def test_fresh_artifact_and_ipa_identity(self):
        config = self.save()
        self.write_ipa()
        before = artifact_snapshot(config, "ios")
        with self.assertRaisesRegex(RuntimeError, "本次"):
            find_beta_artifact(config, "ios", before)
        validate_ipa(self.ipa, "com.example.demo", self.info)
        for key in (
            "CFBundleIdentifier",
            "CFBundleShortVersionString",
            "CFBundleVersion",
        ):
            self.write_ipa(**{key: "wrong"})
            with self.assertRaisesRegex(RuntimeError, key):
                validate_ipa(self.ipa, "com.example.demo", self.info)
        self.write_ipa()
        second = self.ipa.with_name("second.ipa")
        second.write_bytes(self.ipa.read_bytes())
        with self.assertRaisesRegex(RuntimeError, "唯一"):
            find_beta_artifact(config, "ios", {})
        self.ipa.write_bytes(b"bad zip")
        with self.assertRaises(RuntimeError):
            validate_ipa(self.ipa, "com.example.demo", self.info)

    def pipeline_patches(self, stack):
        events = []
        config = self.save()
        config.builds["prod"]["ios"]["pre_commands"] = ["platform-pre"]
        config.builds["prod"]["ios"]["next_commands"] = ["platform-next"]
        stack.enter_context(
            patch(
                "tbqrfly.workflow.beta.run_commands",
                side_effect=lambda root, commands, **k: events.extend(commands),
            )
        )

        def build(*args):
            events.append("build")
            self.write_ipa()

        stack.enter_context(patch("tbqrfly.workflow.beta.run_build", side_effect=build))
        upload = stack.enter_context(
            patch(
                "tbqrfly.workflow.beta.upload_testflight",
                side_effect=lambda *a: events.append("upload"),
            )
        )
        notify = stack.enter_context(patch("tbqrfly.workflow.beta.notify_beta"))
        return config, events, upload, notify

    def test_platform_lifecycle(self):
        with ExitStack() as stack:
            config, events, upload, notify = self.pipeline_patches(stack)
            run_beta_platform(
                config, env="prod", platform="ios", info=self.info, summary=""
            )
            self.assertEqual(
                events, ["platform-pre", "build", "upload", "platform-next"]
            )
            upload.assert_called_once()
            self.assertIn("等待 Apple 处理", notify.call_args.kwargs["message"])

    def test_upload_failure_skips_next(self):
        with ExitStack() as stack:
            config, events, upload, notify = self.pipeline_patches(stack)
            upload.side_effect = RuntimeError("upload failed")
            with self.assertRaisesRegex(RuntimeError, "upload failed"):
                run_beta_platform(
                    config, env="prod", platform="ios", info=self.info, summary=""
                )
            self.assertNotIn("platform-next", events)
            self.assertIn("失败", notify.call_args.kwargs["message"])

    def test_post_failure_reports_upload_success(self):
        with ExitStack() as stack:
            config, events, upload, notify = self.pipeline_patches(stack)

            def hooks(root, commands, **kwargs):
                if commands == ["platform-next"]:
                    raise RuntimeError("next failed")

            stack.enter_context(
                patch("tbqrfly.workflow.beta.run_commands", side_effect=hooks)
            )
            with self.assertRaisesRegex(RuntimeError, "next failed"):
                run_beta_platform(
                    config, env="prod", platform="ios", info=self.info, summary=""
                )
            self.assertIn("已上传", notify.call_args.kwargs["message"])

    def test_beta_notify_failure_is_nonfatal_and_redacted(self):
        config = self.save()
        config.feishu = {"app_id": "id", "app_secret": "secret", "webhook_url": "url"}
        with (
            patch(
                "tbqrfly.workflow.beta.FeishuClient", side_effect=RuntimeError("secret")
            ),
            redirect_stdout(io.StringIO()) as output,
        ):
            notify_beta(config, platform="ios", info=self.info, message="uploaded")
        self.assertIn("发布结果不受影响", output.getvalue())
        self.assertNotIn("secret", output.getvalue())
        config.notify = False
        with patch("tbqrfly.workflow.beta.FeishuClient") as client:
            notify_beta(config, platform="ios", info=self.info, message="uploaded")
            client.assert_not_called()

    def test_fastlane_credentials_are_environment_only(self):
        config = self.save()
        with patch(
            "tbqrfly.clients.testflight.subprocess.run", return_value=Mock(returncode=0)
        ) as run:
            upload_testflight(config, self.ipa, "ios")
            args, kwargs = run.call_args
            self.assertEqual(
                args[0], ["bundle", "exec", "fastlane", "ios", "tbqrfly_upload"]
            )
            self.assertNotIn("private-id", str(args))
            self.assertEqual(
                kwargs["env"]["TBQRFLY_ASC_KEY_FILE"], str(self.key.resolve())
            )
            self.assertEqual(kwargs["env"]["TBQRFLY_BETA_IPA"], str(self.ipa.resolve()))
            self.assertEqual(kwargs["cwd"], fastlane_directory(config))
        with patch(
            "tbqrfly.clients.testflight.subprocess.run", return_value=Mock(returncode=1)
        ):
            with self.assertRaisesRegex(RuntimeError, "上传失败"):
                upload_testflight(config, self.ipa, "ios")

    def test_fastlane_runtime_is_pinned_and_separate(self):
        config = self.save()
        with patch(
            "tbqrfly.clients.testflight.subprocess.run",
            side_effect=[Mock(returncode=1), Mock(returncode=0)],
        ) as run:
            prepare_fastlane(config)
            self.assertEqual(run.call_count, 2)
        directory = fastlane_directory(config)
        self.assertIn("2.241.2", (directory / "Gemfile").read_text())
        self.assertTrue((directory / "Gemfile.lock").is_file())
        fastfile = (directory / "fastlane/Fastfile").read_text()
        self.assertIn("skip_submission: true", fastfile)
        self.assertIn("skip_waiting_for_build_processing: true", fastfile)
        with patch(
            "tbqrfly.clients.testflight.subprocess.run", return_value=Mock(returncode=0)
        ) as run:
            prepare_fastlane(config)
            run.assert_called_once()

    def test_ios_lock_prevents_conflicting_pipelines_and_releases(self):
        config = self.save()
        with ios_release_lock(self.root, "ios"):
            with self.assertRaisesRegex(RuntimeError, "已有 iOS"):
                run_platform_pipeline(
                    config, env="prod", platform="ios", info=self.info, summary=""
                )
            with ios_release_lock(self.root, "android"):
                pass
        with ios_release_lock(self.root, "ios"):
            pass

    def test_beta_workflow_uses_own_config_and_global_hooks(self):
        config = self.save()
        config.pre_commands = ["global-pre"]
        config.next_commands = ["global-next"]
        events = []
        with ExitStack() as stack:
            stack.enter_context(
                patch("tbqrfly.workflow.run.load_beta_config", return_value=config)
            )
            stack.enter_context(
                patch("tbqrfly.workflow.run.collect_beta_checks", return_value=[])
            )
            stack.enter_context(patch("tbqrfly.workflow.run.prepare_fastlane"))
            stack.enter_context(
                patch(
                    "tbqrfly.workflow.run.run_pre_commands",
                    side_effect=lambda *a: events.append("global-pre"),
                )
            )
            stack.enter_context(
                patch("tbqrfly.workflow.run.load_project_info", return_value=self.info)
            )
            stack.enter_context(
                patch("tbqrfly.workflow.run.generate_summary", return_value="summary")
            )
            stack.enter_context(
                patch(
                    "tbqrfly.workflow.run.run_beta_platform",
                    side_effect=lambda *a, **k: events.append("platform"),
                )
            )
            stack.enter_context(
                patch(
                    "tbqrfly.workflow.run.run_commands",
                    side_effect=lambda *a, **k: events.append("global-next"),
                )
            )
            stack.enter_context(
                patch(
                    "tbqrfly.workflow.run.load_runtime_config",
                    side_effect=AssertionError("Zealot config must not load"),
                )
            )
            self.assertEqual(run_workflow(self.root, target="beta", platform="all"), 0)
        self.assertEqual(events, ["global-pre", "platform", "global-next"])
        self.assertTrue(config.history_path.is_file())
        self.assertFalse((self.directory / "commit_history.json").exists())

    def test_build_preserves_prod_and_adds_export_and_commit(self):
        config = self.save()
        process = Mock(stdout=iter([]))
        process.wait.return_value = 0
        with patch(
            "tbqrfly.workflow.build.subprocess.Popen", return_value=process
        ) as popen:
            run_build(config, "prod", "ios", self.info)
            args = popen.call_args.args[0]
            self.assertIn("--flavor", args)
            self.assertIn("prod", args)
            self.assertIn("--dart-define=FLAVOR=prod", args)
            self.assertIn("--dart-define=GIT_COMMIT=abc", args)
            self.assertIn(f"--export-options-plist={self.export}", args)

    def test_beta_parallel_child_failure_is_awaited_without_global_hooks(self):
        config = self.save()
        config.builds["prod"]["android"] = {"publish": {"provider": "future"}}
        config.terminal = "system"
        with ExitStack() as stack:
            stack.enter_context(
                patch("tbqrfly.workflow.run.load_beta_config", return_value=config)
            )
            stack.enter_context(patch("tbqrfly.workflow.run.validate_beta"))
            stack.enter_context(
                patch("tbqrfly.workflow.run.collect_beta_checks", return_value=[])
            )
            stack.enter_context(patch("tbqrfly.workflow.run.prepare_fastlane"))
            stack.enter_context(
                patch("tbqrfly.workflow.run.load_project_info", return_value=self.info)
            )
            stack.enter_context(
                patch("tbqrfly.workflow.run.generate_summary", return_value="")
            )
            stack.enter_context(patch("tbqrfly.workflow.run.run_beta_platform"))
            launch = stack.enter_context(
                patch("tbqrfly.workflow.run.spawn_platform_terminal", return_value=True)
            )
            wait = stack.enter_context(
                patch("tbqrfly.workflow.run.wait_terminal_process", return_value=1)
            )
            history = stack.enter_context(patch("tbqrfly.workflow.run.save_history"))
            self.assertEqual(run_workflow(self.root, target="beta", platform="all"), 1)
            self.assertEqual(launch.call_args.kwargs["command"], "beta")
            wait.assert_called_once()
            history.assert_not_called()

    def test_beta_cli_and_doctor(self):
        args = build_parser().parse_args(["beta", "--platform", "ios"])
        self.assertFalse(hasattr(args, "env"))
        with patch("tbqrfly.cli.run_workflow", return_value=0) as workflow:
            self.assertEqual(
                main(["beta", "--project", str(self.root), "--platform", "ios"]), 0
            )
            self.assertEqual(workflow.call_args.kwargs["target"], "beta")
        with (
            patch("tbqrfly.beta_doctor.platform.system", return_value="Darwin"),
            patch("tbqrfly.doctor._which", return_value="/fake/command"),
            patch(
                "tbqrfly.beta_doctor.subprocess.run",
                side_effect=[
                    Mock(returncode=0, stdout="ruby 3.4.0"),
                    Mock(returncode=0, stdout="Bundler version 4.0.20"),
                ],
            ),
        ):
            checks = collect_beta_checks(self.root)
            self.assertFalse(any(item.level == "fail" for item in checks))
        self.raw["builds"]["ios"]["publish"]["key_id"] = ""
        self.save()
        self.assertEqual(
            main(["doctor", "--target", "beta", "--project", str(self.root)]), 2
        )

    @unittest.skipUnless(sys.platform == "darwin", "Beta CLI requires macOS")
    def test_beta_cli_end_to_end_with_fake_toolchain(self):
        tools = self.root / "bin"
        tools.mkdir()
        fake = tools / "fixture-tool"
        fake.write_text(f"#!{sys.executable}\n" + r"""
import json
import os
import plistlib
import sys
import zipfile
from pathlib import Path
name = Path(sys.argv[0]).name
args = sys.argv[1:]
root = Path(os.environ["BETA_TEST_ROOT"])
if name == "ruby":
    print("ruby 3.4.0")
elif name == "bundle":
    if args == ["--version"]:
        print("Bundler version 4.0.20")
    elif args[:1] == ["exec"]:
        assert args == ["exec", "fastlane", "ios", "tbqrfly_upload"]
        assert Path(os.environ["TBQRFLY_BETA_IPA"]).is_file()
        (root / "uploaded").write_text("success")
elif name == "git":
    if args[:1] == ["rev-parse"]:
        print("main" if "--abbrev-ref" in args else "abc" if "--short" in args else "abcdef")
    else:
        print("- test change")
elif name == "flutter":
    assert args[:2] == ["build", "ipa"]
    assert "--dart-define=FLAVOR=prod" in args
    assert "--dart-define=GIT_COMMIT=abc" in args
    ipa = root / "build/ios/ipa/demo.ipa"
    ipa.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ipa, "w") as archive:
        archive.writestr("Payload/Demo.app/Info.plist", plistlib.dumps({
            "CFBundleIdentifier": "com.example.demo",
            "CFBundleShortVersionString": "1.2.3",
            "CFBundleVersion": "4",
        }))
""")
        fake.chmod(0o755)
        for command in ("flutter", "git", "bundle", "ruby", "xcrun", "xcodebuild"):
            (tools / command).symlink_to(fake)
        self.raw["next_commands"] = [
            f"{sys.executable} -c \"from pathlib import Path; Path('after-upload').touch()\""
        ]
        self.save()
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tbqrfly",
                "beta",
                "--project",
                str(self.root),
                "--platform",
                "all",
            ],
            cwd=self.root,
            env={
                **os.environ,
                "PATH": f"{tools}{os.pathsep}{os.defpath}",
                "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                "BETA_TEST_ROOT": str(self.root),
            },
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "uploaded").is_file())
        self.assertTrue((self.root / "after-upload").is_file())
        self.assertIn("等待 Apple 处理", result.stdout)

    def test_beta_forks_keep_beta_command_and_omit_env(self):
        shared = self.directory / ".shared_test.json"
        with (
            patch("tbqrfly.workflow.fork._tbqrfly_bin", return_value="tbqrfly"),
            patch("tbqrfly.workflow.fork.subprocess.Popen") as popen,
            patch("tbqrfly.workflow.fork.threading.Thread"),
        ):
            spawn_platform_inline(
                self.root,
                env="prod",
                platform="ios",
                shared_state=shared,
                command="beta",
            )
            command = popen.call_args.args[0]
            self.assertEqual(command[1], "beta")
            self.assertNotIn("--env", command)
        with (
            patch("tbqrfly.workflow.fork.platform_system", return_value="Darwin"),
            patch("tbqrfly.workflow.fork._tbqrfly_bin", return_value="tbqrfly"),
            patch(
                "tbqrfly.workflow.fork.subprocess.run", return_value=Mock(returncode=0)
            ) as run,
        ):
            spawn_platform_terminal(
                self.root,
                env="prod",
                platform="ios",
                shared_state=shared,
                command="beta",
            )
            script = Path(run.call_args.args[0][-1]).read_text()
            self.assertIn("tbqrfly beta", script)
            self.assertNotIn("--env", script)


if __name__ == "__main__":
    unittest.main()
