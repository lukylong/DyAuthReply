"""Current bd-ticket-guard ECDH/HKDF primitives."""
from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.kdf.hkdf import HKDF


def _load_private_key(value: str) -> ec.EllipticCurvePrivateKey:
    text = (value or "").strip()
    if "-----BEGIN" in text:
        key = serialization.load_pem_private_key(text.encode("utf-8"), password=None)
        if not isinstance(key, ec.EllipticCurvePrivateKey):
            raise ValueError("bd-ticket private key is not an EC key")
        return key
    return ec.derive_private_key(int(text, 16), ec.SECP256R1())


def _load_server_public_key(value: str) -> ec.EllipticCurvePublicKey:
    text = (value or "").strip()
    if text.startswith("pub."):
        raw = base64.b64decode(text[4:])
        key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
    else:
        cert = x509.load_pem_x509_certificate(text.encode("utf-8"))
        key = cert.public_key()
    if not isinstance(key, ec.EllipticCurvePublicKey):
        raise ValueError("bd-ticket server certificate is not an EC key")
    return key


def derive_ecdh_key(private_key: str, server_cert: str) -> bytes:
    """Derive the 32-byte ticket-guard HMAC key (P-256 ECDH then HKDF-SHA256)."""

    private = _load_private_key(private_key)
    public = _load_server_public_key(server_cert)
    shared = private.exchange(ec.ECDH(), public)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"",
    ).derive(shared)


def hmac_request_sign(payload: str, ecdh_key: bytes) -> str:
    """Return the standard-base64 HMAC-SHA256 signature used by sign type 1."""

    digest = hmac.new(ecdh_key, payload.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")
