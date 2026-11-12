from __future__ import annotations

import shlex
import subprocess
from collections import deque
from pathlib import Path

from ..config import RuntimeConfig
from .project_info import ProjectInfo


def _ios_export_error(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("error: exportArchive"):
        return stripped.removeprefix("error: exportArchive").strip()
    if "Encountered error while creating the IPA" in stripped:
        return "IPA 导出失败"
    return ""


def run_build(
    config: RuntimeConfig,
    env: str,
    platform: str,
    info: ProjectInfo,
) -> None:
    block = config.platform_config(env, platform)
    command = shlex.split(str(block.get("build_command") or ""))
    if not command:
        raise RuntimeError(f"builds.{env}.{platform}.build_command 为空")

    # Append GIT_COMMIT dart-define without rewriting the user command body.
    commit_define = f"--dart-define=GIT_COMMIT={info.commit_short}"
    command = [item for item in command if not item.startswith("--dart-define=GIT_COMMIT=")]
    command.append(commit_define)

    if platform == "ios":
        export_options = str(block.get("export_options") or "").strip()
        if export_options:
            path = Path(export_options).expanduser()
            if not path.is_absolute():
                path = config.project_root / path
            if path.is_file():
                flag = f"--export-options-plist={path}"
                if not any(item.startswith("--export-options-plist=") for item in command):
                    command.append(flag)
            else:
                print(f"警告: export_options 不存在: {path}")

    print(f"  执行: {' '.join(command)}")
    process = subprocess.Popen(
        command,
        cwd=config.project_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    ios_export_error = ""
    output_tail: deque[str] = deque(maxlen=120)
    for line in process.stdout:
        line = line.rstrip()
        print(line)
        if line:
            output_tail.append(line)
        if platform == "ios":
            detected = _ios_export_error(line)
            if detected and detected != "IPA 导出失败":
                ios_export_error = detected
            elif detected and not ios_export_error:
                ios_export_error = detected
    code = process.wait()
    if code != 0:
        detail = "\n".join(list(output_tail)[-12:])
        raise RuntimeError(f"构建失败，退出码 {code}\n{detail}")
    if ios_export_error:
        raise RuntimeError(f"IPA 导出失败: {ios_export_error}")
