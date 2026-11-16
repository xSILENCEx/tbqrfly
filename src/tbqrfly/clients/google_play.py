from __future__ import annotations

import subprocess
from pathlib import Path

from ..beta_config import BetaConfig, resolve_path, validate_google_play
from .testflight import fastlane_directory, fastlane_environment


def upload_google_play(config: BetaConfig, artifact: Path, platform: str) -> None:
    publish = config.platform_config("prod", platform)["publish"]
    validate_google_play(config, publish)
    environment = {
        **fastlane_environment(config),
        "TBQRFLY_PLAY_PACKAGE_NAME": publish["package_name"],
        "TBQRFLY_PLAY_SERVICE_ACCOUNT_FILE": str(resolve_path(config.project_root, publish["service_account_file"]).resolve()),
        "TBQRFLY_BETA_AAB": str(artifact.resolve()),
        "TBQRFLY_PLAY_RELEASE_STATUS": publish.get("release_status", "completed"),
    }
    result = subprocess.run(
        ["bundle", "exec", "fastlane", "android", "tbqrfly_upload"],
        cwd=fastlane_directory(config), env=environment, check=False,
    )
    if result.returncode:
        raise RuntimeError(f"Google Play 上传失败，退出码 {result.returncode}，请查看上传日志")
