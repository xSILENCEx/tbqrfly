from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


def run_pre_commands(project_root: Path, commands: list[str]) -> None:
    run_commands(project_root, commands, label="前置命令")


def run_commands(project_root: Path, commands: list[str], *, label: str) -> None:
    """Run commands in order without a shell; stop at the first failure."""
    if not commands:
        print(f"{label}为空，跳过")
        return

    print(f"\n===== {label} =====")
    for raw in commands:
        command = shlex.split(raw)
        if not command:
            continue
        print(f"  执行: {raw}")
        completed = subprocess.run(command, cwd=project_root, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"{label}失败（退出码 {completed.returncode}）: {raw}"
            )
    print(f"{label}全部完成")
