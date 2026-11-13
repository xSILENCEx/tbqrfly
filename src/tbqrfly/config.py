from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONFIG_DIR_NAME = ".tbqrfly"
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class ConfigError(ValueError):
    pass


def command_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ConfigError(f"{location} 必须是字符串数组")
    return [item.strip() for item in value if item.strip()]


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV_PATTERN.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"无法读取 {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ConfigError(f"{path} 根节点必须是对象")
    return _expand(payload)


def _resolve_text(config_dir: Path, value: str, default_name: str) -> str:
    raw = (value or default_name).strip()
    if not raw:
        return ""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = config_dir / candidate
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8")
    # Inline template (or missing file treated as literal text)
    if raw.endswith((".md", ".txt")) and not (config_dir / raw).is_file():
        raise ConfigError(f"模板文件不存在: {candidate}")
    return value


def find_project_root(start: Path | None = None, explicit: Path | None = None) -> Path | None:
    if explicit:
        root = explicit.expanduser().resolve()
        return root if (root / "pubspec.yaml").is_file() else None
    current = (start or Path.cwd()).expanduser().resolve()
    for candidate in (current, *current.parents):
        if (candidate / CONFIG_DIR_NAME).is_dir() or (candidate / "pubspec.yaml").is_file():
            if (candidate / "pubspec.yaml").is_file():
                return candidate
    return None


def config_dir(project_root: Path) -> Path:
    return project_root / CONFIG_DIR_NAME


@dataclass
class RuntimeConfig:
    project_root: Path
    app_name: str
    default_env: str
    wait_timeout: int
    artifacts: dict[str, list[str]]
    builds: dict[str, dict[str, dict[str, Any]]]
    zealot: dict[str, str]
    feishu: dict[str, str]
    llm: dict[str, Any]
    # auto: IDE 内用 inline，否则 system Terminal
    # system: 始终新开 macOS Terminal
    # inline: 在当前终端并行跑其它平台（适合 Cursor/VS Code）
    terminal: str = "auto"
    pre_commands: list[str] = field(default_factory=list)
    next_commands: list[str] = field(default_factory=list)
    prompt_text: str = ""
    message_template: str = ""
    source_dir: Path = field(default_factory=Path)

    def env_names(self) -> list[str]:
        return list(self.builds.keys())

    def platforms_for(self, env: str) -> list[str]:
        block = self.builds.get(env) or {}
        return [name for name, value in block.items() if isinstance(value, dict)]

    def platform_config(self, env: str, platform: str) -> dict[str, Any]:
        try:
            return dict(self.builds[env][platform])
        except KeyError as exc:
            raise ConfigError(f"缺少构建配置 builds.{env}.{platform}") from exc

    def artifact_patterns(self, platform: str) -> list[str]:
        return list(self.artifacts.get(platform, []))

    @property
    def history_path(self) -> Path:
        return self.source_dir / "commit_history.json"

    @property
    def feishu_ready(self) -> bool:
        return bool(
            self.feishu.get("webhook_url")
            and self.feishu.get("app_id")
            and self.feishu.get("app_secret")
        )


