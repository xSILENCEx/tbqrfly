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
    """Open a new macOS Terminal window to run a single-platform pipeline."""
    if platform_system() != "Darwin":
        return False

    command = (
        f"cd {shlex.quote(str(project_root))} && "
        f"{shlex.quote(_tbqrfly_bin())} run "
        f"--env {shlex.quote(env)} "
        f"--platform {shlex.quote(platform)} "
        f"--shared-state {shlex.quote(str(shared_state))} ; "
        f"echo; echo '[tbqrfly] {platform} 分叉结束，退出码 '$?; "
        f"exec bash"
    )
    script = f'''
tell application "Terminal"
    activate
    do script {json.dumps(command)}
end tell
'''
    completed = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        print(f"警告: 无法新开终端（{completed.stderr.strip() or completed.stdout.strip()}）")
        return False
    print(f"已新开终端构建平台: {platform}")
    return True


def platform_system() -> str:
    return platform.system()
