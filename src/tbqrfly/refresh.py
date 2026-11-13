from __future__ import annotations

import copy
import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, config_dir
from .init_cmd import _template_text, detect_app_name


@dataclass
class ConfigUpdate:
    path: Path
    before: bytes | None
    after: bytes


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_object(content: bytes, name: str) -> dict:
    try:
        payload = json.loads(content, object_pairs_hook=_unique_object)
    except (ValueError, UnicodeError) as exc:
        raise ConfigError(f"{name} 不是有效的 JSON，未修改任何配置") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{name} 根节点必须是对象，未修改任何配置")
    return payload


def _merge_missing(current: dict, defaults: dict, location: str) -> dict:
    result = copy.deepcopy(current)
    for key, default in defaults.items():
        if key not in result:
            result[key] = copy.deepcopy(default)
        elif isinstance(default, dict):
            if not isinstance(result[key], dict):
                raise ConfigError(
                    f"{location}.{key} 结构不兼容；refresh 不进行结构迁移"
                )
            result[key] = _merge_missing(result[key], default, f"{location}.{key}")
        # Existing scalars, arrays, empty strings and null are intentional values.
    return result


def _platform_defaults(block: dict, defaults: dict, location: str) -> dict:
    defaults = copy.deepcopy(defaults)
    publish = block.get("publish")
    if isinstance(publish, dict) and publish.get("provider") not in (
        None,
        defaults.get("publish", {}).get("provider"),
    ):
        # Do not attach another publisher's credentials to a custom provider.
        defaults.pop("publish", None)
    return _merge_missing(block, defaults, location)


def _refresh_builds(current: dict, defaults: dict, *, beta: bool) -> dict:
    static_defaults = {
        key: value
        for key, value in defaults.items()
        if key not in ("builds", "artifacts")
    }
    result = _merge_missing(
        current, static_defaults, "beta.json" if beta else "builds.json"
    )
    if "builds" not in result:
        result["builds"] = copy.deepcopy(defaults["builds"])
    builds = result["builds"]
    if not isinstance(builds, dict):
        raise ConfigError("builds 必须是对象；refresh 不进行结构迁移")
    if beta:
        groups = [("builds", builds)]
        platform_defaults = defaults["builds"]
    else:
        groups = list(builds.items())
        platform_defaults = defaults["builds"]["dev"]
        if "default_env" not in current and builds:
            result["default_env"] = next(iter(builds))
    platforms = set()
    for group, blocks in groups:
        if not isinstance(blocks, dict):
            raise ConfigError(f"{group} 必须是对象；refresh 不进行结构迁移")
        for platform, block in blocks.items():
            if not isinstance(block, dict):
                raise ConfigError(
                    f"{group}.{platform} 必须是对象；refresh 不进行结构迁移"
                )
            platforms.add(platform)
            fallback = {"pre_commands": [], "next_commands": []}
            blocks[platform] = _platform_defaults(
                block, platform_defaults.get(platform, fallback), f"{group}.{platform}"
            )
    if not beta:
        artifacts = result.setdefault("artifacts", {})
        if not isinstance(artifacts, dict):
            raise ConfigError("artifacts 必须是对象；refresh 不进行结构迁移")
        for platform in platforms:
            if platform not in artifacts and platform in defaults["artifacts"]:
                artifacts[platform] = copy.deepcopy(defaults["artifacts"][platform])
    return result


def plan_refresh(project_root: Path, *, target: str = "all") -> list[ConfigUpdate]:
    directory = config_dir(project_root)
    if not directory.is_dir():
        raise ConfigError(".tbqrfly 不存在，请先执行 tbqrfly init")
    if target not in ("all", "run", "beta"):
        raise ConfigError(f"未知刷新目标: {target}")
    targets = (
        [target]
        if target != "all"
        else [
            name
            for name, file in (("run", "builds.json"), ("beta", "beta.json"))
            if (directory / file).is_file()
        ]
    )
    if not targets:
        raise ConfigError("未找到 builds.json 或 beta.json，请先初始化配置")
    files = {"feishu.json"}
    if "run" in targets:
        files.update(
            ("builds.json", "zealot.json", "llm.json", "message.md", "prompt.md")
        )
    if "beta" in targets:
        files.add("beta.json")
    updates = []
    for name in sorted(files):
        path = directory / name
        if path.is_symlink():
            raise ConfigError(
                f"{name} 是符号链接，请直接维护目标文件；refresh 未修改任何配置"
            )
        before = path.read_bytes() if path.exists() else None
        if not name.endswith(".json"):
            if before is None:
                updates.append(ConfigUpdate(path, None, _template_text(name).encode()))
            continue
        defaults = json.loads(_template_text(name))
        current = _parse_object(before, name) if before is not None else {}
        if name in ("builds.json", "beta.json"):
            defaults["app_name"] = detect_app_name(project_root)
            result = _refresh_builds(current, defaults, beta=name == "beta.json")
        else:
            # Compatibility keys already supported by the loader stay in place.
            aliases = {
                "zealot.json": {"base_url": "zealot_base_url", "token": "zealot_token"},
                "feishu.json": {
                    "app_id": "feishu_app_id",
                    "app_secret": "feishu_app_secret",
                    "webhook_url": "feishu_webhook_url",
                },
            }.get(name, {})
            for key, alias in aliases.items():
                if alias in current:
                    defaults.pop(key, None)
            result = _merge_missing(current, defaults, name)
        if before is None or result != current:
            after = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode()
            updates.append(ConfigUpdate(path, before, after))
    return updates


def _atomic_write(path: Path, content: bytes) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temporary = Path(file.name)
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def refresh_config(
    project_root: Path, *, target: str = "all", dry_run: bool = False
) -> int:
    updates = plan_refresh(project_root, target=target)
    if not updates:
        print("配置已完整，无需刷新。")
        return 0
    for update in updates:
        action = "补齐" if update.before is not None else "新增"
        print(f"{'预览' if dry_run else '计划'}{action}: {update.path.name}")
    if dry_run:
        print("仅预览，未写入配置或创建备份。")
        return 0
    backup_root = config_dir(project_root) / "backups"
    backup_root.mkdir(mode=0o700, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = Path(tempfile.mkdtemp(prefix=f"refresh-{stamp}-", dir=backup_root))
    for update in updates:
        if update.before is not None:
            _atomic_write(backup / update.path.name, update.before)
    manifest = {
        "created_files": [
            update.path.name for update in updates if update.before is None
        ]
    }
    _atomic_write(
        backup / "manifest.json", (json.dumps(manifest, indent=2) + "\n").encode()
    )
    written = []
    try:
        for update in updates:
            current = update.path.read_bytes() if update.path.exists() else None
            if current != update.before:
                raise ConfigError(f"{update.path.name} 在刷新期间被修改，已停止")
            _atomic_write(update.path, update.after)
            written.append(update)
    except Exception as exc:
        try:
            for update in reversed(written):
                if update.before is None:
                    update.path.unlink(missing_ok=True)
                else:
                    _atomic_write(update.path, update.before)
        except Exception as rollback_error:
            raise RuntimeError(
                f"刷新失败且自动恢复未完成，请使用备份恢复：{backup}"
            ) from rollback_error
        raise RuntimeError(f"刷新失败，已恢复本次写入的配置；备份：{backup}") from exc
    print(f"配置刷新完成；原文件备份：{backup}")
    return 0
