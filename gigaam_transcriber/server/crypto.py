"""Шифрование секретов at-rest (спека §8): Fernet на ключе BLOODTRANSCRIPTS_FERNET_KEY.

Ключ Fernet выводится из произвольной строки через SHA-256 → base64 (чтобы не
навязывать формат ключа). Используется для Яндекс-токена в БД (M5).
"""

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(key: str) -> Fernet:
    if not key:
        raise ValueError("Пустой ключ шифрования (BLOODTRANSCRIPTS_FERNET_KEY не задан)")
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(key: str, plaintext: str) -> str:
    return _fernet(key).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(key: str, token: str) -> str | None:
    try:
        return _fernet(key).decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None
