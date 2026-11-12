from __future__ import annotations

from pathlib import Path

import requests
from PIL import Image


def normalize_qrcode(path: Path, edge_px: int) -> None:
    if edge_px < 96 or edge_px > 4096:
        raise ValueError(f"二维码边长必须在 96 到 4096 之间，当前为 {edge_px}")
    with Image.open(path) as source:
        source.load()
        if source.mode == "RGBA":
            image = Image.new("RGB", source.size, (255, 255, 255))
            image.paste(source, mask=source.getchannel("A"))
        else:
            image = source.convert("RGB")
        resample = getattr(Image, "Resampling", Image).NEAREST
        image.resize((edge_px, edge_px), resample).save(path, "PNG", optimize=True)


class FeishuClient:
    AUTH_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    IMAGE_URL = "https://open.feishu.cn/open-apis/im/v1/images"

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        webhook_url: str,
        *,
        timeout: tuple[int, int] = (30, 120),
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.webhook_url = webhook_url
        self.timeout = timeout

    def _token(self) -> str:
        response = requests.post(
            self.AUTH_URL,
            json={"app_id": self.app_id, "app_secret": self.app_secret},
            timeout=self.timeout,
        )
        payload = response.json()
        if payload.get("code") != 0 or not payload.get("tenant_access_token"):
            raise RuntimeError(f"获取飞书访问令牌失败: {payload.get('msg', payload)}")
        return str(payload["tenant_access_token"])

    def download(self, url: str, target: Path) -> None:
        with requests.get(url, stream=True, timeout=self.timeout) as response:
            response.raise_for_status()
            with target.open("wb") as file:
                for chunk in response.iter_content(8192):
                    if chunk:
                        file.write(chunk)
        if not target.is_file() or target.stat().st_size == 0:
            raise RuntimeError("下载的二维码为空")

    def _upload_image(self, token: str, image_path: Path) -> str:
        with image_path.open("rb") as file:
            response = requests.post(
                self.IMAGE_URL,
                headers={"Authorization": f"Bearer {token}"},
                files={"image": (image_path.name, file, "image/png")},
                data={"image_type": "message"},
                timeout=self.timeout,
            )
        payload = response.json()
        image_key = (payload.get("data") or {}).get("image_key")
        if payload.get("code") != 0 or not image_key:
            raise RuntimeError(f"上传飞书图片失败: {payload.get('msg', payload)}")
        return str(image_key)

    def _send(self, title: str, text: str, image_key: str = "") -> None:
        blocks = [[{"tag": "text", "text": text}]]
        if image_key:
            blocks.append([{"tag": "img", "image_key": image_key}])
        content = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": blocks,
                    }
                }
            },
        }
        response = requests.post(self.webhook_url, json=content, timeout=self.timeout)
        payload = response.json()
        if payload.get("StatusCode") != 0 and payload.get("code") != 0:
            raise RuntimeError(f"发送飞书消息失败: {payload}")

    def send_qrcode(
        self,
        qrcode_url: str,
        image_path: Path,
        *,
        title: str,
        text: str,
        edge_px: int = 0,
    ) -> None:
        self.download(qrcode_url, image_path)
        if edge_px:
            normalize_qrcode(image_path, edge_px)
        token = self._token()
        image_key = self._upload_image(token, image_path)
        self._send(title, text, image_key)
