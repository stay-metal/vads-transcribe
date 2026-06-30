"""Яндекс.Диск — ручной ingestion (M5, спека §4.3).

Токен личного аккаунта (debug-token) хранится Fernet-шифрованным в БД; проверяется
`check_token()` ДО записи; тело токена не логируется. browse листает папку, pull
делает exactly-once claim по `path:revision` (INSERT OR IGNORE) и ставит скачивание
на io-очередь (не занимает GPU-слот). Скачивание → создание записи/джобы → gpu.

Клиент абстрагирован (`app.state.yandex_factory`) — тесты подменяют фейком, без сети.
"""

from __future__ import annotations

import os
import unicodedata
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from . import crypto
from .auth import require_session
from .repository import (
    claim_ingest,
    create_job,
    create_recording,
    get_yandex_auth,
    set_job_dirs,
    set_recording_latest_job,
    set_yandex_token,
    update_ingest,
)

router = APIRouter()

AUDIO_EXT = {".wav", ".mp3", ".m4a", ".mp4", ".mov", ".ogg", ".opus", ".flac", ".webm", ".mkv", ".aac"}


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

    def listdir(self, path: str) -> List[dict]:
        out = []
        for r in self._y.listdir(path):
            out.append({
                "name": r.name,
                "path": r.path,
                "type": r.type,  # file | dir
                "size": getattr(r, "size", None),
                "md5": getattr(r, "md5", None),
                "revision": getattr(r, "revision", None),
                "resource_id": getattr(r, "resource_id", None),
            })
        return out

    def get_meta(self, path: str) -> dict:
        r = self._y.get_meta(path)
        return {"name": r.name, "path": r.path, "type": r.type,
                "revision": getattr(r, "revision", None),
                "resource_id": getattr(r, "resource_id", None)}

    def download(self, remote: str, local: str) -> None:
        self._y.download(remote, local)


def _default_factory(token: str) -> YaDiskClient:
    return YaDiskClient(token)


def _build_client(request: Request) -> Optional[object]:
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


@router.post("/api/yandex/pull")
def pull(payload: PullIn, request: Request, user: str = Depends(require_session)) -> dict:
    settings = request.app.state.settings
    watch_dir = os.getenv("DIALOGSCRIBE_YANDEX_WATCH_DIR", "/")
    if not _under_watch_dir(payload.path, watch_dir):
        raise HTTPException(403, "Путь вне разрешённой папки")
    client = _build_client(request)
    if client is None:
        raise HTTPException(400, "Токен Яндекс.Диска не настроен")

    # Определяем: папка с дорожками (Route A) или одиночный файл (single).
    try:
        meta = client.get_meta(payload.path)
    except Exception:
        raise HTTPException(404, "Путь не найден на Яндекс.Диске")

    if meta["type"] == "dir":
        entries = [e for e in client.listdir(payload.path)
                   if e["type"] == "file" and Path(e["name"]).suffix.lower() in AUDIO_EXT]
        if not entries:
            raise HTTPException(400, "В папке нет аудио-дорожек")
        kind = "route_a" if len(entries) > 1 else "single"
        revision = str(meta.get("revision") or max((e.get("revision") or 0) for e in entries))
    else:
        entries = [meta | {"name": meta["name"], "path": meta["path"]}]
        kind = "single"
        revision = str(meta.get("revision") or 0)

    ingest_key = f"{payload.path}:{revision}"
    surrogate = claim_ingest(settings.db_path, ingest_key, meta.get("resource_id"))
    if surrogate is None:  # уже подтягивали эту ревизию — дедуп, без второй джобы
        return {"status": "already_seen", "ingest_key_seen": True}

    remote_tracks = [{"name": _name(e["name"]), "remote": e["path"]} for e in entries]
    enqueue_io = getattr(request.app.state, "enqueue_io", None)
    if enqueue_io is not None:
        enqueue_io(surrogate, kind, remote_tracks)
    return {"status": "pulling", "surrogate_id": surrogate, "kind": kind}


def _name(filename: str) -> str:
    return unicodedata.normalize("NFC", Path(filename).stem) or "track"


# --------------------------------------------------------------------------- #
# Worker (io-очередь): скачать дорожки → создать запись/джобу → enqueue gpu
# --------------------------------------------------------------------------- #
def ingest_pull(settings, surrogate_id: str, kind: str, remote_tracks: list, client, enqueue_gpu) -> Optional[str]:
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

        rec_id = create_recording(db, origin="yandex", kind=kind, tracks=tracks,
                                  title=tracks[0]["name"] if tracks else None)
        params = {"glossary": True}
        if kind == "single":
            params["diarization"] = "pyannote" if os.getenv("HF_TOKEN") else "none"
        job_id = create_job(db, mode=kind, source="yandex", recording_id=rec_id, params=params)
        output_dir = Path(settings.data_dir) / "outputs" / job_id
        set_job_dirs(db, job_id, work_dir=str(work), output_dir=str(output_dir),
                     manifest_path=str(output_dir / "manifest.json"))
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
