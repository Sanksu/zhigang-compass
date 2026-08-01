"""生成 RSA-256 密钥对用于 JWT 签名。

用法：
    uv run python scripts/generate_keys.py
"""

from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEYS_DIR = Path(__file__).resolve().parent.parent / "keys"


def main():
    KEYS_DIR.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # 私钥 PEM（PKCS#1 格式，头：-----BEGIN RSA PRIVATE KEY-----）
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (KEYS_DIR / "private.pem").write_bytes(private_pem)

    # 公钥 PEM（SubjectPublicKeyInfo 格式，头：-----BEGIN PUBLIC KEY-----）
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    (KEYS_DIR / "public.pem").write_bytes(public_pem)

    print(f"✅ RSA 密钥对已生成：{KEYS_DIR}")


if __name__ == "__main__":
    main()