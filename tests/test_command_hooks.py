from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from tbqrfly.config import ConfigError, RuntimeConfig, load_runtime_config
from tbqrfly.workflow.fork import (
    spawn_platform_terminal,
    terminal_status_path,
    wait_terminal_process,
)
from tbqrfly.workflow.pre_commands import run_commands
from tbqrfly.workflow.project_info import ProjectInfo
from tbqrfly.workflow.run import run_platform_pipeline, run_workflow


class CommandHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = []
        self.info = ProjectInfo("Demo", "1", "1", "abcdef", "abc", "main", "today")
        self.config = RuntimeConfig(
            project_root=self.root, app_name="Demo", default_env="dev", wait_timeout=1,
            artifacts={"android": ["*.apk"], "ios": ["*.ipa"]},
            builds={"dev": {name: {
                "zealot_channel_key": "key", "build_command": "build",
                "pre_commands": [f"pre-{name}"], "next_commands": [f"next-{name}"],
            } for name in ("android", "ios")}},
            zealot={"base_url": "url", "token": "token"}, feishu={}, llm={},
            terminal="inline", pre_commands=["global-pre"], next_commands=["global-next"],
            source_dir=self.root,
        )

    def hooks(self, root, commands, **kwargs):
        self.assertEqual(root, self.root)
        self.events.extend(commands)

    def platform_mocks(self, stack):
        prefix = "tbqrfly.workflow.run."
        stack.enter_context(patch(prefix + "run_commands", side_effect=self.hooks))
        stack.enter_context(patch(prefix + "find_reusable", return_value=None))
        stack.enter_context(patch(prefix + "run_build", side_effect=lambda *a: self.events.append("build")))
        stack.enter_context(patch(prefix + "find_after_build", return_value=Path(__file__)))
        stack.enter_context(patch(prefix + "upload_and_notify", side_effect=lambda *a, **k: self.events.append("upload")))
        return stack.enter_context(patch(prefix + "notify_failure"))

    def test_platform_order_including_reused_artifact(self):
        for reuse in (False, True):
            self.events.clear()
            with self.subTest(reuse=reuse), ExitStack() as stack:
                self.platform_mocks(stack)
                if reuse:
                    stack.enter_context(patch("tbqrfly.workflow.run.find_reusable", return_value=Path(__file__)))
                    stack.enter_context(patch("tbqrfly.workflow.run._ask_reuse", return_value=True))
                run_platform_pipeline(self.config, env="dev", platform="android", info=self.info, summary="")
                expected = ["pre-android"] + ([] if reuse else ["build"]) + ["upload", "next-android"]
                self.assertEqual(self.events, expected)

    def test_platform_failure_skips_next_commands(self):
        for step in ("run_commands", "run_build", "upload_and_notify"):
            with self.subTest(step=step), ExitStack() as stack:
                self.events.clear()
                failure = self.platform_mocks(stack)
                stack.enter_context(patch(f"tbqrfly.workflow.run.{step}", side_effect=RuntimeError("failed")))
                with self.assertRaisesRegex(RuntimeError, "failed"):
                    run_platform_pipeline(self.config, env="dev", platform="android", info=self.info, summary="")
                self.assertNotIn("next-android", self.events)
                failure.assert_called_once()

    def workflow_mocks(self, stack, terminal="inline", child_code=0):
        prefix = "tbqrfly.workflow.run."
        stack.enter_context(patch(prefix + "collect_checks", return_value=[]))
        stack.enter_context(patch(prefix + "load_runtime_config", return_value=self.config))
        stack.enter_context(patch(prefix + "run_pre_commands", side_effect=self.hooks))
        stack.enter_context(patch(prefix + "run_commands", side_effect=self.hooks))
        stack.enter_context(patch(prefix + "load_project_info", return_value=self.info))
        stack.enter_context(patch(prefix + "generate_summary", return_value="summary"))
        stack.enter_context(patch(prefix + "resolve_terminal_mode", return_value=terminal))
        stack.enter_context(patch(prefix + "spawn_platform_inline", side_effect=lambda *a, **k: self.events.append("spawn") or Mock()))
        stack.enter_context(patch(prefix + "spawn_platform_terminal", side_effect=lambda *a, **k: self.events.append("spawn") or True))
        for name in ("wait_inline_process", "wait_terminal_process"):
            stack.enter_context(patch(prefix + name, side_effect=lambda *a: self.events.append("wait") or child_code))
        stack.enter_context(patch(prefix + "run_platform_pipeline", side_effect=lambda *a, **k: self.events.append("primary")))
        return stack.enter_context(patch(prefix + "save_history"))

    def test_global_next_runs_once_after_all_platforms(self):
        for terminal in ("inline", "system"):
            with self.subTest(terminal=terminal), ExitStack() as stack:
                self.events.clear()
                history = self.workflow_mocks(stack, terminal)
                self.assertEqual(run_workflow(self.root, env="dev", platform="all"), 0)
                self.assertEqual(self.events, ["global-pre", "spawn", "primary", "wait", "global-next"])
                history.assert_called_once()
                self.assertFalse(list(self.root.glob(".tbqrfly/.shared_*.json")))

    def test_child_failure_skips_global_next_and_history(self):
        for terminal in ("inline", "system"):
            with self.subTest(terminal=terminal), ExitStack() as stack:
                self.events.clear()
                history = self.workflow_mocks(stack, terminal, child_code=1)
                self.assertEqual(run_workflow(self.root, env="dev", platform="all"), 1)
                self.assertNotIn("global-next", self.events)
                history.assert_not_called()

    def test_global_next_failure_sets_nonzero_exit(self):
        with ExitStack() as stack:
            history = self.workflow_mocks(stack)
            stack.enter_context(patch("tbqrfly.workflow.run.run_commands", side_effect=RuntimeError("hook failed")))
            self.assertEqual(run_workflow(self.root, env="dev", platform="android"), 1)
            history.assert_not_called()

    def test_primary_failure_skips_global_next_but_waits_for_child(self):
        with ExitStack() as stack:
            history = self.workflow_mocks(stack)
            stack.enter_context(patch(
                "tbqrfly.workflow.run.run_platform_pipeline",
                side_effect=RuntimeError("primary failed"),
            ))
            self.assertEqual(run_workflow(self.root, env="dev", platform="all"), 1)
            self.assertIn("wait", self.events)
            self.assertNotIn("global-next", self.events)
            history.assert_not_called()

    def test_no_global_next_preserves_detached_terminal_behavior(self):
        self.config.next_commands = []
        with ExitStack() as stack:
            self.workflow_mocks(stack, "system")
            self.assertEqual(run_workflow(self.root, env="dev", platform="all"), 0)
            self.assertNotIn("wait", self.events)

    def test_platform_next_failure_is_reported(self):
        def fail_next(root, commands, **kwargs):
            self.hooks(root, commands)
            if commands == ["next-android"]:
                raise RuntimeError("后置命令失败")

        with ExitStack() as stack:
            failure = self.platform_mocks(stack)
            stack.enter_context(patch(
                "tbqrfly.workflow.run.run_commands", side_effect=fail_next,
            ))
            with self.assertRaisesRegex(RuntimeError, "后置命令失败"):
                run_platform_pipeline(
                    self.config, env="dev", platform="android", info=self.info, summary="",
                )
            self.assertEqual(self.events, ["pre-android", "build", "upload", "next-android"])
            self.assertEqual(failure.call_args.kwargs["step"], "next_commands")

    def test_child_does_not_run_global_hooks(self):
        with ExitStack() as stack:
            self.workflow_mocks(stack)
            stack.enter_context(patch("tbqrfly.workflow.run.load_shared_state", return_value={
                "env": "dev", "summary": "summary", "info": vars(self.info),
            }))
            self.assertEqual(run_workflow(self.root, platform="android", shared_state=self.root / "state"), 0)
            self.assertEqual(self.events, ["primary"])

    def test_config_defaults_and_validation(self):
        directory = self.root / ".tbqrfly"
        directory.mkdir()
        (directory / "zealot.json").write_text("{}")
        (directory / "llm.json").write_text('{"enabled": false}')
        builds = {"builds": {"dev": {"android": {}}}}
        path = directory / "builds.json"
        path.write_text(json.dumps(builds))
        loaded = load_runtime_config(self.root)
        self.assertEqual(loaded.next_commands, [])
        self.assertEqual(loaded.platform_config("dev", "android")["next_commands"], [])
        for value in ("echo bad", [1], None, {}):
            for nested in (False, True):
                with self.subTest(value=value, nested=nested):
                    builds = {"builds": {"dev": {"android": {}}}}
                    target = builds["builds"]["dev"]["android"] if nested else builds
                    target["next_commands"] = value
                    path.write_text(json.dumps(builds))
                    with self.assertRaises(ConfigError):
                        load_runtime_config(self.root)
        builds = {"pre_commands": [" one ", ""], "next_commands": ["two"],
                  "builds": {"dev": {"android": {"pre_commands": ["three"], "next_commands": ["four"]}}}}
        path.write_text(json.dumps(builds))
        loaded = load_runtime_config(self.root)
        self.assertEqual(loaded.pre_commands, ["one"])
        self.assertEqual(loaded.next_commands, ["two"])
        self.assertEqual(loaded.platform_config("dev", "android")["next_commands"], ["four"])

    def test_command_runner_stops_on_failure_and_preserves_arguments(self):
        with patch("tbqrfly.workflow.pre_commands.subprocess.run", return_value=Mock(returncode=3)) as run:
            with self.assertRaisesRegex(RuntimeError, "后置命令失败"):
                run_commands(self.root, ['echo "two words"', "never"], label="后置命令")
            run.assert_called_once_with(["echo", "two words"], cwd=self.root, check=False)

    def test_terminal_script_reports_exit_before_window_closes(self):
        # Execute generated scripts directly: no Terminal window or external service.
        for code in (0, 7):
            with self.subTest(code=code):
                shared = self.root / ".tbqrfly" / f".shared_{code}.json"
                fake = self.root / "fake tool"
                fake.write_text(f"#!/bin/sh\nexit {code}\n")
                fake.chmod(0o755)
                with patch("tbqrfly.workflow.fork.platform_system", return_value="Darwin"), patch(
                    "tbqrfly.workflow.fork._tbqrfly_bin", return_value=str(fake)
                ), patch("tbqrfly.workflow.fork.subprocess.run", return_value=Mock(returncode=0)) as launch:
                    self.assertTrue(spawn_platform_terminal(self.root, env="dev", platform="android", shared_state=shared))
                    runner = launch.call_args.args[0][-1]
                subprocess.run(["bash", runner], input="\n", text=True, check=False)
                self.assertEqual(wait_terminal_process(shared, "android"), code)
                self.assertFalse(terminal_status_path(shared, "android").exists())


if __name__ == "__main__":
    unittest.main()
