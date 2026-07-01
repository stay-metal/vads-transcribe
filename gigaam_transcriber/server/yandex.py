"""Яндекс.Диск — ручной ingestion (M5, спека §4.3).

Токен личного аккаунта (debug-token) хранится Fernet-шифрованным в БД; проверяется
`check_token()` ДО записи; тело токена не логируется. browse листает папку, pull
делает exactly-once claim по `path:revision` (INSERT OR IGNORE) и ставит скачивание
на io-очередь (не занимает GPU-слот). Скачивание → создание записи/джобы → gpu.

Клиент абстрагирован (`app.state.yandex_factory`) — тесты подменяют фейком, без сети.
"""

from __future__ import annotations

import json
import os
import unicodedata
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import crypto
from .auth import require_session
from .repository import (
    claim_ingest,
    create_job,
    create_recording,
    get_ingest_source,
    get_yandex_auth,
    record_stability,
    set_job_dirs,
    set_recording_latest_job,
    set_yandex_token,
    update_ingest,
    upsert_ingest_source,
)

# Авто-watch: клеймим запись только когда её сигнатура неизменна ≥ N поллингов.
STABILITY_THRESHOLD = 2

router = APIRouter()

AUDIO_EXT = {
    ".wav",
    ".mp3",
    ".m4a",
    ".mp4",
    ".mov",
    ".ogg",
    ".opus",
    ".flac",
    ".webm",
    ".mkv",
    ".aac",
}


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


def _build_client(request: Request) -> Any:  # duck-typed клиент (реальный/фейк)
    """Собрать клиент из сохранённого (расшифрованного) токена или None."""
    settings = request.app.state.settings
    auth = get_yandex_auth(settings.db_path)
    if auth is None:
        return None
    token = crypto.decrypt(settings.fernet_key, auth["token_enc"])
    if not token:
        return None
    factory = getattr(request.app.state, "yandex_factory", _default_factory)
    return factory(token)


def _under_watch_dir(path: str, watch_dir: str) -> bool:
    """allowlist: путь обязан быть внутри watch_dir (анти-traversal по Я.Диску).

    Нормализуем `..`/двойные слэши перед сравнением, иначе "/watch/../secret" или
    "/watchEVIL" обошли бы наивный prefix-match.
    """
    import posixpath

    if not watch_dir or watch_dir == "/":
        return True
    norm = posixpath.normpath(path)
    base = posixpath.normpath(watch_dir)
    return norm == base or norm.startswith(base + "/")


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
    return {"connected": auth is not None, "check_ok": bool(auth and auth["check_ok"])}


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


@router.get("/api/yandex/browse")
def browse(request: Request, path: str = "/", user: str = Depends(require_session)) -> dict:
    watch_dir = os.getenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/")
    if not _under_watch_dir(path, watch_dir):
        raise HTTPException(403, "Путь вне разрешённой папки")
    client = _build_client(request)
    if client is None:
        raise HTTPException(400, "Токен Яндекс.Диска не настроен")
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


def ingest_path(settings, client, path: str, enqueue_io) -> dict:
    """Общий путь ingestion (ручной pull и авто-watch): claim по `path:revision` +
    enqueue скачивания. Не HTTP-зависима — бросает IngestError, не HTTPException."""
    try:
        meta = client.get_meta(path)
    except Exception:
        raise IngestError(404, "Путь не найден на Яндекс.Диске")

    if meta["type"] == "dir":
        entries = [
            e
            for e in client.listdir(path)
            if e["type"] == "file" and Path(e["name"]).suffix.lower() in AUDIO_EXT
        ]
        if not entries:
            raise IngestError(400, "В папке нет аудио-дорожек")
        kind = "route_a" if len(entries) > 1 else "single"
        revision = str(meta.get("revision") or max((e.get("revision") or 0) for e in entries))
    else:
        entries = [meta | {"name": meta["name"], "path": meta["path"]}]
        kind = "single"
        revision = str(meta.get("revision") or 0)

    ingest_key = f"{path}:{revision}"
    surrogate = claim_ingest(settings.db_path, ingest_key, meta.get("resource_id"))
    if surrogate is None:  # уже подтягивали эту ревизию — дедуп, без второй джобы
        return {"status": "already_seen", "ingest_key_seen": True}

    remote_tracks = [{"name": _name(e["name"]), "remote": e["path"]} for e in entries]
    if enqueue_io is not None:
        enqueue_io(surrogate, kind, remote_tracks)
    return {"status": "pulling", "surrogate_id": surrogate, "kind": kind}


