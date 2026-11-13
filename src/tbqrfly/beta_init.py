from __future__ import annotations

import json
import plistlib
from pathlib import Path

from .config import ConfigError, config_dir
from .init_cmd import (
    _ensure_gitignore,
    _parse_ios_signing,
    _template_text,
    detect_app_name,
)
from .vscode_tasks import ensure_vscode_tbqrfly_task


def init_beta(project_root: Path, *, force: bool = False) -> int:
    directory = config_dir(project_root)
    target = directory / "beta.json"
    if target.exists() and not force:
        raise ConfigError("beta.json 已存在；如需覆盖请使用 --force")
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_template_text("beta.json"))
    payload["app_name"] = detect_app_name(project_root)
    export = project_root / "ios" / "ExportOptions-AppStore.plist"
    if not export.is_file():
        team, signing = _parse_ios_signing(project_root)
        if team:
            generated = directory / "ExportOptions-AppStore.plist"
            if not generated.exists():
                with generated.open("wb") as file:
                    plistlib.dump(
                        {
                            "method": "app-store-connect",
                            "destination": "export",
                            "teamID": team,
                            "signingStyle": signing,
                            "manageAppVersionAndBuildNumber": False,
                            "stripSwiftSymbols": True,
                            "uploadSymbols": True,
                        },
                        file,
                    )
            payload["builds"]["ios"][
                "export_options"
            ] = ".tbqrfly/ExportOptions-AppStore.plist"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    feishu = directory / "feishu.json"
    if not feishu.exists():
        feishu.write_text(_template_text("feishu.json"), encoding="utf-8")
    _ensure_gitignore(project_root)
    try:
        print(ensure_vscode_tbqrfly_task(project_root, target="beta"))
    except ValueError as exc:
        print(f"写入 VS Code 任务失败: {exc}")
    print(f"已初始化: {target}")
    print(
        "请填写 builds.ios.publish 的 Bundle ID 与 Apple API Key 配置，并检查 prod 构建命令和 App Store 导出配置。"
    )
    return 0
