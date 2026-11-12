from __future__ import annotations

import tempfile
from pathlib import Path

from ..clients.feishu import FeishuClient
from ..clients.zealot import ZealotClient
from ..config import RuntimeConfig, render_placeholders
from .project_info import ProjectInfo


def _release_type(environment: str, platform: str) -> str:
    if environment == "local":
        return "debug"
    if environment == "dev":
        return "beta"
    return "adhoc" if platform == "ios" else "release"


def _display_platform(platform: str) -> str:
    mapping = {"android": "Android", "ios": "iOS"}
    return mapping.get(platform.lower(), platform)


def upload_and_notify(
    config: RuntimeConfig,
    *,
    env: str,
    platform: str,
    artifact: Path,
    info: ProjectInfo,
    summary: str,
) -> None:
    block = config.platform_config(env, platform)
    channel_key = str(block.get("zealot_channel_key") or "").strip()
    if not channel_key:
        raise RuntimeError(f"缺少 zealot_channel_key: builds.{env}.{platform}")

    changelog = (
        f"版本: {info.version}\n"
        f"构建号: {info.build_number}\n"
        f"分支: {info.branch}\n"
        f"提交: {info.commit_short}\n"
        f"构建: {info.build_date}\n\n"
        f"{summary}"
    )
    client = ZealotClient(config.zealot["base_url"], config.zealot["token"])
    install_url, qrcode_url = client.upload(
        artifact,
        channel_key,
        changelog=changelog,
        branch=info.branch,
        git_commit=info.commit_short,
        release_type=_release_type(env, platform),
    )
    print(f"  安装地址: {install_url}")
    print(f"  二维码: {qrcode_url}")

    if not config.feishu_ready:
        print("飞书未配置完整，跳过通知")
        return

    display_platform = _display_platform(platform)
    values = {
        "platform": display_platform,
        "version": info.version,
        "buildNumber": info.build_number,
        "commit": info.commit_short,
        "env": env,
        "app_name": info.app_name,
        "summary": summary,
    }
    text = render_placeholders(config.message_template or "{summary}", values)
    title = f"{info.app_name} {display_platform} 发布"
    feishu = FeishuClient(
        config.feishu["app_id"],
        config.feishu["app_secret"],
        config.feishu["webhook_url"],
    )
    with tempfile.TemporaryDirectory(prefix="tbqrfly-") as tmp:
        image_path = Path(tmp) / f"{platform}_qrcode.png"
        feishu.send_qrcode(qrcode_url, image_path, title=title, text=text)
    print(f"  已发送飞书通知: {platform}")
