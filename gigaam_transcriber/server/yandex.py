"""Яндекс.Диск — ingestion (ручной pull + авто-watch).

Аутентификация: основной путь — OAuth Authorization Code (`oauth/start`→`callback`)
с авто-refresh истёкшего access-токена (`_valid_access_token`/`_refresh_access`);
`PUT /token` c debug-токеном личного аккаунта оставлен для совместимости. Токены
хранятся Fernet-шифрованными в БД, тело токена не логируется.

browse листает папку; pull/поллер делают exactly-once claim по `path:revision`
(INSERT OR IGNORE) и ставят скачивание на io-очередь (не занимает GPU-слот).
Скачивание → magic-bytes проверка → создание записи/джобы → gpu.

Клиент абстрагирован (`app.state.yandex_factory`) — тесты подменяют фейком, без сети.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from ..exceptions import UnsupportedFormatError
from . import crypto, media
from .auth import require_session
from .ingest_common import STABILITY_THRESHOLD, register_job, under_watch_dir
from .repository import (
    claim_ingest,
    get_ingest_source,
    get_yandex_auth,
    record_stability,
    set_yandex_token,
    update_ingest,
)
from .zoom_scan import drop_video_duplicates

router = APIRouter()

# Расширения, принимаемые ingest'ом Я.Диска — единый источник из библиотечной
# константы (exceptions лёгкий, без ML): любой поддерживаемый аудио/видео-контейнер
# (ffmpeg вытащит дорожку). Видео-ДУБЛИ встречи отсеиваются на уровне папки.
AUDIO_EXT = UnsupportedFormatError.SUPPORTED_AUDIO | UnsupportedFormatError.SUPPORTED_VIDEO


def _dir_audio_entries(children: list[dict]) -> list[dict]:
    """Файлы-кандидаты ingest'а для папки: видео-дубли аудио-дорожек отсеиваются.

    Видео выбрасывается ТОЛЬКО при аудио-паре (Zoom кладёт рядом audio*.m4a и
    video*.mp4 одной записи → ложный route_a, F7); несвязанное видео остаётся
    дорожкой. Общая с zoom_scan реализация (`drop_video_duplicates`). Папка из
    двух видео БЕЗ аудио по-прежнему даст route_a — известное ограничение.
    """
    files = [
        e for e in children if e["type"] == "file" and Path(e["name"]).suffix.lower() in AUDIO_EXT
    ]
    return drop_video_duplicates(
        files,
        stem_of=lambda e: Path(e["name"]).stem,
        suffix_of=lambda e: Path(e["name"]).suffix.lower(),
    )


# --------------------------------------------------------------------------- #
# Клиент (реальный поверх yadisk; в тестах подменяется фейком)
# --------------------------------------------------------------------------- #
class YaDiskClient:
    """Тонкая обёртка над yadisk (Production/Stable, обходит троттл 128 КиБ/с)."""

    def __init__(self, token: str):
        import yadisk

        self._y = yadisk.YaDisk(token=token)

    def check(self) -> bool:
        try:
            return bool(self._y.check_token())
        except Exception:
            return False

    def listdir(self, path: str) -> list[dict]:
        out = []
        for r in self._y.listdir(path):
            out.append(
                {
                    "name": r.name,
                    "path": r.path,
                    "type": r.type,  # file | dir
                    "size": getattr(r, "size", None),
                    "md5": getattr(r, "md5", None),
                    "revision": getattr(r, "revision", None),
                    "resource_id": getattr(r, "resource_id", None),
                }
            )
        return out

    def get_meta(self, path: str) -> dict:
        r = self._y.get_meta(path)
        return {
            "name": r.name,
            "path": r.path,
            "type": r.type,
            "revision": getattr(r, "revision", None),
            "resource_id": getattr(r, "resource_id", None),
        }

    def download(self, remote: str, local: str) -> None:
        self._y.download(remote, local)


def _default_factory(token: str) -> YaDiskClient:
    return YaDiskClient(token)


def _oauth_config() -> tuple[str, str, str] | None:
    """(client_id, client_secret, redirect_uri) из env или None, если OAuth не настроен."""
    cid = os.getenv("YANDEX_OAUTH_CLIENT_ID")
    secret = os.getenv("YANDEX_OAUTH_CLIENT_SECRET")
    if not cid or not secret:
        return None
    redirect = os.getenv(
        "DIALOGSCRIBE_OAUTH_REDIRECT", "http://localhost:8000/api/yandex/oauth/callback"
    )
    return cid, secret, redirect


def _token_request(data: dict) -> dict | None:
    """POST oauth.yandex.ru/token; None на любой сетевой/HTTP-ошибке (инъектируем в тестах)."""
    import httpx

    try:
        r = httpx.post("https://oauth.yandex.ru/token", data=data, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def _store_tokens(settings, tok: dict, *, keep_refresh_enc: str | None = None) -> None:
    from datetime import datetime, timedelta, timezone

    access = tok["access_token"]
    expires_in = int(tok.get("expires_in", 3600))
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=max(60, expires_in - 60))
    ).isoformat()
    token_enc = crypto.encrypt(settings.fernet_key, access)
    new_refresh = tok.get("refresh_token")
    refresh_enc = (
        crypto.encrypt(settings.fernet_key, new_refresh) if new_refresh else keep_refresh_enc
    )
    set_yandex_token(
        settings.db_path,
        token_enc,
        check_ok=True,
        refresh_token_enc=refresh_enc,
        expires_at=expires_at,
    )


def _refresh_access(settings, auth: dict) -> str | None:
    """Обменять refresh_token на новый access. None → refresh недоступен/упал."""
    cfg = _oauth_config()
    refresh_enc = auth.get("refresh_token_enc")
    if cfg is None or not refresh_enc:
        return None
    cid, secret, _ = cfg
    refresh = crypto.decrypt(settings.fernet_key, refresh_enc)
    if not refresh:
        return None
    tok = _token_request(
        {
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": cid,
            "client_secret": secret,
        }
    )
    if tok is None or "access_token" not in tok:
        return None
    _store_tokens(settings, tok, keep_refresh_enc=refresh_enc)
    return str(tok["access_token"])


def _valid_access_token(settings) -> str | None:
    """Актуальный access-токен: refresh если истёк (OAuth); debug-token без expires
    отдаётся как есть (обратная совместимость).

    Истёк, а refresh недоступен/упал → None: заведомо просроченный токен не
    отдаём (вызывающий трактует None как «нужна переавторизация Яндекс»)."""
    from datetime import datetime, timezone

    auth = get_yandex_auth(settings.db_path)
    if auth is None:
        return None
    expires_at = auth.get("expires_at")
    if expires_at:
        try:
            expired = datetime.now(timezone.utc) >= datetime.fromisoformat(expires_at)
        except ValueError:
            expired = False
        if expired:
            return _refresh_access(settings, auth)  # None → нужна переавторизация
    return crypto.decrypt(settings.fernet_key, auth["token_enc"])


def _build_client(request: Request) -> Any:  # duck-typed клиент (реальный/фейк)
    """Собрать клиент из актуального (при нужде обновлённого) токена или None."""
    settings = request.app.state.settings
    token = _valid_access_token(settings)
    if not token:
        return None
    factory = getattr(request.app.state, "yandex_factory", _default_factory)
    return factory(token)


def _require_client(request: Request) -> Any:
    """Клиент или понятный HTTP-отказ. Токен настроен, но невалиден (истёк +
    refresh не удался) → 401 «нужна переавторизация»; вовсе не настроен → 400."""
    client = _build_client(request)
    if client is not None:
        return client
    if get_yandex_auth(request.app.state.settings.db_path) is not None:
        raise HTTPException(401, "Требуется переавторизация Яндекс.Диска")
    raise HTTPException(400, "Токен Яндекс.Диска не настроен")


# --------------------------------------------------------------------------- #
# Эндпоинты
# --------------------------------------------------------------------------- #
class TokenIn(BaseModel):
    token: str


class PullIn(BaseModel):
    path: str


@router.get("/api/yandex/status")
def status(request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    auth = get_yandex_auth(settings.db_path)
    connected = auth is not None
    # check_ok = токен фактически годен СЕЙЧАС (истёкший обновляется по refresh);
    # None → переавторизация. Refresh дёргается лишь при истечении (раз в час).
    check_ok = connected and _valid_access_token(settings) is not None
    return {
        "connected": connected,
        "check_ok": check_ok,
        "reason": None if check_ok or not connected else "Требуется переавторизация Яндекс.Диска",
        "oauth_available": _oauth_config() is not None,
    }


@router.put("/api/yandex/token")
def put_token(payload: TokenIn, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    factory = getattr(request.app.state, "yandex_factory", _default_factory)
    client = factory(payload.token)
    if not client.check():  # валидация ДО записи; тело токена не логируем
        raise HTTPException(400, "Токен Яндекс.Диска недействителен")
    token_enc = crypto.encrypt(settings.fernet_key, payload.token)
    set_yandex_token(settings.db_path, token_enc, check_ok=True)
    return {"connected": True, "check_ok": True}


def _oauth_serializer(settings):
    from itsdangerous import URLSafeTimedSerializer

    return URLSafeTimedSerializer(settings.session_key, salt="yandex-oauth")


@router.get("/api/yandex/oauth/start")
def oauth_start(request: Request, user: str = Depends(require_session)):
    """Редирект на Яндекс OAuth (Authorization Code). CSRF-state — в подписанной cookie."""
    cfg = _oauth_config()
    if cfg is None:
        raise HTTPException(400, "OAuth не настроен (нет YANDEX_OAUTH_CLIENT_ID/SECRET)")
    cid, _, redirect = cfg
    settings = request.app.state.settings
    state = secrets.token_urlsafe(24)
    url = (
        "https://oauth.yandex.ru/authorize?response_type=code"
        f"&client_id={cid}&redirect_uri={quote(redirect, safe='')}"
        f"&scope={quote('cloud_api:disk.read')}&state={state}"
    )
    resp = RedirectResponse(url)
    # SameSite=Lax: cookie переживёт top-level redirect обратно с oauth.yandex.ru.
    resp.set_cookie(
        "ya_oauth_state",
        _oauth_serializer(settings).dumps(state),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=600,
        path="/",
    )
    return resp


@router.get("/api/yandex/oauth/callback")
def oauth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Приём кода Яндекса → обмен на токены. Публичный (session-cookie Strict не
    приходит при cross-site redirect); защита — сверка state с подписанной cookie."""
    from itsdangerous import BadData

    settings = request.app.state.settings
    if error or not code:
        return RedirectResponse("/settings?yandex=error", status_code=303)
    cfg = _oauth_config()
    if cfg is None:
        raise HTTPException(400, "OAuth не настроен")
    signed = request.cookies.get("ya_oauth_state", "")
    try:
        expected = _oauth_serializer(settings).loads(signed, max_age=600)
    except BadData:
        raise HTTPException(400, "Недействительный OAuth-state")
    # compare_digest на bytes — не падает на non-ASCII (злонамеренный state → 400, не 500).
    if not state or not secrets.compare_digest(
        str(state).encode("utf-8"), str(expected).encode("utf-8")
    ):
        raise HTTPException(400, "OAuth-state не совпадает")
    cid, secret, redirect = cfg
    tok = _token_request(
        {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": cid,
            "client_secret": secret,
            "redirect_uri": redirect,
        }
    )
    if tok is None or "access_token" not in tok:
        return RedirectResponse("/settings?yandex=error", status_code=303)
    _store_tokens(settings, tok)
    resp = RedirectResponse("/settings?yandex=connected", status_code=303)
    resp.delete_cookie("ya_oauth_state", path="/")
    return resp


