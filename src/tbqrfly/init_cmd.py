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
    matches = (
        list((project_root / "ios").rglob("ExportOptions*.plist"))
        if (project_root / "ios").is_dir()
        else []
    )
    return matches[0] if matches else None


def _parse_ios_signing(project_root: Path) -> tuple[str, str]:
    """Return (team_id, signing_style) from Runner Xcode project settings."""
    pbxproj = project_root / "ios" / "Runner.xcodeproj" / "project.pbxproj"
    if not pbxproj.is_file():
        return "", "automatic"

    text = pbxproj.read_text(encoding="utf-8", errors="ignore")
    # Prefer Runner app configs: DEVELOPMENT_TEAM near a non-test bundle id.
    team = ""
    for match in re.finditer(
        r"DEVELOPMENT_TEAM\s*=\s*([A-Z0-9]+)\s*;([\s\S]{0,400}?PRODUCT_BUNDLE_IDENTIFIER\s*=\s*([^;]+);)",
        text,
    ):
        bundle = match.group(3).strip().strip('"')
        if "RunnerTests" in bundle or bundle.endswith("Tests"):
            continue
        team = match.group(1).strip()
        break
    if not team:
        match = re.search(r"DEVELOPMENT_TEAM\s*=\s*([A-Z0-9]+)\s*;", text)
        if match:
            team = match.group(1).strip()

    style = "automatic"
    style_match = re.search(r"CODE_SIGN_STYLE\s*=\s*(\w+)\s*;", text)
    if style_match and style_match.group(1).lower() == "manual":
        style = "manual"
    return team, style


def _generate_export_options_plist(team_id: str, signing_style: str = "automatic") -> str:
    # Zealot QR installs usually use ad-hoc; automatic signing matches most Flutter setups.
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>compileBitcode</key>
	<false/>
	<key>method</key>
	<string>ad-hoc</string>
	<key>signingStyle</key>
	<string>{signing_style}</string>
	<key>stripSwiftSymbols</key>
	<true/>
	<key>teamID</key>
	<string>{team_id}</string>
	<key>thinning</key>
	<string>&lt;none&gt;</string>
</dict>
</plist>
"""


def ensure_export_options(project_root: Path, directory: Path) -> str:
    """Copy or generate ExportOptions.plist into .tbqrfly; return relative path or ''."""
    dest = directory / "ExportOptions.plist"
    source_export = _find_export_options(project_root)
    if source_export is not None:
        shutil.copy2(source_export, dest)
        return ".tbqrfly/ExportOptions.plist"

    team_id, signing_style = _parse_ios_signing(project_root)
    if not team_id:
        return ""

    dest.write_text(
        _generate_export_options_plist(team_id, signing_style),
        encoding="utf-8",
    )
    return ".tbqrfly/ExportOptions.plist"


def _write_builds(project_root: Path, target: Path, app_name: str, export_options: str) -> None:
    del project_root
    payload = json.loads(_template_text("builds.json"))
    payload["app_name"] = app_name
    for env_block in payload.get("builds", {}).values():
        if isinstance(env_block, dict) and "ios" in env_block:
            env_block["ios"]["export_options"] = export_options
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _patch_builds_export_options(builds_path: Path, export_options: str) -> None:
    if not builds_path.is_file() or not export_options:
        return
    payload = json.loads(builds_path.read_text(encoding="utf-8"))
    builds = payload.get("builds")
    if not isinstance(builds, dict):
        return
    changed = False
    for env_block in builds.values():
        if isinstance(env_block, dict) and isinstance(env_block.get("ios"), dict):
            if not str(env_block["ios"].get("export_options") or "").strip():
                env_block["ios"]["export_options"] = export_options
                changed = True
    if changed:
        builds_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def repair_export_options(project_root: Path) -> int:
    """Generate ExportOptions.plist for an already-initialized project."""
    directory = config_dir(project_root)
    if not directory.is_dir():
        raise ConfigError(f"{directory} 不存在，请先执行 tbqrfly init")
    export_rel = ensure_export_options(project_root, directory)
    if not export_rel:
        print(
            "未能从 ios/Runner.xcodeproj 读取 DEVELOPMENT_TEAM，"
            "请先在 Xcode 为 Runner 配置 Signing Team"
        )
        return 2
    _patch_builds_export_options(directory / "builds.json", export_rel)
    team_id, signing = _parse_ios_signing(project_root)
    print(f"已生成: {directory / 'ExportOptions.plist'}")
    print(f"teamID: {team_id}  signingStyle: {signing}")
    print(f"已回填 builds.json 中空的 ios.export_options -> {export_rel}")
    return 0


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

    export_rel = ensure_export_options(project_root, directory)
    team_id, _signing = _parse_ios_signing(project_root)

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
        if team_id:
            print(f"DEVELOPMENT_TEAM: {team_id}（已写入 ExportOptions.plist）")
    else:
        print(
            "export_options: 未能从 ios/Runner.xcodeproj 读取 DEVELOPMENT_TEAM，"
            "已留空；请在 Xcode 配置团队后重新 init --force，或手动填写 builds.json"
        )

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
