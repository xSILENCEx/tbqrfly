from __future__ import annotations

import plistlib
import shlex
from dataclasses import dataclass
from pathlib import Path

from .config import ConfigError, RuntimeConfig, _load_json, command_list, config_dir


@dataclass
class BetaConfig(RuntimeConfig):
    notify: bool = True

    @property
    def history_path(self) -> Path:
        return self.source_dir / "beta_commit_history.json"


def resolve_path(root: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else root / path


def load_beta_config(project_root: Path) -> BetaConfig:
    directory = config_dir(project_root)
    path = directory / "beta.json"
    if not path.is_file():
        raise ConfigError("缺少 .tbqrfly/beta.json，请执行 tbqrfly init --target beta")
    raw = _load_json(path)
    if "default_env" in raw or "env" in raw:
        raise ConfigError("beta.json 不接受环境配置，固定发布 prod")
    builds = raw.get("builds")
    if not isinstance(builds, dict) or not builds:
        raise ConfigError("beta.json builds 必须是非空平台对象")
    artifacts = {}
    for platform, block in builds.items():
        location = f"beta.json builds.{platform}"
        if not isinstance(block, dict):
            raise ConfigError(f"{location} 必须是对象")
        for key in ("pre_commands", "next_commands"):
            block[key] = command_list(block.get(key, []), f"{location}.{key}")
        artifacts[platform] = command_list(
            block.get("artifacts", []), f"{location}.artifacts"
        )
    terminal = raw.get("terminal", "auto")
    if terminal not in ("auto", "inline", "system"):
        raise ConfigError("beta.json terminal 只能是 auto / inline / system")
    timeout = raw.get("wait_timeout", 10)
    if type(timeout) is not int or timeout < 0:
        raise ConfigError("beta.json wait_timeout 必须是非负整数")
    notify = raw.get("notify", True)
    if not isinstance(notify, bool):
        raise ConfigError("beta.json notify 必须是布尔值")
    feishu = {}
    if notify and (directory / "feishu.json").is_file():
        raw_feishu = _load_json(directory / "feishu.json")
        for key in ("app_id", "app_secret", "webhook_url"):
            value = raw_feishu.get(key) or raw_feishu.get(f"feishu_{key}") or ""
            feishu[key] = str(value)
    return BetaConfig(
        project_root=project_root,
        app_name=str(raw.get("app_name") or project_root.name),
        default_env="prod",
        wait_timeout=timeout,
        terminal=terminal,
        pre_commands=command_list(
            raw.get("pre_commands", []), "beta.json pre_commands"
        ),
        next_commands=command_list(
            raw.get("next_commands", []), "beta.json next_commands"
        ),
        artifacts=artifacts,
        builds={"prod": builds},
        zealot={},
        feishu=feishu,
        llm={"enabled": False},
        source_dir=directory,
        notify=notify,
    )


def validate_beta(config: BetaConfig, env: str, platforms: list[str]) -> None:
    if env != "prod":
        raise ConfigError("Beta 发布仅支持 prod")
    for platform in platforms:
        block = config.platform_config("prod", platform)
        publish = block.get("publish")
        if not isinstance(publish, dict):
            raise ConfigError(f"builds.{platform}.publish 必须是对象")
        provider = publish.get("provider")
        if platform != "ios" or provider != "testflight":
            raise ConfigError(f"尚不支持 Beta 发布平台/渠道: {platform}/{provider}")
        command = block.get("build_command")
        if not isinstance(command, str) or not command.strip():
            raise ConfigError(f"builds.{platform}.build_command 未填写")
        try:
            args = shlex.split(command)
        except ValueError as exc:
            raise ConfigError("build_command 引号不匹配") from exc
        if not args:
            raise ConfigError("build_command 不能为空")
        for index, arg in enumerate(args):
            if arg in ("--debug", "--profile"):
                raise ConfigError("Beta 构建必须为 release")
            flavor = (
                arg.partition("=")[2]
                if arg.startswith("--flavor=")
                else (
                    args[index + 1]
                    if arg == "--flavor" and index + 1 < len(args)
                    else ""
                )
            )
            define = arg.removeprefix("--dart-define=")
            if arg == "--dart-define" and index + 1 < len(args):
                define = args[index + 1]
            if (flavor and flavor != "prod") or (
                define.startswith("FLAVOR=") and define != "FLAVOR=prod"
            ):
                raise ConfigError("Beta 构建不能指定非 prod 环境")
        if not config.artifact_patterns(platform):
            raise ConfigError(f"builds.{platform}.artifacts 未配置")
        for key in ("bundle_id", "key_id", "issuer_id", "key_file"):
            if not isinstance(publish.get(key), str) or not publish[key].strip():
                raise ConfigError(f"builds.{platform}.publish.{key} 未填写")
        key_path = resolve_path(config.project_root, publish["key_file"])
        try:
            if b"-----BEGIN PRIVATE KEY-----" not in key_path.read_bytes():
                raise ConfigError("请配置有效的 Apple .p8 私钥文件")
        except OSError as exc:
            raise ConfigError("无法读取 Apple .p8 私钥文件") from exc
        export = block.get("export_options")
        if not isinstance(export, str) or not export:
            raise ConfigError("builds.ios.export_options 未填写")
        try:
            with resolve_path(config.project_root, export).open("rb") as file:
                options = plistlib.load(file)
        except (OSError, ValueError, plistlib.InvalidFileException) as exc:
            raise ConfigError("无法读取 iOS export_options plist") from exc
        if not isinstance(options, dict) or options.get("method") not in (
            "app-store",
            "app-store-connect",
        ):
            raise ConfigError("Beta iOS 导出必须使用 app-store-connect 或 app-store")
        if options.get("destination", "export") != "export":
            raise ConfigError(
                "export_options.destination 必须是 export，由工具执行上传"
            )
        if options.get("manageAppVersionAndBuildNumber", False):
            raise ConfigError("请关闭 manageAppVersionAndBuildNumber，保留项目版本号")