def load_runtime_config(project_root: Path) -> RuntimeConfig:
    directory = config_dir(project_root)
    builds_path = directory / "builds.json"
    zealot_path = directory / "zealot.json"
    if not builds_path.is_file():
        raise ConfigError(f"缺少 {builds_path}，请先执行 tbqrfly init")
    if not zealot_path.is_file():
        raise ConfigError(f"缺少 {zealot_path}，请先执行 tbqrfly init")

    builds_raw = _load_json(builds_path)
    zealot = _load_json(zealot_path)
    # Compat with prefixed keys
    if "base_url" not in zealot and "zealot_base_url" in zealot:
        zealot["base_url"] = str(zealot.get("zealot_base_url") or "")
    if "token" not in zealot and "zealot_token" in zealot:
        zealot["token"] = str(zealot.get("zealot_token") or "")

    feishu: dict[str, str] = {}
    feishu_path = directory / "feishu.json"
    if feishu_path.is_file():
        raw = _load_json(feishu_path)
        feishu = {
            "webhook_url": str(
                raw.get("webhook_url") or raw.get("feishu_webhook_url") or ""
            ),
            "app_id": str(raw.get("app_id") or raw.get("feishu_app_id") or ""),
            "app_secret": str(
                raw.get("app_secret") or raw.get("feishu_app_secret") or ""
            ),
            "message_template": str(raw.get("message_template") or "message.md"),
            "qrcode_edge_px": str(raw.get("qrcode_edge_px") or "768"),
        }

    llm_path = directory / "llm.json"
    if llm_path.is_file():
        llm = _load_json(llm_path)
    else:
        llm = {"enabled": True, "command": "claude -p", "prompt": "prompt.md"}

    builds_map = builds_raw.get("builds")
    if not isinstance(builds_map, dict) or not builds_map:
        raise ConfigError("builds.json 中 builds 不能为空")

    artifacts_raw = builds_raw.get("artifacts") or {}
    if not isinstance(artifacts_raw, dict):
        raise ConfigError("builds.json artifacts 必须是对象")
    artifacts = {
        str(key): [str(item) for item in (value or [])]
        for key, value in artifacts_raw.items()
        if isinstance(value, list)
    }

    prompt_text = ""
    if llm.get("enabled", True):
        prompt_text = _resolve_text(
            directory, str(llm.get("prompt") or "prompt.md"), "prompt.md"
        )

    message_template = ""
    if feishu:
        message_template = _resolve_text(
            directory,
            str(feishu.get("message_template") or "message.md"),
            "message.md",
        )

    terminal = str(builds_raw.get("terminal") or "auto").strip().lower()
    if terminal not in {"auto", "system", "inline"}:
        raise ConfigError("builds.json terminal 只能是 auto / system / inline")

    pre_commands = command_list(builds_raw.get("pre_commands", []), "pre_commands")
    next_commands = command_list(builds_raw.get("next_commands", []), "next_commands")
    for env, platforms in builds_map.items():
        if not isinstance(platforms, dict):
            continue
        for platform, block in platforms.items():
            if not isinstance(block, dict):
                continue
            for key in ("pre_commands", "next_commands"):
                block[key] = command_list(
                    block.get(key, []), f"builds.{env}.{platform}.{key}"
                )

    return RuntimeConfig(
        project_root=project_root,
        app_name=str(builds_raw.get("app_name") or project_root.name),
        default_env=str(builds_raw.get("default_env") or "dev"),
        wait_timeout=int(builds_raw.get("wait_timeout") or 10),
        artifacts=artifacts,
        builds={str(k): v for k, v in builds_map.items() if isinstance(v, dict)},
        zealot={
            "base_url": str(zealot.get("base_url") or "").strip(),
            "token": str(zealot.get("token") or "").strip(),
        },
        feishu=feishu,
        llm=llm,
        terminal=terminal,
        pre_commands=pre_commands,
        next_commands=next_commands,
        prompt_text=prompt_text,
        message_template=message_template,
        source_dir=directory,
    )


def validate_for_run(config: RuntimeConfig, env: str, platforms: list[str]) -> None:
    if not config.zealot.get("base_url") or not config.zealot.get("token"):
        raise ConfigError("zealot.json 需要填写 base_url 和 token")
    if env not in config.builds:
        raise ConfigError(f"环境 {env} 不存在于 builds.json")
    for platform in platforms:
        block = config.platform_config(env, platform)
        if not str(block.get("zealot_channel_key") or "").strip():
            raise ConfigError(f"builds.{env}.{platform}.zealot_channel_key 未填写")
        if not str(block.get("build_command") or "").strip():
            raise ConfigError(f"builds.{env}.{platform}.build_command 未填写")
        if not config.artifact_patterns(platform):
            raise ConfigError(f"builds.json artifacts.{platform} 未配置")


def render_placeholders(template: str, values: dict[str, str]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result
