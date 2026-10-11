"""Server-side RFB authentication (VncAuth).

Section 14.5 (L2470) states a requirement that a transparent websockify cannot meet
(quoted verbatim from the Polish source document):

    "Wybrany Stream Gateway jest RFB-aware i uwierzytelnia upstream odrębnym, wyłącznie
     serwerowym credentialem TigerVNC dla view-only albo control; credential nigdy nie
     trafia do przeglądarki. Zwykły transparentny websockify nie może być jedynym
     enforcementem."

That is why the handshake with Xtigervnc is performed by the **gateway**, not by the
browser. The browser receives an already authenticated stream and never sees the password.

About the algorithm itself: VncAuth is challenge-response over DES. Two properties that
are easy to overlook and that break the implementation silently:

1. The password is truncated/padded to exactly 8 bytes.
2. **Every byte of the key has its bit order reversed** — this is a historical property
   of the AT&T implementation, not a mistake. Without it the response is formally
   correct, but the server rejects it.

Single DES is not exposed directly by `cryptography`, but 3DES with three identical keys
is equivalent to it (K1=K2=K3 ⇒ EDE = E).

This is NOT a mechanism that protects us from a network attacker — VncAuth is weak and old.
It protects against something else: against the credential leaking to the browser. The real
network boundary is that the VNC socket is a unix socket inside the capsule and does not
exist as a TCP port.
"""

from __future__ import annotations

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes

PASSWORD_LENGTH = 8
CHALLENGE_LENGTH = 16


def _reverse_bits(byte: int) -> int:
    """Reverses the bit order within a byte. A VncAuth requirement."""
    return int(f"{byte:08b}"[::-1], 2)


def des_key(password: bytes) -> bytes:
    """Turns a password into an 8-byte DES key following the VncAuth rules."""
    truncated = password[:PASSWORD_LENGTH].ljust(PASSWORD_LENGTH, b"\x00")
    return bytes(_reverse_bits(b) for b in truncated)


def challenge_response(password: bytes, challenge: bytes) -> bytes:
    """Computes the response to the server's 16-byte challenge.

    The challenge is encrypted with DES in ECB, block by block, with the same key.
    """
    if len(challenge) != CHALLENGE_LENGTH:
        raise ValueError(f"Challenge RFB ma {len(challenge)} bajtów, oczekiwano {CHALLENGE_LENGTH}")

    key = des_key(password)
    # 3DES with K1=K2=K3 is equivalent to single DES.
    cipher = Cipher(TripleDES(key * 3), modes.ECB())
    des_cipher = cipher.encryptor()
    return des_cipher.update(challenge) + des_cipher.finalize()
