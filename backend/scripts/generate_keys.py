"""生成 RSA-256 密钥对用于 JWT 签名。

用法：
    uv run python scripts/generate_keys.py          # 首次生成
    uv run python scripts/generate_keys.py --force  # 覆盖已有密钥

说明：
    - 已存在密钥时拒绝覆盖：JWT refresh/access token 用私钥签发，覆盖会使
      已签发 token 全部失效（刷新拉黑依赖 jti，旧 token 将无法校验）。
      确需轮换时显式传 --force。
    - POSIX 下私钥写 0600，避免同机其他用户可读。
"""

import argparse
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KEYS_DIR = Path(__file__).resolve().parent.parent / "keys"
PRIVATE_PATH = KEYS_DIR / "private.pem"
PUBLIC_PATH = KEYS_DIR / "public.pem"


def main() -> int:
    parser = argparse.ArgumentParser(description="生成 JWT RSA-256 密钥对")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的密钥")
    args = parser.parse_args()

    if (PRIVATE_PATH.exists() or PUBLIC_PATH.exists()) and not args.force:
        print(
            f"✗ 密钥已存在（{KEYS_DIR}），拒绝覆盖。"
            "覆盖会使已签发 token 全部失效；确需轮换请加 --force。",
            file=sys.stderr,
        )
        return 1

    KEYS_DIR.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    # 私钥 PEM（PKCS#1 格式，头：-----BEGIN RSA PRIVATE KEY-----）
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )
    PRIVATE_PATH.write_bytes(private_pem)
    if os.name != "nt":
        PRIVATE_PATH.chmod(0o600)

    # 公钥 PEM（SubjectPublicKeyInfo 格式，头：-----BEGIN PUBLIC KEY-----）
    public_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    PUBLIC_PATH.write_bytes(public_pem)

    print(f"✅ RSA 密钥对已生成：{KEYS_DIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
