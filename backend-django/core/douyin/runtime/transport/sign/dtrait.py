"""Build the path-bound ``x-tt-session-dtrait`` header used by passport calls.

The opaque device blob is captured from the real creator page.  This module only
rebuilds the documented outer envelope for the current request path and time; it
does not invent browser traits.
"""
from __future__ import annotations

import base64
import json
import os
import time
from typing import Callable, Optional

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DEFAULT_TRAIT_SDK_VERSION = "1.0.0.16"

# RSA public key embedded in the current passport bundle (version d0).
_BUILTIN_TRAIT_PK1_B64 = (
    "LS0tLS1CRUdJTiBSU0EgUFVCTElDIEtFWS0tLS0tCk1JSUJDZ0tDQVFFQTQrZHZ2WTd1"
    "TStvcGMrbkxHL0R1bVNlRm83YVZjSW0xTE8rbVVJcldwclJ6UDBhMUdwRVEKNHF0TzlN"
    "UmYvbHdFSXgzOCs0Qlo0WE9HemV2VnR1VXZmSU9VRTdBVHRRVzdGS0pmNVBuU0xDSTYv"
    "azB2bDFGQwpMVVNWbUVQNnFQSnJJalo0elhvcWkzeXVOWisxb2RiUkEvL0dIZ2NnU3l5"
    "eWFMcXp3amtwV0dYb3VNWW12WXNTCnBway9mdjJFV0FCc3RQTnhXYTRFT0JDYWRUVVBr"
    "WE5RNzZOQkVQOXh6ZkpTMjB3aUR2MW9TL3ZLdnJTVXBXY0oKbmF6a2tCdnFRYmJBcVZi"
    "UUZURi9EUGlrcHB1NlpUNmxHSVh2SktDcmVlRmlIQTJxSzZ0UzE4U1dWSFc5QVJ6MQor"
    "cGpCMWVxSUlZdG9oV3BUMkI0ME9DNE84dFZlQkFuYmlRSURBUUFCCi0tLS0tRU5EIFJT"
    "QSBQVUJMSUMgS0VZLS0tLS0="
)


def builtin_trait_pubkey() -> tuple[str, str]:
    """Return ``(PEM, version)`` from the creator passport bundle."""

    return base64.b64decode(_BUILTIN_TRAIT_PK1_B64).decode("ascii"), "d0"


def _der_len(buf: bytes, offset: int) -> tuple[int, int]:
    value = buf[offset]
    offset += 1
    if value < 0x80:
        return value, offset
    width = value & 0x7F
    return int.from_bytes(buf[offset : offset + width], "big"), offset + width


def _parse_rsa_public_key(pem: str) -> tuple[int, int]:
    body = "".join(line for line in pem.strip().splitlines() if "-----" not in line)
    der = base64.b64decode(body)
    if not der or der[0] != 0x30:
        raise ValueError("dtrait 公钥不是合法 DER SEQUENCE")
    _, offset = _der_len(der, 1)
    if der[offset] != 0x02:
        raise ValueError("dtrait 公钥缺少 modulus")
    size, offset = _der_len(der, offset + 1)
    modulus = int.from_bytes(der[offset : offset + size], "big")
    offset += size
    if der[offset] != 0x02:
        raise ValueError("dtrait 公钥缺少 exponent")
    size, offset = _der_len(der, offset + 1)
    exponent = int.from_bytes(der[offset : offset + size], "big")
    return modulus, exponent


def _rsa_encrypt_pkcs1v15(
    modulus: int,
    exponent: int,
    message: bytes,
    *,
    randbytes: Callable[[int], bytes],
) -> bytes:
    width = (modulus.bit_length() + 7) // 8
    if len(message) > width - 11:
        raise ValueError("dtrait RSA 明文超长")
    padding_size = width - len(message) - 3
    padding = bytearray()
    while len(padding) < padding_size:
        padding.extend(value for value in randbytes(padding_size - len(padding)) if value)
    encoded = b"\x00\x02" + bytes(padding[:padding_size]) + b"\x00" + message
    encrypted = pow(int.from_bytes(encoded, "big"), exponent, modulus)
    return encrypted.to_bytes(width, "big")


def _aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    padding_size = 16 - len(plaintext) % 16
    padded = plaintext + bytes([padding_size]) * padding_size
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


def build_session_dtrait(
    path: str,
    dtrait_blob: str,
    *,
    timestamp: Optional[int] = None,
    randbytes: Optional[Callable[[int], bytes]] = None,
    session_material: Optional[tuple[str, bytes]] = None,
    return_material: bool = False,
) -> str | tuple[str, tuple[str, bytes]]:
    """Build a fresh header for ``path`` from a browser-captured device blob.

    ``session_material`` lets one provider instance reuse the encrypted AES key
    while refreshing the IV, timestamp and request path, matching the page SDK.
    """

    blob = str(dtrait_blob or "").strip()
    if not blob:
        raise ValueError("dtrait_blob 为空")
    path = str(path or "").strip()
    if not path.startswith("/"):
        raise ValueError("dtrait path 必须以 / 开头")
    random_bytes = randbytes or os.urandom
    pem, version = builtin_trait_pubkey()

    if session_material is None:
        key_hex = random_bytes(16).hex()
        # Keep Chromium/reference consumption order: key -> IV -> RSA padding.
        iv = random_bytes(16)
        modulus, exponent = _parse_rsa_public_key(pem)
        encrypted_key = _rsa_encrypt_pkcs1v15(
            modulus,
            exponent,
            key_hex.encode("ascii"),
            randbytes=random_bytes,
        )
    else:
        key_hex, encrypted_key = session_material
        if len(key_hex) != 32 or len(encrypted_key) != 256:
            raise ValueError("dtrait session_material 格式错误")
        iv = random_bytes(16)

    key = bytes.fromhex(key_hex)
    payload = {
        "dtrait": blob,
        "timestamp": int(timestamp if timestamp is not None else time.time()),
        "sdkVersion": DEFAULT_TRAIT_SDK_VERSION,
        "path": path,
    }
    plaintext = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    encrypted_payload = _aes_cbc_encrypt(key, iv, plaintext)
    header = "_".join(
        (
            version,
            base64.b64encode(encrypted_key).decode("ascii"),
            base64.b64encode(iv + encrypted_payload).decode("ascii"),
        )
    )
    material = (key_hex, encrypted_key)
    return (header, material) if return_material else header
