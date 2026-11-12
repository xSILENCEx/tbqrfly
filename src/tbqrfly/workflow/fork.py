from __future__ import annotations

import json
import platform
import shlex
import subprocess
import sys
import time
from pathlib import Path

from ..config import config_dir


def write_shared_state(
    project_root: Path,
    *,
    env: str,
    summary: str,
    info: dict,
) -> Path:
    directory = config_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f".shared_{int(time.time() * 1000)}.json"
    payload = {
        "env": env,
        "summary": summary,
        "info": info,
        "created_at": time.time(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_shared_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tbqrfly_bin() -> str:
    return sys.argv[0] if sys.argv else "tbqrfly"


def spawn_platform_terminal(
    project_root: Path,
    *,
    env: str,
    platform: str,
    shared_state: Path,
) -> bool:
    """Open a new macOS Terminal window to run a single-platform pipeline.

    Uses a ``.command`` file + ``open`` instead of AppleScript, avoiding
    quoting bugs and Automation permission prompts.
    """
    if platform_system() != "Darwin":
        return False

    directory = config_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    runner = directory / f"run_{platform}_{int(time.time() * 1000)}.command"
    script_body = "\n".join(
        [
            "#!/bin/bash",
            "set +e",
            f"cd {shlex.quote(str(project_root))}",
            (
                f"{shlex.quote(_tbqrfly_bin())} run "
                f"--env {shlex.quote(env)} "
                f"--platform {shlex.quote(platform)} "
                f"--shared-state {shlex.quote(str(shared_state))}"
            ),
            "code=$?",
            "echo",
            f'echo "[tbqrfly] {platform} 分叉结束，退出码 $code"',
            'echo "按回车关闭窗口..."',
            "read -r _",
            "exit \"$code\"",
            "",
        ]
    )
    runner.write_text(script_body, encoding="utf-8")
    runner.chmod(0o755)

    completed = subprocess.run(
        ["open", "-a", "Terminal", str(runner)],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        print(f"警告: 无法新开终端（{detail or f'exit {completed.returncode}'}）")
        return False
    print(f"已新开终端构建平台: {platform}")
    print(f"  脚本: {runner}")
    return True


def platform_system() -> str:
    return platform.system()
