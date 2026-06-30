"""Тесты Route A (инкремент 21) — discover/парсинг имён без ASR-модели."""

from gigaam_transcriber import GigaAMTranscriber


def test_discover_parses_and_canonicalizes(tmp_path):
    rec = tmp_path / "Audio Record"
    rec.mkdir()
    for fn in [
        "audioAlexPedan51378374725.m4a",
        "audioIvan21378374725.m4a",
        "audioPonimaiuAI11378374725.m4a",
    ]:
        (rec / fn).write_bytes(b"")
    tracks = GigaAMTranscriber.discover_route_a_tracks(tmp_path)
    # имена канонизированы через глоссарий people (config/glossary.json)
    assert "Алексей Педан" in tracks
    assert "Иван Крючков" in tracks
    assert "Павел Шаталов" in tracks
    assert all(p.endswith(".m4a") for p in tracks.values())


def test_discover_empty_folder(tmp_path):
    assert GigaAMTranscriber.discover_route_a_tracks(tmp_path) == {}