@router.get("/api/yandex/browse")
def browse(request: Request, path: str = "/", user: str = Depends(require_session)) -> dict:
    watch_dir = os.getenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/")
    if not under_watch_dir(path, watch_dir):
        raise HTTPException(403, "Путь вне разрешённой папки")
    client = _require_client(request)
    try:
        entries = client.listdir(path)
    except Exception:
        raise HTTPException(502, "Не удалось прочитать папку Яндекс.Диска")
    return {"path": path, "entries": entries}


class IngestError(Exception):
    """Ошибка ingestion c HTTP-статусом (роут маппит в HTTPException)."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def ingest_path(settings, client, path: str, enqueue_io, *, allow_reclaim: bool = False) -> dict:
    """Общий путь ingestion (ручной pull и авто-watch): claim по `path:revision` +
    enqueue скачивания. Не HTTP-зависима — бросает IngestError, не HTTPException.

    `allow_reclaim` — переклеймить упавшую (`error`) загрузку той же ревизии:
    True для ручного pull (пользователь явно повторяет), False для авто-поллера."""
    try:
        meta = client.get_meta(path)
    except Exception:
        raise IngestError(404, "Путь не найден на Яндекс.Диске")

    if meta["type"] == "dir":
        entries = _dir_audio_entries(client.listdir(path))
        if not entries:
            raise IngestError(400, "В папке нет аудио-дорожек")
        kind = "route_a" if len(entries) > 1 else "single"
        # Ключ дедупа — от ревизий КЛЕЙМИМЫХ файлов, не папки: доливка
        # отфильтрованного видео-дубля бампает ревизию папки, но не должна
        # порождать второй ingest той же встречи.
        revision = str(max((e.get("revision") or 0) for e in entries) or meta.get("revision") or 0)
    else:
        # Одиночный файл: расширение по allowlist ДО клейма (ручной pull мог указать
        # на .txt/архив); контент дополнительно проверит magic-bytes после скачивания.
        if Path(meta["name"]).suffix.lower() not in AUDIO_EXT:
            raise IngestError(415, "Неподдерживаемый формат файла")
        entries = [meta]
        kind = "single"
        revision = str(meta.get("revision") or 0)

    ingest_key = f"{path}:{revision}"
    surrogate = claim_ingest(
        settings.db_path, ingest_key, meta.get("resource_id"), allow_reclaim=allow_reclaim
    )
    if surrogate is None:  # уже подтягивали эту ревизию — дедуп, без второй джобы
        return {"status": "already_seen", "ingest_key_seen": True}

    remote_tracks = [
        {"name": media.nfc_label(e["name"], "track"), "remote": e["path"]} for e in entries
    ]
    if enqueue_io is not None:
        enqueue_io(surrogate, kind, remote_tracks)
    return {"status": "pulling", "surrogate_id": surrogate, "kind": kind}


@router.post("/api/yandex/pull")
def pull(payload: PullIn, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    watch_dir = os.getenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/")
    if not under_watch_dir(payload.path, watch_dir):
        raise HTTPException(403, "Путь вне разрешённой папки")
    client = _require_client(request)
    enqueue_io = getattr(request.app.state, "enqueue_io", None)
    try:
        # Ручной pull: повторный клик по упавшей загрузке должен переклеймить её.
        return ingest_path(settings, client, payload.path, enqueue_io, allow_reclaim=True)
    except IngestError as e:
        raise HTTPException(e.status, e.detail)


# --------------------------------------------------------------------------- #
# Авто-watch: поллер + конфиг источника
# --------------------------------------------------------------------------- #
def _signature(client, entry: dict) -> str | None:
    """Сигнатура (тип|размер/дети|ревизия) элемента верхнего уровня watch_dir.

    None → элемент ещё НЕ стабилен: не аудио, либо файл(ы) без md5 (дозаливается).
    Клеймим только когда сигнатура неизменна ≥ STABILITY_THRESHOLD поллингов."""
    if entry["type"] == "dir":
        try:
            children = client.listdir(entry["path"])
        except Exception:
            return None
        # len/rev — по клеймимым файлам (тот же отбор, что в ingest_path), но
        # барьер «дозаливается» держим по ВСЕМ кандидатам включая видео-дубли:
        # ранний клейм до конца синхронизации папки терял бы поздние дорожки.
        all_files = [
            c
            for c in children
            if c["type"] == "file" and Path(c["name"]).suffix.lower() in AUDIO_EXT
        ]
        audio = _dir_audio_entries(children)
        if not audio or any(c.get("md5") is None for c in all_files):
            return None  # пусто или файлы ещё грузятся
        rev = max((c.get("revision") or 0) for c in audio)
        return f"dir|{len(audio)}|{rev}"
    if Path(entry["name"]).suffix.lower() not in AUDIO_EXT or entry.get("md5") is None:
        return None
    return f"file|{entry.get('size') or 0}|{entry.get('revision') or 0}"


def poll_ingest_sources(settings, client, enqueue_io) -> list[dict]:
    """Один проход авто-watch: для стабильных элементов watch_dir → ingest_path.

    Идемпотентно: `ingest_seen` дедупит по `path:revision`, `ingest_stability`
    держит окно (клеймим лишь после N неизменных поллингов). Ошибки элемента
    не роняют проход."""
    src = get_ingest_source(settings.db_path)
    if not src or not src["enabled"]:
        return []
    watch_dir = src["watch_dir"]
    try:
        entries = client.listdir(watch_dir)
    except Exception:
        return []
    out: list[dict] = []
    for e in entries:
        if not under_watch_dir(e["path"], watch_dir):
            continue
        sig = _signature(client, e)
        if sig is None:
            continue
        if record_stability(settings.db_path, e["path"], sig) < STABILITY_THRESHOLD:
            continue  # ещё не устоялось
        try:
            out.append({"path": e["path"], **ingest_path(settings, client, e["path"], enqueue_io)})
        except IngestError as ie:
            out.append({"path": e["path"], "status": "error", "detail": ie.detail})
    return out


def build_client_from_settings(settings, factory=_default_factory):
    """Собрать клиент из актуального токена вне HTTP-контекста (для periodic_task);
    OAuth-refresh при истечении переиспользуется через `_valid_access_token`."""
    token = _valid_access_token(settings)
    return factory(token) if token else None


# --------------------------------------------------------------------------- #
# Worker (io-очередь): скачать дорожки → создать запись/джобу → enqueue gpu
# --------------------------------------------------------------------------- #
def ingest_pull(
    settings, surrogate_id: str, kind: str, remote_tracks: list, client, enqueue_gpu
) -> str | None:
    """Скачивание на io-воркере (без GPU). Возвращает job_id или None при ошибке."""
    import shutil

    db = settings.db_path
    work = Path(settings.data_dir) / "uploads" / surrogate_id
    work.mkdir(parents=True, exist_ok=True)
    update_ingest(db, surrogate_id, status="downloading")
    try:
        tracks = []
        for i, t in enumerate(remote_tracks):
            suffix = Path(t["remote"]).suffix.lower()
            local = work / f"{i:02d}{suffix}"
            tmp = local.with_suffix(local.suffix + ".part")
            client.download(t["remote"], str(tmp))
            os.replace(tmp, local)  # atomic-rename после полного скачивания
            # Формат — по сигнатуре, не по суффиксу: скачанное могло оказаться
            # HTML-заглушкой/чужим файлом. Несоответствие → except → status=error
            # + rmtree(work), запись/джоба НЕ создаются (как и при сбое download).
            with open(local, "rb") as fh:
                head = fh.read(64)
            if media.sniff_media(head) is None:
                raise UnsupportedFormatError(str(local))
            tracks.append({"name": t["name"], "path": str(local), "size": local.stat().st_size})

        params: dict[str, Any] = {"glossary": True}
        if kind == "single":
            params["diarization"] = "pyannote" if os.getenv("HF_TOKEN") else "none"
        rec_id, job_id = register_job(
            settings,
            origin="yandex",
            kind=kind,
            tracks=tracks,
            title=tracks[0]["name"] if tracks else None,
            params=params,
            work_dir=str(work),
        )
        update_ingest(db, surrogate_id, status="downloaded", recording_id=rec_id, job_id=job_id)
    except Exception:
        # claim остаётся не-терминальным → re-pull переклеймит; чистим частичное.
        update_ingest(db, surrogate_id, status="error")
        shutil.rmtree(work, ignore_errors=True)
        return None
    if enqueue_gpu is not None:
        enqueue_gpu(job_id)
    return job_id
