"""Конфигурация сервера из переменных окружения (спека §8/§11).

Без pydantic-settings — простой dataclass, чтобы тесты могли собирать Settings
напрямую с временными путями/ключами. Секреты НЕ логируются.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

# Безопасные дефолты лимитов: записи Ponimaiu разные (~30 мин…час+, неск. спикеров)
# → щедрые предохранители от abuse, а не типичный размер (спека §8/решения).
_GB = 1024**3
DEFAULT_MAX_FILE_SIZE = 3 * _GB
DEFAULT_MAX_RECORDING_TOTAL = 5 * _GB
DEFAULT_MAX_DURATION_SEC = 4 * 3600
DEFAULT_SESSION_MAX_AGE = 12 * 3600  # ~12 часов


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return int(raw)


@dataclass
class Settings:
    """Серверные настройки. Источник истины — переменные окружения (`from_env`)."""

    # --- креды единственного пользователя (спека §8) ---
    user: str = "admin"
    password_hash: str = ""  # bcrypt-хэш; пустой → логин невозможен

    # --- РАЗДЕЛЬНЫЕ ключи: подпись сессии vs шифрование секретов at-rest ---
    session_key: str = ""  # подпись cookie (itsdangerous)
    fernet_key: str = ""  # шифрование секретов в БД (Яндекс-токен, M5)

    # --- директории/данные ---
    data_dir: Path = field(default_factory=lambda: Path.home() / ".dialogscribe")
    db_path: Path | None = None  # app.sqlite; None → data_dir/app.sqlite
    ready_flag_path: Path | None = None  # флаг тёплой модели; None → data_dir/worker.ready

    # --- сетевые/безопасность ---
    cookie_secure: bool = True  # cookie Secure (отключать только в dev по HTTP)
    require_https: bool = True  # отвергать X-Forwarded-Proto != https (доверяем nginx)
    session_max_age: int = DEFAULT_SESSION_MAX_AGE

    # --- защита от brute-force (спека §8) ---
    login_max_failures: int = 10  # per-IP порог до временного локаута
    login_lockout_seconds: int = 60  # базовое окно локаута (растёт экспоненциально)
    # Глобальный порог с ЗАПАСОМ над per-IP, иначе один IP блокирует всех (DoS).
    login_global_max_failures: int = 50
    login_max_lockout_seconds: int = 3600  # потолок экспоненциального backoff

    # --- лимиты загрузки (спека §8) ---
    max_file_size: int = DEFAULT_MAX_FILE_SIZE
    max_recording_total: int = DEFAULT_MAX_RECORDING_TOTAL
    max_duration_sec: int = DEFAULT_MAX_DURATION_SEC

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        if self.db_path is None:
            self.db_path = self.data_dir / "app.sqlite"
        if self.ready_flag_path is None:
            self.ready_flag_path = self.data_dir / "worker.ready"
        self.db_path = Path(self.db_path)
        self.ready_flag_path = Path(self.ready_flag_path)

    @classmethod
    def from_env(cls) -> Settings:
        data_dir = Path(os.getenv("DIALOGSCRIBE_DATA_DIR", str(Path.home() / ".dialogscribe")))
        return cls(
            user=os.getenv("DIALOGSCRIBE_USER", "admin"),
            password_hash=os.getenv("DIALOGSCRIBE_PASSWORD_HASH", ""),
            session_key=os.getenv("DIALOGSCRIBE_SESSION_KEY", ""),
            fernet_key=os.getenv("DIALOGSCRIBE_FERNET_KEY", ""),
            data_dir=data_dir,
            cookie_secure=_env_bool("DIALOGSCRIBE_COOKIE_SECURE", True),
            require_https=_env_bool("DIALOGSCRIBE_REQUIRE_HTTPS", True),
            session_max_age=_env_int("DIALOGSCRIBE_SESSION_MAX_AGE", DEFAULT_SESSION_MAX_AGE),
            login_max_failures=_env_int("DIALOGSCRIBE_LOGIN_MAX_FAILURES", 10),
            login_lockout_seconds=_env_int("DIALOGSCRIBE_LOGIN_LOCKOUT_SECONDS", 60),
            login_global_max_failures=_env_int("DIALOGSCRIBE_LOGIN_GLOBAL_MAX_FAILURES", 50),
            login_max_lockout_seconds=_env_int("DIALOGSCRIBE_LOGIN_MAX_LOCKOUT_SECONDS", 3600),
            max_file_size=_env_int("DIALOGSCRIBE_MAX_FILE_SIZE", DEFAULT_MAX_FILE_SIZE),
            max_recording_total=_env_int(
                "DIALOGSCRIBE_MAX_RECORDING_TOTAL", DEFAULT_MAX_RECORDING_TOTAL
            ),
            max_duration_sec=_env_int("DIALOGSCRIBE_MAX_DURATION_SEC", DEFAULT_MAX_DURATION_SEC),
        )

    def validate_for_serve(self) -> list[str]:
        """Список фатальных проблем конфигурации перед реальным запуском (не для тестов)."""
        problems = []
        if not self.password_hash:
            problems.append("DIALOGSCRIBE_PASSWORD_HASH не задан — вход невозможен.")
        if not self.session_key:
            problems.append("DIALOGSCRIBE_SESSION_KEY не задан — подпись сессии небезопасна.")
        if not self.fernet_key:
            problems.append(
                "DIALOGSCRIBE_FERNET_KEY не задан — секреты at-rest шифровались бы "
                "константным публично-выводимым ключом."
            )
        if self.session_key and self.session_key == self.fernet_key:
            problems.append(
                "DIALOGSCRIBE_SESSION_KEY и DIALOGSCRIBE_FERNET_KEY должны различаться "
                "(раздельные ключи: подпись сессии vs шифрование секретов)."
            )
        return problems

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ready_flag_path.parent.mkdir(parents=True, exist_ok=True)
