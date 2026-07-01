"""M6.2 — retention prune: uploads TTL 7д, outputs TTL 30д (время инъектируется)."""

import os

from gigaam_transcriber.server.config import Settings
from gigaam_transcriber.server.retention import (
    OUTPUTS_TTL,
    UPLOADS_TTL,
    prune_retention,
)


def _settings(tmp_path):
    return Settings(data_dir=tmp_path, session_key="x")


def _make_aged(path, age_seconds, now):
    path.mkdir(parents=True, exist_ok=True)
    f = path / "a.bin"
    f.write_bytes(b"\x00")
    mtime = now - age_seconds
    os.utime(path, (mtime, mtime))
    os.utime(f, (mtime, mtime))


def test_prune_removes_old_uploads_keeps_fresh(tmp_path):
    s = _settings(tmp_path)
    now = 1_000_000_000.0
    up = tmp_path / "uploads"
    _make_aged(up / "old", UPLOADS_TTL + 100, now)
    _make_aged(up / "fresh", 100, now)

    stats = prune_retention(s, now=now)
    assert stats["uploads"] == 1
    assert not (up / "old").exists()
    assert (up / "fresh").exists()


def test_prune_outputs_uses_longer_ttl(tmp_path):
    s = _settings(tmp_path)
    now = 1_000_000_000.0
    out = tmp_path / "outputs"
    # 10 дней — старше uploads-TTL (7д), но младше outputs-TTL (30д) → НЕ трогаем
    _make_aged(out / "job1", 10 * 24 * 3600, now)
    _make_aged(out / "ancient", OUTPUTS_TTL + 100, now)

    stats = prune_retention(s, now=now)
    assert stats["outputs"] == 1
    assert (out / "job1").exists()
    assert not (out / "ancient").exists()


def test_prune_missing_dirs_safe(tmp_path):
    stats = prune_retention(_settings(tmp_path), now=1_000_000_000.0)
    assert stats == {"uploads": 0, "outputs": 0, "work": 0}
