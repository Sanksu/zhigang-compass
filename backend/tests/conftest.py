"""共享测试夹具（08-14 审查：RSA 密钥对生成在 4 个测试文件重复实现，收敛于此）。

- tmp_rsa_keys：临时 RSA 密钥对（CI 无 keys/*.pem，gitignore 排除）
- _use_tmp_keys：全局注入临时密钥路径（create/decode_token 均经 settings 读取）
"""

from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from app.core.config import settings


@pytest.fixture(scope="session")
def tmp_rsa_keys(tmp_path_factory):
    """生成临时 RSA 密钥对并返回 (私钥路径, 公钥路径)。"""
    tmp = tmp_path_factory.mktemp("jwt-keys")
    priv_path = tmp / "private.pem"
    pub_path = tmp / "public.pem"

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    pub_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return str(priv_path), str(pub_path)


@pytest.fixture(autouse=True)
def _use_tmp_keys(tmp_rsa_keys):
    """全局注入临时密钥路径（create/decode 均经 settings 读取）。"""
    priv, pub = tmp_rsa_keys
    with patch.object(settings, "jwt_private_key_path", priv), \
         patch.object(settings, "jwt_public_key_path", pub):
        yield
