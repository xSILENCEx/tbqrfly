from __future__ import annotations

import json
import re
import shutil
from importlib import resources
from pathlib import Path

from .config import CONFIG_DIR_NAME, ConfigError, config_dir
from .doctor import collect_checks, has_fatal, print_checks


def _template_text(name: str) -> str:
    return (
        resources.files("tbqrfly")
        .joinpath("templates", name)
        .read_text(encoding="utf-8")
    )


def detect_app_name(project_root: Path) -> str:
    pubspec = project_root / "pubspec.yaml"
    if pubspec.is_file():
        match = re.search(r"^name:\s*([^\s#]+)", pubspec.read_text(encoding="utf-8"), re.M)
        if match:
            return match.group(1)
    manifest = project_root / "android" / "app" / "src" / "main" / "AndroidManifest.xml"
    if manifest.is_file():
        match = re.search(r'android:label="([^"]+)"', manifest.read_text(encoding="utf-8"))
        if match and not match.group(1).startswith("@"):
            return match.group(1)
    for plist in (project_root / "ios").rglob("Info.plist"):
        text = plist.read_text(encoding="utf-8", errors="ignore")
        for key in ("CFBundleDisplayName", "CFBundleName"):
            match = re.search(
                rf"<key>{key}</key>\s*<string>([^<]+)</string>", text
            )
            if match and not match.group(1).startswith("$("):
                return match.group(1)
    return project_root.name


def _ensure_gitignore(project_root: Path) -> None:
    gitignore = project_root / ".gitignore"
    entry = f"{CONFIG_DIR_NAME}/"
    if gitignore.is_file():
        content = gitignore.read_text(encoding="utf-8")
        if CONFIG_DIR_NAME in content:
            return
        separator = "" if content.endswith("\n") or not content else "\n"
        gitignore.write_text(content + f"{separator}\n# tbqrfly local config\n{entry}\n", encoding="utf-8")
    else:
        gitignore.write_text(f"# tbqrfly local config\n{entry}\n", encoding="utf-8")


def _find_export_options(project_root: Path) -> Path | None:
    candidates = [
        project_root / "ios" / "ExportOptions.plist",
        project_root / "ios" / "ExportOptions-AdHoc.plist",
        project_root / ".release" / "ExportOptions.plist",
    ]
    for path in candidates:
        if path.is_file():
            return path
    matches = list((project_root / "ios").rglob("ExportOptions*.plist")) if (project_root / "ios").is_dir() else []
    return matches[0] if matches else None


def _write_builds(project_root: Path, target: Path, app_name: str, export_options: str) -> None:
    payload = json.loads(_template_text("builds.json"))
    payload["app_name"] = app_name
    for env_block in payload.get("builds", {}).values():
        if isinstance(env_block, dict) and "ios" in env_block:
            env_block["ios"]["export_options"] = export_options
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_init(project_root: Path, *, force: bool = False) -> int:
    checks = collect_checks(project_root=project_root, for_init=True, include_llm=False)
    print("===== 环境检查 =====")
    print_checks(checks)
    if has_fatal(checks) and not force:
        print("\n存在致命问题，已中止 init。修复后重试，或使用 --force 强制写入模板。")
        return 2

    directory = config_dir(project_root)
    if directory.exists() and not force:
        raise ConfigError(f"{directory} 已存在；如需覆盖请使用 --force")

    directory.mkdir(parents=True, exist_ok=True)
    app_name = detect_app_name(project_root)

    export_rel = ""
    source_export = _find_export_options(project_root)
    if source_export is not None:
        dest = directory / "ExportOptions.plist"
        shutil.copy2(source_export, dest)
        export_rel = ".tbqrfly/ExportOptions.plist"

    files = {
        "zealot.json": _template_text("zealot.json"),
        "feishu.json": _template_text("feishu.json"),
        "llm.json": _template_text("llm.json"),
        "prompt.md": _template_text("prompt.md"),
        "message.md": _template_text("message.md"),
        "commit_history.json": _template_text("commit_history.json"),
    }
    for name, content in files.items():
        (directory / name).write_text(content, encoding="utf-8")
    _write_builds(project_root, directory / "builds.json", app_name, export_rel)
    _ensure_gitignore(project_root)

    print(f"\n已初始化: {directory}")
    print(f"app_name: {app_name}")
    if export_rel:
        print(f"export_options: {export_rel}")
    else:
        print("export_options: 未能推断，已留空，请按需填写 builds.json 中 ios.export_options")

    warnings = [item for item in checks if item.level == "warn"]
    if warnings:
        print("\n===== 警告 =====")
        for item in warnings:
            print(f"- {item.name}: {item.message}")

    print("\n===== 请填写以下配置 =====")
    print(f"- {directory / 'zealot.json'}  → base_url / token")
    print(f"- {directory / 'builds.json'}  → 各环境 zealot_channel_key（及按需调整 build_command）")
    print(f"- {directory / 'feishu.json'}  → webhook_url / app_id / app_secret（可选）")
    print(f"- {directory / 'llm.json'}     → enabled / command / prompt（可选）")
    return 0 if not has_fatal(checks) else 2
