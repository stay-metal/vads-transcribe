"""Примитивы аутентификации (спека §8): пароль (bcrypt), подпись сессии (itsdangerous).

Сессионная cookie = подписанный `"{user}|{epoch}"`. `epoch` сверяется с БД на каждом
запросе → бамп epoch мгновенно инвалидирует все cookie. Ключ подписи (`session_key`)
ОТДЕЛЁН от ключа шифрования секретов (`fernet_key`).

Примечание: bcrypt усекает пароль до 72 байт — для длинной high-entropy фразы это
не снижает практическую стойкость; документируем как ограничение v1.
"""

from __future__ import annotations

import hmac

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_SESSION_SALT = "bloodtranscripts.session.v1"


def hash_password(plain: str) -> str:
    """Сгенерировать bcrypt-хэш (для подготовки BLOODTRANSCRIPTS_PASSWORD_HASH)."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time проверка пароля против bcrypt-хэша."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def verify_user(candidate: str, expected: str) -> bool:
    """Constant-time сравнение имени пользователя."""
    return hmac.compare_digest(candidate.encode("utf-8"), expected.encode("utf-8"))


def _serializer(session_key: str) -> URLSafeTimedSerializer:
    # URL-safe (base64) — payload может содержать не-ASCII имя (кириллица),
    # cookie остаётся ASCII; '|' в имени тоже не проблема (структурный JSON).
    return URLSafeTimedSerializer(session_key, salt=_SESSION_SALT)


def issue_session(session_key: str, user: str, epoch: int) -> str:
    """Подписанный токен сессии для cookie."""
    return _serializer(session_key).dumps({"u": user, "e": epoch})


def verify_session(
    session_key: str,
    token: str,
    *,
    max_age: int,
    expected_user: str,
    expected_epoch: int,
) -> str | None:
    """Вернуть имя пользователя, если токен валиден, свеж и epoch совпал; иначе None."""
    if not session_key or not token:
        return None
    try:
        data = _serializer(session_key).loads(token, max_age=max_age)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(data, dict):
        return None
    user = data.get("u")
    epoch = data.get("e")
    if not isinstance(user, str) or not isinstance(epoch, int):
        return None
    # Сравнение на байтах (как verify_user): str-compare_digest падает на не-ASCII имени.
    if not hmac.compare_digest(user.encode("utf-8"), expected_user.encode("utf-8")):
        return None
    if epoch != expected_epoch:
        return None
    return user
