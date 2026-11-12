from __future__ import annotations

import json
import os
import platform
import shlex
import subprocess
import sys
import threading
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


def running_in_ide() -> bool:
    """Detect Cursor / VS Code / JetBrains integrated terminal."""
    term_program = os.environ.get("TERM_PROGRAM", "").lower()
    if term_program == "vscode":
        return True
    if os.environ.get("VSCODE_PID") or os.environ.get("CURSOR_TRACE_ID"):
        return True
    if os.environ.get("TERM_PROGRAM_VERSION") and "vscode" in term_program:
        return True
    emulator = os.environ.get("TERMINAL_EMULATOR", "")
    if emulator.startswith("JetBrains"):
        return True
    return False


def resolve_terminal_mode(configured: str) -> str:
    mode = (configured or "auto").strip().lower()
    if mode == "auto":
        return "inline" if running_in_ide() else "system"
    if mode in {"system", "inline"}:
        return mode
    return "system"


def spawn_platform_terminal(
    project_root: Path,
    *,
    env: str,
    platform: str,
    shared_state: Path,
) -> bool:
    """Open a new macOS Terminal.app window via a .command file."""
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
            'exit "$code"',
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
        print(f"警告: 无法新开系统终端（{detail or f'exit {completed.returncode}'}）")
        return False
    print(f"已新开系统终端构建平台: {platform}")
    print(f"  脚本: {runner}")
    return True


def spawn_platform_inline(
    project_root: Path,
    *,
    env: str,
    platform: str,
    shared_state: Path,
) -> subprocess.Popen[str]:
    """Start a platform pipeline as a child process; stream logs with a prefix.

    Cursor/VS Code have no stable public API to open a *new* integrated terminal
    panel from an external CLI, so we keep parallel work inside the current IDE
    terminal instead of launching Terminal.app.
    """
    command = [
        _tbqrfly_bin(),
        "run",
        "--env",
        env,
        "--platform",
        platform,
        "--shared-state",
        str(shared_state),
    ]
    print(f"已在当前 IDE 终端并行启动平台: {platform}")
    process = subprocess.Popen(
        command,
        cwd=project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    def _pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            print(f"[{platform}] {line}", end="" if line.endswith("\n") else "\n")

    thread = threading.Thread(target=_pump, name=f"tbqrfly-{platform}", daemon=True)
    thread.start()
    # Attach thread for join by caller via process attribute.
    process._tbqrfly_pump = thread  # type: ignore[attr-defined]
    return process


def wait_inline_process(platform: str, process: subprocess.Popen[str]) -> int:
    pump = getattr(process, "_tbqrfly_pump", None)
    if pump is not None:
        pump.join()
    code = process.wait()
    print(f"[tbqrfly] {platform} 并行任务结束，退出码 {code}")
    return code


def platform_system() -> str:
    return platform.system()
