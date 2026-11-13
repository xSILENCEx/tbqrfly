from __future__ import annotations

import plistlib
import re
import zipfile
from pathlib import Path

from ..beta_config import BetaConfig
from ..clients.feishu import FeishuClient
from ..clients.testflight import upload_testflight
from .artifacts import matching_files
from .build import run_build
from .pre_commands import run_commands
from .project_info import ProjectInfo
from .release_lock import ios_release_lock


def artifact_snapshot(config: BetaConfig, platform: str) -> dict[Path, tuple[int, int]]:
    return {
        path.resolve(): (path.stat().st_mtime_ns, path.stat().st_size)
        for path in matching_files(config, platform)
    }


def find_beta_artifact(
    config: BetaConfig, platform: str, before: dict[Path, tuple[int, int]]
) -> Path:
    after = artifact_snapshot(config, platform)
    changed = [
        path for path, signature in after.items() if before.get(path) != signature
    ]
    if len(changed) != 1:
        raise RuntimeError("无法唯一识别本次 Beta 构建产物，停止上传")
    return changed[0]


def validate_ipa(artifact: Path, bundle_id: str, info: ProjectInfo) -> None:
    try:
        with zipfile.ZipFile(artifact) as archive:
            names = [
                name
                for name in archive.namelist()
                if re.fullmatch(r"Payload/[^/]+\.app/Info\.plist", name)
            ]
            if len(names) != 1:
                raise RuntimeError("IPA 主 App 不唯一")
            plist = plistlib.loads(archive.read(names[0]))
    except (
        OSError,
        ValueError,
        zipfile.BadZipFile,
        KeyError,
        plistlib.InvalidFileException,
    ) as exc:
        raise RuntimeError("无法读取 IPA 的 Info.plist") from exc
    if not isinstance(plist, dict):
        raise RuntimeError("IPA Info.plist 格式不正确")
    for key, expected in (
        ("CFBundleIdentifier", bundle_id),
        ("CFBundleShortVersionString", info.version),
        ("CFBundleVersion", info.build_number),
    ):
        if str(plist.get(key, "")) != expected:
            raise RuntimeError(f"IPA {key} 与本次发布配置不匹配")


def notify_beta(
    config: BetaConfig, *, platform: str, info: ProjectInfo | None, message: str
) -> None:
    if not config.notify or not config.feishu_ready:
        print("Beta 飞书通知已禁用或未配置完整，跳过")
        return
    version = f"{info.version}+{info.build_number}" if info else "未读取"
    commit = info.commit_short if info else "未读取"
    try:
        FeishuClient(
            config.feishu["app_id"],
            config.feishu["app_secret"],
            config.feishu["webhook_url"],
        ).send_text(
            title=f"{config.app_name} {platform} Beta 发布",
            text=f"{message}\n版本: {version}\n提交: {commit}\n环境: prod\n平台: {platform}",
        )
    except Exception as exc:
        print(f"警告: Beta 飞书通知失败（{type(exc).__name__}）；发布结果不受影响。")


def run_beta_platform(
    config: BetaConfig,
    *,
    env: str,
    platform: str,
    info: ProjectInfo,
    summary: str,
) -> None:
    del summary
    if env != "prod":
        raise RuntimeError("Beta 发布仅支持 prod")
    block = config.platform_config("prod", platform)
    uploaded = False
    try:
        with ios_release_lock(config.project_root, platform):
            run_commands(
                config.project_root,
                block["pre_commands"],
                label=f"{platform} Beta 前置命令",
            )
            before = artifact_snapshot(config, platform)
            run_build(config, "prod", platform, info)
            artifact = find_beta_artifact(config, platform, before)
            validate_ipa(artifact, block["publish"]["bundle_id"], info)
            upload_testflight(config, artifact, platform)
            uploaded = True
            print(f"{platform}: 已上传 TestFlight，等待 Apple 处理。")
            run_commands(
                config.project_root,
                block["next_commands"],
                label=f"{platform} Beta 后置命令",
            )
    except Exception:
        message = (
            "已上传 TestFlight，但后置命令失败；无需因此重复上传。"
            if uploaded
            else "Beta 构建或上传失败，已停止后续步骤，请查看打包机日志。"
        )
        notify_beta(config, platform=platform, info=info, message=message)
        raise
    notify_beta(
        config,
        platform=platform,
        info=info,
        message="已上传 TestFlight，等待 Apple 处理。",
    )
