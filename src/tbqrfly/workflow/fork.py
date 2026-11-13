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
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return path


def load_shared_state(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_runtime_temps(
    project_root: Path,
    *,
    keep: Path | None = None,
    max_age_seconds: int = 2 * 60 * 60,
) -> None:
    """Remove stale .shared_*.json and run_*.command leftovers under .tbqrfly/."""
    directory = config_dir(project_root)
    if not directory.is_dir():
        return
    now = time.time()
    keep_resolved = keep.resolve() if keep is not None else None
    patterns = (
        ".shared_*.json",
        ".shared_*.status",
        ".shared_*.status.tmp",
        "run_*.command",
    )
    for pattern in patterns:
        for path in directory.glob(pattern):
            try:
                if keep_resolved is not None and path.resolve() == keep_resolved:
                    continue
                age = now - path.stat().st_mtime
                if age >= max_age_seconds:
                    path.unlink(missing_ok=True)
            except OSError:
                continue


def remove_path_quiet(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


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
    command: str = "run",
) -> bool:
    """Open a new macOS Terminal.app window via a .command file."""
    if platform_system() != "Darwin":
        return False

    directory = config_dir(project_root)
    directory.mkdir(parents=True, exist_ok=True)
    runner = directory / f"run_{platform}_{int(time.time() * 1000)}.command"
    status = terminal_status_path(shared_state, platform)
    temporary_status = status.with_suffix(".status.tmp")
    write_status = (
        f'printf "%s\\n" "$code" > {shlex.quote(str(temporary_status))}'
        f" && mv {shlex.quote(str(temporary_status))} {shlex.quote(str(status))}"
    )
    exit_trap = f"code=$?; {write_status}"
    script_body = "\n".join(
        [
            "#!/bin/bash",
            "set +e",
            f"trap {shlex.quote(exit_trap)} EXIT",
            f"cd {shlex.quote(str(project_root))}",
            (
                f"{shlex.quote(_tbqrfly_bin())} {shlex.quote(command)} "
                + (f"--env {shlex.quote(env)} " if command == "run" else "")
                + f"--platform {shlex.quote(platform)} "
                f"--shared-state {shlex.quote(str(shared_state))}"
            ),
            "code=$?",
            write_status,
            "trap - EXIT",
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


def terminal_status_path(shared_state: Path, platform: str) -> Path:
    return shared_state.with_name(f"{shared_state.stem}_{platform}.status")


def wait_terminal_process(shared_state: Path, platform: str) -> int:
    status = terminal_status_path(shared_state, platform)
    print(f"等待系统终端平台结束: {platform}")
    while not status.is_file():
        time.sleep(0.2)
    try:
        code = int(status.read_text().strip())
    except (OSError, ValueError):
        code = 1
    remove_path_quiet(status)
    print(f"[tbqrfly] {platform} 系统终端任务结束，退出码 {code}")
    return code


def spawn_platform_inline(
    project_root: Path,
    *,
    env: str,
    platform: str,
    shared_state: Path,
    command: str = "run",
) -> subprocess.Popen[str]:
    """Start a platform pipeline as a child process; stream logs with a prefix.

    Cursor/VS Code have no stable public API to open a *new* integrated terminal
    panel from an external CLI, so we keep parallel work inside the current IDE
    terminal instead of launching Terminal.app.
    """
    invocation = [
        _tbqrfly_bin(),
        command,
        *(["--env", env] if command == "run" else []),
        "--platform",
        platform,
        "--shared-state",
        str(shared_state),
    ]
    print(f"已在当前 IDE 终端并行启动平台: {platform}")
    process = subprocess.Popen(
        invocation,
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
