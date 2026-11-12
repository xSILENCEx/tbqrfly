from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin

import requests


class ZealotClient:
    UPLOAD_TIMEOUT_BASE = 300
    UPLOAD_TIMEOUT_PER_100MB = 180
    UPLOAD_TIMEOUT_MAX = 1800
    RESPONSE_TIMEOUT = 600

    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _timeout(self, file_size: int) -> tuple[int, int]:
        size_mb = file_size / (1024 * 1024)
        upload = self.UPLOAD_TIMEOUT_BASE + int(size_mb / 100) * self.UPLOAD_TIMEOUT_PER_100MB
        return min(upload, self.UPLOAD_TIMEOUT_MAX), self.RESPONSE_TIMEOUT

    def _absolute_url(self, value: str) -> str:
        if not value:
            return ""
        if value.startswith(("http://", "https://")):
            return value
        return urljoin(f"{self.base_url}/", value.lstrip("/"))

    def upload(
        self,
        artifact: Path,
        channel_key: str,
        *,
        changelog: str,
        branch: str,
        git_commit: str,
        release_type: str,
    ) -> tuple[str, str]:
        data = {
            "token": self.token,
            "channel_key": channel_key,
            "source": "api",
            "changelog": changelog,
            "branch": branch,
            "git_commit": git_commit,
            "release_type": release_type,
        }
        with artifact.open("rb") as file:
            response = requests.post(
                f"{self.base_url}/api/apps/upload",
                data=data,
                files={"file": (artifact.name, file, "application/octet-stream")},
                timeout=self._timeout(artifact.stat().st_size),
            )
        if not response.ok:
            raise RuntimeError(f"Zealot HTTP {response.status_code}: {response.text[:500]}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Zealot 返回了非 JSON 内容: {response.text[:500]}") from exc

        install_url = self._absolute_url(str(payload.get("install_url") or "").strip())
        qrcode_url = self._absolute_url(str(payload.get("qrcode_url") or "").strip())
        if not install_url:
            raise RuntimeError("Zealot 响应缺少 install_url")
        if not qrcode_url:
            release_url = self._absolute_url(str(payload.get("release_url") or "").strip())
            if release_url:
                qrcode_url = f"{release_url}/qrcode?size=thumb"
        return install_url, qrcode_url
