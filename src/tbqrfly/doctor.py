from __future__ import annotations

import platform
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import RuntimeConfig, config_dir, find_project_root


@dataclass
class CheckItem:
    level: str  # ok | warn | fail
    name: str
    message: str


def _which(command: str) -> str | None:
    return shutil.which(command)


def check_python() -> CheckItem:
    version = sys.version_info
    if version < (3, 9):
        return CheckItem(
            "fail",
            "python",
            f"需要 Python ≥ 3.9，当前为 {version.major}.{version.minor}",
        )
    return CheckItem("ok", "python", f"{version.major}.{version.minor}.{version.micro}")


def check_flutter_project(project_root: Path | None) -> CheckItem:
    if project_root is None or not (project_root / "pubspec.yaml").is_file():
        return CheckItem("fail", "flutter_project", "未找到含 pubspec.yaml 的 Flutter 项目")
    return CheckItem("ok", "flutter_project", str(project_root))


def check_command(name: str, command: str, *, fatal: bool = True) -> CheckItem:
    path = _which(command)
    if path:
        return CheckItem("ok", name, path)
    level = "fail" if fatal else "warn"
    return CheckItem(level, name, f"未找到命令 `{command}`，请安装并加入 PATH")


def check_ios_toolchain(needed: bool) -> CheckItem:
    if not needed:
        return CheckItem("ok", "xcodebuild", "当前未配置 ios，跳过")
    path = _which("xcodebuild")
    if path:
        return CheckItem("ok", "xcodebuild", path)
    return CheckItem("warn", "xcodebuild", "未找到 xcodebuild，iOS 构建可能失败")


def check_android_toolchain(needed: bool) -> CheckItem:
    if not needed:
        return CheckItem("ok", "android_sdk", "当前未配置 android，跳过")
    java = _which("java")
    sdk = os_environ_android_home()
    if java and sdk:
        return CheckItem("ok", "android_sdk", f"java={java}; ANDROID_HOME={sdk}")
    if java:
        return CheckItem(
            "warn",
            "android_sdk",
            "已找到 java，但未设置 ANDROID_HOME/ANDROID_SDK_ROOT",
        )
    return CheckItem("warn", "android_sdk", "未检测到 java / Android SDK，Android 构建可能失败")


def os_environ_android_home() -> str:
    import os

    return os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT") or ""


def check_parallel_terminal() -> CheckItem:
    if platform.system() != "Darwin":
        return CheckItem(
            "warn",
            "parallel_terminal",
            "非 macOS，多平台将改为当前终端串行",
        )
    return CheckItem("ok", "parallel_terminal", "macOS，可尝试新开终端并行")


def check_llm_command(config: RuntimeConfig | None) -> CheckItem:
    if config is None:
        return CheckItem("ok", "llm", "尚未加载配置")
    if not config.llm.get("enabled", True):
        return CheckItem("ok", "llm", "已禁用")
    command = str(config.llm.get("command") or "claude -p").strip()
    exe = command.split()[0] if command else ""
    if not exe:
        return CheckItem("warn", "llm", "llm.command 为空，将回退 git log")
    path = _which(exe)
    if path:
        return CheckItem("ok", "llm", f"{exe} -> {path}")
    return CheckItem("warn", "llm", f"未找到 `{exe}`，run 时将回退 git log")


def collect_checks(
    *,
    project_root: Path | None = None,
    config: RuntimeConfig | None = None,
    include_llm: bool = False,
    for_init: bool = False,
) -> list[CheckItem]:
    root = project_root or find_project_root()
    items = [
        check_python(),
        check_flutter_project(root),
        check_command("flutter", "flutter", fatal=True),
        check_command("git", "git", fatal=True),
    ]

    platforms: set[str] = set()
    if config is not None:
        for env_block in config.builds.values():
            platforms.update(env_block.keys())
    elif root is not None and (config_dir(root) / "builds.json").is_file():
        try:
            from .config import load_runtime_config

            config = load_runtime_config(root)
            for env_block in config.builds.values():
                platforms.update(env_block.keys())
        except Exception:
            platforms = {"android", "ios"}
    else:
        platforms = {"android", "ios"}

    items.append(check_ios_toolchain("ios" in platforms))
    items.append(check_android_toolchain("android" in platforms))
    if not for_init:
        items.append(check_parallel_terminal())
    if include_llm:
        items.append(check_llm_command(config))
    return items


def print_checks(items: list[CheckItem]) -> int:
    icons = {"ok": "✓", "warn": "!", "fail": "✗"}
    for item in items:
        print(f"{icons.get(item.level, '?')} [{item.level}] {item.name}: {item.message}")
    if any(item.level == "fail" for item in items):
        return 2
    if any(item.level == "warn" for item in items):
        return 1
    return 0


def has_fatal(items: list[CheckItem]) -> bool:
    return any(item.level == "fail" for item in items)
