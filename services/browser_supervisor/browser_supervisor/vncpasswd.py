"""Generating a TigerVNC password file with a separate view-only password.

Why this exists at all:

Section 14.5 (L2470) requires the Stream Gateway to authenticate to Xtigervnc with
**a distinct, server-only credential — separate for view-only and for control**.
Xtigervnc then enforces view-only on its own side: a client logged in with the view-only
password cannot send input events, no matter what it sends.

For that to work, the capsule has to start Xtigervnc with `-SecurityTypes VncAuth`
and a password file containing BOTH passwords. `vncpasswd -f` can only write one,
so we generate the file ourselves.

The TigerVNC password file format: 8 bytes of the full password, optionally 8 bytes of the
view-only password, each encrypted with DES using a fixed, publicly known obfuscation key.
This is NOT encryption protecting the password — it is obfuscation, exactly as in the original
VNC. The security comes from the file having 0600 permissions, living inside the capsule,
and the passwords being random and living only as long as the process.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

#: The fixed obfuscation key from the original VNC implementation. Publicly known.
OBFUSCATION_KEY = bytes([23, 82, 107, 6, 35, 78, 88, 7])

PASSWORD_LENGTH = 8


def _reverse_bits(byte: int) -> int:
    return int(f"{byte:08b}"[::-1], 2)


def _encrypt(password: str) -> bytes:
    text = password.encode("utf-8")[:PASSWORD_LENGTH].ljust(PASSWORD_LENGTH, b"\x00")
    key = bytes(_reverse_bits(b) for b in OBFUSCATION_KEY)
    cipher = Cipher(TripleDES(key * 3), modes.ECB())  # the TigerVNC file format
    des_cipher = cipher.encryptor()
    return des_cipher.update(text) + des_cipher.finalize()


def generate_passwords() -> tuple[str, str]:
    """Two random passwords: the full one and the view-only one.

    Eight characters, because VncAuth truncates the password to eight bytes anyway — a longer
    one would give an illusion of strength that is not there.
    """
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    full_password = "".join(secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH))
    view_only = "".join(secrets.choice(alphabet) for _ in range(PASSWORD_LENGTH))
    return full_password, view_only


def write_password_file(path: Path, full_password: str, view_only: str) -> None:
    """Writes the TigerVNC password file with 0600 permissions."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_encrypt(full_password) + _encrypt(view_only))
    path.chmod(0o600)


def write_for_gateway(path: Path, full_password: str, view_only: str) -> None:
    """Writes the passwords in plaintext for the Stream Gateway, with 0600 permissions.

    The Gateway has to know the passwords in order to perform the RFB handshake on behalf of
    the browser (L2470). The file lives in the shared capsule runtime volume and is NEVER
    passed to the browser or to the model runtime.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"full={full_password}\nview_only={view_only}\n", encoding="utf-8")
    path.chmod(0o600)
