#!/usr/bin/env python3
"""Generate an Ed25519 keypair for license lease signing."""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main() -> None:
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key()

    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    print("LICENSE_LEASE_PRIVATE_KEY<<EOF")
    print(private_pem.decode("utf-8").strip())
    print("EOF\n")

    print("LICENSE_LEASE_PUBLIC_KEY<<EOF")
    print(public_pem.decode("utf-8").strip())
    print("EOF\n")

    print(f"LICENSE_LEASE_PRIVATE_KEY_B64={base64.b64encode(private_pem).decode('ascii')}")
    print(f"LICENSE_LEASE_PUBLIC_KEY_B64={base64.b64encode(public_pem).decode('ascii')}")


if __name__ == "__main__":
    main()
