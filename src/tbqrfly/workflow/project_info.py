from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class ProjectInfo:
    app_name: str
    version: str
    build_number: str
    commit: str
    commit_short: str
    branch: str
    build_date: str


def _git(project_root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=project_root,
        text=True,
        stderr=subprocess.STDOUT,
    ).strip()


def load_project_info(project_root: Path, app_name: str) -> ProjectInfo:
    pubspec = (project_root / "pubspec.yaml").read_text(encoding="utf-8")
    version_match = re.search(r"^version:\s*([^\s#]+)", pubspec, re.M)
    if not version_match:
        raise RuntimeError("pubspec.yaml 缺少 version")
    full = version_match.group(1)
    if "+" in full:
        version, build_number = full.split("+", 1)
    else:
        version, build_number = full, ""
    commit = _git(project_root, "rev-parse", "HEAD")
    return ProjectInfo(
        app_name=app_name,
        version=version,
        build_number=build_number,
        commit=commit,
        commit_short=_git(project_root, "rev-parse", "--short", "HEAD"),
        branch=_git(project_root, "rev-parse", "--abbrev-ref", "HEAD"),
        build_date=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
