#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
@File: runtime/media_decrypt.py
@Desc: 抖音 IM 加密媒体（用户本地图片/视频封面）解密 + 浏览器可显示格式转换。

抖音私信里用户从相册发的图片/视频是**加密 CDN 资源**：
  - resource_url.origin_url_list[0] / poster.origin_url_list[0] 直链拿到的是密文
    （Content-Type: application/octet-stream），<img> 无法直接显示。
  - 加密算法（真机验证 2026-06-24）：AES-256-GCM
      key   = bytes.fromhex(skey)        # skey 为 64 位十六进制 = 32 字节
      nonce = ciphertext[:12]            # 前 12 字节
      body  = ciphertext[12:]            # 其余 = 密文 + 16 字节 GCM tag（cryptography 自动校验）
      明文长度 == resource_url.data_size
  - 解出来常是 HEIF/HEIC（iPhone 照片），Chrome/WebView2 不支持，需转 JPEG。
"""
from __future__ import annotations

import io
import logging
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

logger = logging.getLogger(__name__)

_GCM_NONCE_LEN = 12

# 浏览器可直接显示的格式：magic 前缀 → content-type
_BROWSER_SAFE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


class MediaDecryptError(RuntimeError):
    """解密 / 转码失败。"""


def decrypt_im_media(ciphertext: bytes, skey_hex: str) -> bytes:
    """AES-256-GCM 解密抖音 IM 加密媒体。失败抛 MediaDecryptError。"""
    if not ciphertext:
        raise MediaDecryptError("密文为空")
    skey_hex = (skey_hex or "").strip()
    if not skey_hex:
        raise MediaDecryptError("skey 为空")
    try:
        key = bytes.fromhex(skey_hex)
    except ValueError as e:
        raise MediaDecryptError(f"skey 非法十六进制: {e}") from e
    if len(key) not in (16, 24, 32):
        raise MediaDecryptError(f"skey 长度异常: {len(key)} 字节")
    if len(ciphertext) <= _GCM_NONCE_LEN + 16:
        raise MediaDecryptError(f"密文过短: {len(ciphertext)} 字节")
    nonce = ciphertext[:_GCM_NONCE_LEN]
    body = ciphertext[_GCM_NONCE_LEN:]
    try:
        return AESGCM(key).decrypt(nonce, body, None)
    except Exception as e:  # noqa: BLE001  (InvalidTag 等)
        raise MediaDecryptError(f"AES-GCM 解密失败: {type(e).__name__}: {e}") from e


def _sniff_browser_safe(data: bytes) -> Optional[str]:
    """识别浏览器原生可显示的格式，返回 content-type；不可直接显示返回 None。"""
    for magic, ctype in _BROWSER_SAFE_MAGIC:
        if data.startswith(magic):
            return ctype
    # WebP: RIFF....WEBP
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def to_browser_image(data: bytes) -> tuple[bytes, str]:
    """把解密后的图片转成浏览器可显示格式。

    JPEG/PNG/GIF/WebP 直接透传；HEIF/HEIC 等其它格式用 Pillow(+pillow-heif) 转 JPEG。
    返回 (bytes, content_type)。失败抛 MediaDecryptError。
    """
    ctype = _sniff_browser_safe(data)
    if ctype:
        return data, ctype

    # 非浏览器原生格式（多为 HEIF/HEIC）→ 转 JPEG
    try:
        try:
            import pillow_heif  # type: ignore

            pillow_heif.register_heif_opener()
        except ImportError:
            logger.warning("[media_decrypt] 未安装 pillow-heif，HEIF 图片可能无法转码")
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return buf.getvalue(), "image/jpeg"
    except Exception as e:  # noqa: BLE001
        raise MediaDecryptError(f"图片转码失败: {type(e).__name__}: {e}") from e


def pick_encrypted_image(content_json_obj: dict) -> Optional[tuple[str, str]]:
    """从 content_json 里挑出"可解密的图片源"，返回 (url, skey)。

    - 图片消息：resource_url.{origin/large/medium/thumb}_url_list[0] + resource_url.skey
    - 视频消息：poster.{origin/medium}_url_list[0] + poster.skey（视频封面）
    无加密图片源返回 None。
    """
    if not isinstance(content_json_obj, dict):
        return None

    def _first_url(d: dict, *keys: str) -> str:
        for k in keys:
            v = d.get(k)
            if isinstance(v, list) and v and isinstance(v[0], str) and v[0]:
                return v[0]
        return ""

    res = content_json_obj.get("resource_url")
    if isinstance(res, dict) and res.get("skey"):
        url = _first_url(
            res, "origin_url_list", "large_url_list", "medium_url_list", "thumb_url_list"
        )
        if url:
            return url, str(res["skey"])

    poster = content_json_obj.get("poster")
    if isinstance(poster, dict) and poster.get("skey"):
        url = _first_url(poster, "origin_url_list", "medium_url_list")
        if url:
            return url, str(poster["skey"])

    return None
