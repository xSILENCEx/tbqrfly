from __future__ import annotations

import platform
import re
import subprocess
from pathlib import Path

from .beta_config import load_beta_config, validate_beta
from .config import ConfigError
from .doctor import CheckItem, check_command, check_flutter_project, check_python


def collect_beta_checks(
    project_root: Path, platforms: list[str] | None = None
) -> list[CheckItem]:
    items = [check_python(), check_flutter_project(project_root)]
    try:
        config = load_beta_config(project_root)
        validate_beta(config, "prod", platforms or config.platforms_for("prod"))
        items.append(CheckItem("ok", "beta_config", "Beta 配置有效（prod）"))
    except ConfigError as exc:
        items.append(CheckItem("fail", "beta_config", str(exc)))
        return items
    if platform.system() != "Darwin":
        items.append(CheckItem("fail", "macos", "TestFlight 构建与上传需要 macOS"))
    for command in ("flutter", "git", "xcodebuild", "xcrun", "ruby", "bundle"):
        items.append(check_command(command, command))
    if any(item.name == "ruby" and item.level == "ok" for item in items):
        try:
            result = subprocess.run(
                ["ruby", "--version"], capture_output=True, text=True, check=False
            )
            match = re.search(r"ruby (\d+)\.(\d+)", result.stdout)
            supported = (
                result.returncode == 0
                and match
                and tuple(map(int, match.groups())) >= (3, 2)
            )
        except OSError:
            supported = False
        items.append(
            CheckItem(
                "ok" if supported else "fail",
                "ruby_version",
                "Fastlane 需要 Ruby >= 3.2",
            )
        )
    if any(item.name == "bundle" and item.level == "ok" for item in items):
        try:
            result = subprocess.run(
                ["bundle", "--version"], capture_output=True, text=True, check=False
            )
            match = re.search(r"(\d+)\.(\d+)", result.stdout)
            supported = (
                result.returncode == 0
                and match
                and tuple(map(int, match.groups())) >= (2, 4)
            )
        except OSError:
            supported = False
        items.append(
            CheckItem(
                "ok" if supported else "fail", "bundler_version", "需要 Bundler >= 2.4"
            )
        )
    return items
