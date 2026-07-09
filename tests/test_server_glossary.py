"""M6 — glossary view/edit: чтение/запись config/glossary.json + lint-страж I1."""

import json

import pytest
from fastapi.testclient import TestClient

from gigaam_transcriber.server.app import create_app
from tests.conftest import login_client, server_settings


@pytest.fixture
def client(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    # словарь настоящих слов для lint-стража I1
    (cfg / "russian_words.txt").write_text("привет\nспасибо\n", encoding="utf-8")
    monkeypatch.setenv("GIGAAM_TRANSCRIBER_CONFIG", str(cfg))
    c = login_client(create_app(server_settings(tmp_path)))
    c._cfg = cfg
    return c


def test_get_empty_glossary(client):
    assert client.get("/api/glossary").json() == {"people": {}, "terms": {}}


def test_put_and_get_roundtrip(client):
    body = {"people": {"дмитрий в": "Дмитрий Власов"}, "terms": {"кубер": "Kubernetes"}}
    r = client.put("/api/glossary", json=body)
    assert r.status_code == 200, r.text
    # сохранено в config/glossary.json
    saved = json.loads((client._cfg / "glossary.json").read_text(encoding="utf-8"))
    assert saved["people"]["дмитрий в"] == "Дмитрий Власов"
    assert client.get("/api/glossary").json()["terms"]["кубер"] == "Kubernetes"


def test_put_preserves_extra_top_level_keys(client):
    # PUT правит только people/terms: version/_README и будущие секции файла
    # не выбрасываются (регрессия: UI-сохранение молча теряло служебные поля).
    path = client._cfg / "glossary.json"
    path.write_text(
        json.dumps({"_README": ["как править"], "version": 3, "people": {}, "terms": {}}),
        encoding="utf-8",
    )
    r = client.put("/api/glossary", json={"people": {"оля": "Ольга"}, "terms": {}})
    assert r.status_code == 200, r.text
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["version"] == 3 and saved["_README"] == ["как править"]
    assert saved["people"] == {"оля": "Ольга"}


def test_put_rejects_real_word_term_alias(client):
    # term-алиас, совпадающий с настоящим словом → lint блокирует (I1)
    r = client.put("/api/glossary", json={"people": {}, "terms": {"привет": "Hello"}})
    assert r.status_code == 400
    assert "I1" in r.text or "слов" in r.text


def test_glossary_requires_auth(tmp_path, monkeypatch):
    monkeypatch.setenv("GIGAAM_TRANSCRIBER_CONFIG", str(tmp_path / "c"))
    c = TestClient(create_app(server_settings(tmp_path)))  # без логина → 401
    assert c.get("/api/glossary").status_code == 401