@router.post("/api/yandex/pull")
def pull(payload: PullIn, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    watch_dir = os.getenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/")
    if not _under_watch_dir(payload.path, watch_dir):
        raise HTTPException(403, "Путь вне разрешённой папки")
    client = _build_client(request)
    if client is None:
        raise HTTPException(400, "Токен Яндекс.Диска не настроен")
    enqueue_io = getattr(request.app.state, "enqueue_io", None)
    try:
        return ingest_path(settings, client, payload.path, enqueue_io)
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
        audio = [
            c
            for c in children
            if c["type"] == "file" and Path(c["name"]).suffix.lower() in AUDIO_EXT
        ]
        if not audio or any(c.get("md5") is None for c in audio):
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
        if not _under_watch_dir(e["path"], watch_dir):
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
    """Собрать клиент из сохранённого токена вне HTTP-контекста (для periodic_task)."""
    auth = get_yandex_auth(settings.db_path)
    if auth is None:
        return None
    token = crypto.decrypt(settings.fernet_key, auth["token_enc"])
    return factory(token) if token else None


class IngestSourceIn(BaseModel):
    watch_dir: str
    enabled: bool = False
    poll_interval: int = 300
    default_params: dict = {}


@router.get("/api/ingest/source")
def get_source(request: Request, user: str = Depends(require_session)) -> dict:
    src = get_ingest_source(request.app.state.settings.db_path)
    if src is None:
        return {"configured": False}
    return {
        "configured": True,
        "watch_dir": src["watch_dir"],
        "enabled": src["enabled"],
        "poll_interval": src["poll_interval"],
        "default_params": json.loads(src["default_params"] or "{}"),
    }


@router.put("/api/ingest/source")
def put_source(
    payload: IngestSourceIn, request: Request, user: str = Depends(require_session)
) -> dict:
    settings = request.app.state.settings
    watch_dir = os.getenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/")
    # Конфигурируемый watch_dir обязан быть под серверным allowlist (анти-обход).
    if not _under_watch_dir(payload.watch_dir, watch_dir):
        raise HTTPException(403, "watch_dir вне разрешённой области")
    upsert_ingest_source(
        settings.db_path,
        payload.watch_dir,
        payload.enabled,
        max(60, int(payload.poll_interval)),
        json.dumps(payload.default_params, ensure_ascii=False),
    )
    return {"configured": True, "watch_dir": payload.watch_dir, "enabled": payload.enabled}


def _name(filename: str) -> str:
    return unicodedata.normalize("NFC", Path(filename).stem) or "track"


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
            tracks.append({"name": t["name"], "path": str(local), "size": local.stat().st_size})

        rec_id = create_recording(
            db,
            origin="yandex",
            kind=kind,
            tracks=tracks,
            title=tracks[0]["name"] if tracks else None,
        )
        params: dict[str, Any] = {"glossary": True}
        if kind == "single":
            params["diarization"] = "pyannote" if os.getenv("HF_TOKEN") else "none"
        job_id = create_job(db, mode=kind, source="yandex", recording_id=rec_id, params=params)
        output_dir = Path(settings.data_dir) / "outputs" / job_id
        set_job_dirs(
            db,
            job_id,
            work_dir=str(work),
            output_dir=str(output_dir),
            manifest_path=str(output_dir / "manifest.json"),
        )
        set_recording_latest_job(db, rec_id, job_id)
        update_ingest(db, surrogate_id, status="downloaded", recording_id=rec_id, job_id=job_id)
    except Exception:
        # claim остаётся не-терминальным → re-pull переклеймит; чистим частичное.
        update_ingest(db, surrogate_id, status="error")
        shutil.rmtree(work, ignore_errors=True)
        return None
    if enqueue_gpu is not None:
        enqueue_gpu(job_id)
    return job_id
