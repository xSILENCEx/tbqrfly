from __future__ import annotations

from pathlib import Path

import requests
from PIL import Image, ImageOps


DEFAULT_QRCODE_EDGE_PX = 768


def normalize_qrcode(path: Path, edge_px: int = DEFAULT_QRCODE_EDGE_PX) -> None:
    """Upscale, binarize to pure black/white, and add quiet-zone padding."""
    if edge_px <= 0:
        edge_px = DEFAULT_QRCODE_EDGE_PX
    if edge_px < 96 or edge_px > 4096:
        raise ValueError(f"二维码边长必须在 96 到 4096 之间，当前为 {edge_px}")

    with Image.open(path) as source:
        source.load()
        if source.mode == "RGBA":
            image = Image.new("RGB", source.size, (255, 255, 255))
            image.paste(source, mask=source.getchannel("A"))
        else:
            image = source.convert("RGB")

        # Zealot thumbnails are often small gray-on-white; force high contrast.
        gray = ImageOps.grayscale(image)
        binary = gray.point(lambda value: 0 if value < 180 else 255, mode="1").convert(
            "RGB"
        )

        # Keep modules crisp when enlarging.
        resample = getattr(Image, "Resampling", Image).NEAREST
        # Leave ~8% quiet zone around the code for scanners / Feishu preview.
        content_edge = max(64, int(edge_px * 0.84))
        scaled = binary.resize((content_edge, content_edge), resample)
        canvas = Image.new("RGB", (edge_px, edge_px), (255, 255, 255))
        offset = (edge_px - content_edge) // 2
        canvas.paste(scaled, (offset, offset))
        canvas.save(path, "PNG", optimize=True)


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
        edge_px: int = DEFAULT_QRCODE_EDGE_PX,
    ) -> None:
        self.download(qrcode_url, image_path)
        normalize_qrcode(image_path, edge_px)
        token = self._token()
        image_key = self._upload_image(token, image_path)
        self._send(title, text, image_key)

    def send_text(self, *, title: str, text: str) -> None:
        self._send(title, text)
