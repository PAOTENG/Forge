"""Generate a local CA + Forge/RoomCheck certs for optional A2A mTLS.

Usage (from repo root, forge conda env):
    python backend/scripts/gen_a2a_certs.py

Then point .env at:
    A2A_TLS_CA=infra/certs/a2a-ca.pem
    A2A_TLS_CERT=infra/certs/forge.pem
    A2A_TLS_KEY=infra/certs/forge-key.pem
and serve A2A over HTTPS (separate TLS terminator or uvicorn ssl flags).
HTTP :8787 Vue 值班台不要开 client-cert，避免把浏览器也绑进 mTLS。
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "infra" / "certs"


def _key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _save_key(key: rsa.RSAPrivateKey, path: Path) -> None:
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )


def _save_cert(cert: x509.Certificate, path: Path) -> None:
    path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    until = now + dt.timedelta(days=825)

    ca_key = _key()
    ca = (
        x509.CertificateBuilder()
        .subject_name(_name("Forge A2A CA"))
        .issuer_name(_name("Forge A2A CA"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(until)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    _save_key(ca_key, OUT / "a2a-ca-key.pem")
    _save_cert(ca, OUT / "a2a-ca.pem")

    for cn, filename in (("forge", "forge"), ("roomcheck", "roomcheck")):
        key = _key()
        cert = (
            x509.CertificateBuilder()
            .subject_name(_name(cn))
            .issuer_name(ca.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now)
            .not_valid_after(until)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(__import__("ipaddress").IPv4Address("127.0.0.1"))]),
                critical=False,
            )
            .add_extension(
                x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.CLIENT_AUTH, x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        _save_key(key, OUT / f"{filename}-key.pem")
        _save_cert(cert, OUT / f"{filename}.pem")
    print(f"wrote certs under {OUT}")


if __name__ == "__main__":
    main()
