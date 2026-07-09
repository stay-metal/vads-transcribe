"""Аутентификация по одним общим кредам (спека §8).

- `POST /api/auth/login` — bcrypt + constant-time, ставит подписанную cookie.
- `POST /api/auth/logout` — гасит cookie.
- `GET  /api/auth/me` — текущий пользователь (требует сессию).
- `require_session` — FastAPI-зависимость: 401 без валидной свежей сессии.

Brute-force: глобальный + per-IP счётчики неудач с временным локаутом (`LoginThrottle`),
реальный client-IP берётся из X-Forwarded-For только когда мы за доверенным nginx.
"""

from __future__ import annotations

import threading
import time

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response

from .config import Settings
from .db import get_session_epoch
from .security import issue_session, verify_password, verify_session, verify_user

SESSION_COOKIE = "bt_session"

router = APIRouter()


# --------------------------------------------------------------------------- #
# Защита от перебора
# --------------------------------------------------------------------------- #
class LoginThrottle:
    """Per-IP + глобальный счётчик неудач с экспоненциальным backoff.

    Глобальный порог имеет ЗАПАС над per-IP (`global_max_failures` ≫ `max_failures`),
    чтобы один атакующий IP не уводил в локаут единственного легитимного пользователя
    (DoS). Окно локаута растёт экспоненциально (`lockout * 2^lockouts`, потолок
    `max_lockout_seconds`). Истёкшие per-IP записи вытесняются (нет утечки памяти).
    """

    def __init__(
        self,
        max_failures: int,
        lockout_seconds: int,
        global_max_failures: int | None = None,
        max_lockout_seconds: int = 3600,
    ):
        self.max_failures = max_failures
        self.lockout_seconds = lockout_seconds
        self.global_max_failures = global_max_failures or max_failures * 5
        self.max_lockout_seconds = max_lockout_seconds
        self._lock = threading.Lock()
        self._global_failures = 0
        self._global_lock_until = 0.0
        self._global_lockouts = 0
        self._per_ip: dict[str, list] = {}  # ip -> [failures, lock_until, lockouts]

    def _backoff(self, lockouts: int) -> float:
        return min(self.lockout_seconds * (2**lockouts), self.max_lockout_seconds)

    def _evict(self, now: float) -> None:
        dead = [ip for ip, r in self._per_ip.items() if r[0] == 0 and r[1] <= now]
        for ip in dead:
            del self._per_ip[ip]

    def retry_after(self, ip: str, now: float | None = None) -> int:
        """0, если попытка разрешена; иначе секунды до разблокировки."""
        now = time.time() if now is None else now
        with self._lock:
            self._evict(now)
            wait = self._global_lock_until - now
            rec = self._per_ip.get(ip)
            if rec is not None:
                wait = max(wait, rec[1] - now)
            return int(wait) + 1 if wait > 0 else 0

    def record_failure(self, ip: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._global_failures += 1
            if self._global_failures >= self.global_max_failures:
                self._global_lock_until = now + self._backoff(self._global_lockouts)
                self._global_lockouts += 1
                self._global_failures = 0
            rec = self._per_ip.setdefault(ip, [0, 0.0, 0])
            rec[0] += 1
            if rec[0] >= self.max_failures:
                rec[1] = now + self._backoff(rec[2])
                rec[2] += 1
                rec[0] = 0

    def record_success(self, ip: str) -> None:
        with self._lock:
            self._global_failures = 0
            self._global_lockouts = 0
            self._per_ip.pop(ip, None)


def client_ip(request: Request, settings: Settings) -> str:
    """Реальный IP за доверенным nginx.

    nginx БЕЗУСЛОВНО перезаписывает `X-Real-IP = $remote_addr` (см. deploy/nginx.conf),
    поэтому доверяем ему. ЛЕВЫЙ `X-Forwarded-For` клиент-контролируем — его спуфинг
    обходил бы per-IP throttle, поэтому из XFF берём только ПРАВЫЙ элемент (hop,
    добавленный доверенным прокси), и только когда мы действительно за nginx.
    """
    if settings.require_https:
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------- #
# Зависимость сессии
# --------------------------------------------------------------------------- #
def require_session(request: Request) -> str:
    settings: Settings = request.app.state.settings
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="Не авторизовано")
    epoch = get_session_epoch(settings.db_path)
    user = verify_session(
        settings.session_key,
        token,
        max_age=settings.session_max_age,
        expected_user=settings.user,
        expected_epoch=epoch,
    )
    if user is None:
        raise HTTPException(status_code=401, detail="Сессия недействительна")
    return user


# --------------------------------------------------------------------------- #
# Эндпоинты
# --------------------------------------------------------------------------- #
@router.post("/api/auth/login")
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
) -> dict:
    settings: Settings = request.app.state.settings
    throttle: LoginThrottle = request.app.state.login_throttle
    ip = client_ip(request, settings)

    wait = throttle.retry_after(ip)
    if wait > 0:
        raise HTTPException(
            status_code=429,
            detail=f"Слишком много попыток. Повторите через {wait} с.",
        )

    # Обе проверки вычисляются всегда (без short-circuit) — против тайминг-атак.
    user_ok = verify_user(username, settings.user)
    pass_ok = verify_password(password, settings.password_hash)
    if not (user_ok and pass_ok):
        throttle.record_failure(ip)
        raise HTTPException(status_code=401, detail="Неверный логин или пароль")

    throttle.record_success(ip)
    epoch = get_session_epoch(settings.db_path)
    token = issue_session(settings.session_key, settings.user, epoch)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_max_age,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return {"status": "ok", "user": settings.user}


@router.post("/api/auth/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "ok"}


@router.get("/api/auth/me")
def me(user: str = Depends(require_session)) -> dict:
    return {"user": user}
