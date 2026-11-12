from __future__ import annotations

import shlex
import subprocess
from pathlib import Path


def run_pre_commands(project_root: Path, commands: list[str]) -> None:
    """Run user-defined pre-commands after option selection, before packaging."""
    if not commands:
        print("前置命令为空，跳过")
        return

    print("\n===== 前置命令 =====")
    for raw in commands:
        command = shlex.split(raw)
        if not command:
            continue
        print(f"  执行: {raw}")
        completed = subprocess.run(command, cwd=project_root, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"前置命令失败（退出码 {completed.returncode}）: {raw}"
            )
    print("前置命令全部完成")
