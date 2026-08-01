
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import pathlib

keys_dir = pathlib.Path('c:/Users/Administrator/zhigang-compass/backend/keys')
keys_dir.mkdir(parents=True, exist_ok=True)

# Generate RSA 2048-bit private key
private_key = rsa.generate_private_key(
    public_exponent=65537,
    key_size=2048
)
public_key = private_key.public_key()

# Save private key in PKCS#1 (TraditionalOpenSSL) format - PEM header: RSA PRIVATE KEY
(keys_dir / 'private.pem').write_bytes(
    private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )
)

# Save public key in SubjectPublicKeyInfo format - PEM header: PUBLIC KEY
(keys_dir / 'public.pem').write_bytes(
    public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
)

print('RSA keys generated successfully')
print(f'Private key: {keys_dir / "private.pem"}')
print(f'Public key: {keys_dir / "public.pem"}')
