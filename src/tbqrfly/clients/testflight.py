from __future__ import annotations

import os
import subprocess
from importlib import resources
from pathlib import Path

from ..beta_config import BetaConfig, resolve_path


def fastlane_directory(config: BetaConfig) -> Path:
    return config.source_dir / ".runtime" / "fastlane-2.241.2"


def fastlane_environment(config: BetaConfig) -> dict[str, str]:
    return {
        **os.environ,
        "BUNDLE_GEMFILE": str(fastlane_directory(config) / "Gemfile"),
        "FASTLANE_SKIP_UPDATE_CHECK": "1",
        "FASTLANE_OPT_OUT_USAGE": "1",
        "FASTLANE_SKIP_DOCS": "1",
        "CI": "true",
    }


def prepare_fastlane(config: BetaConfig) -> None:
    directory = fastlane_directory(config)
    (directory / "fastlane").mkdir(parents=True, exist_ok=True)
    templates = resources.files("tbqrfly").joinpath("templates")
    for source, destination in (
        ("BetaGemfile", directory / "Gemfile"),
        ("BetaFastfile", directory / "fastlane" / "Fastfile"),
    ):
        content = templates.joinpath(source).read_text(encoding="utf-8")
        if (
            not destination.is_file()
            or destination.read_text(encoding="utf-8") != content
        ):
            destination.write_text(content, encoding="utf-8")
    lock = directory / "Gemfile.lock"
    if not lock.exists():
        lock.write_text(
            templates.joinpath("BetaGemfile.lock").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
    environment = fastlane_environment(config)
    check = subprocess.run(
        ["bundle", "check"],
        cwd=directory,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if check.returncode:
        print("准备 Beta 上传依赖（Fastlane 2.241.2）…")
        result = subprocess.run(
            ["bundle", "install"], cwd=directory, env=environment, check=False
        )
        if result.returncode:
            raise RuntimeError(
                "Fastlane 依赖安装失败，请检查 Ruby、Bundler 与 gem 目录权限"
            )


def upload_testflight(config: BetaConfig, artifact: Path, platform: str) -> None:
    publish = config.platform_config("prod", platform)["publish"]
    environment = {
        **fastlane_environment(config),
        "TBQRFLY_ASC_KEY_ID": publish["key_id"],
        "TBQRFLY_ASC_ISSUER_ID": publish["issuer_id"],
        "TBQRFLY_ASC_KEY_FILE": str(
            resolve_path(config.project_root, publish["key_file"]).resolve()
        ),
        "TBQRFLY_BETA_IPA": str(artifact.resolve()),
        "TBQRFLY_BETA_BUNDLE_ID": publish["bundle_id"],
    }
    # Credentials are passed in the environment, never interpolated into a command.
    result = subprocess.run(
        ["bundle", "exec", "fastlane", "ios", "tbqrfly_upload"],
        cwd=fastlane_directory(config),
        env=environment,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(
            f"TestFlight 上传失败，退出码 {result.returncode}，请查看上传日志"
        )
