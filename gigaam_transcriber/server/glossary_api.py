"""Glossary view/edit (M6, спека §M6): чтение/правка канонизации имён и терминов.

Редактирует тот же `config_dir()/glossary.json`, который читает рантайм-канонизация
(`load_runtime`). PUT прогоняет lint (страж I1): term-алиас, совпадающий с настоящим
русским/английским словом, отклоняется — иначе текст-замена ломает вывод модели.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from .auth import require_session

router = APIRouter()


class GlossaryIn(BaseModel):
    people: dict[str, str] = {}
    terms: dict[str, str] = {}


def _glossary_path() -> Path:
    from gigaam_transcriber._paths import config_dir

    return config_dir() / "glossary.json"


@router.get("/api/glossary")
def get_glossary(request: Request, user: str = Depends(require_session)) -> dict:
    from gigaam_transcriber.glossary import load_glossary

    g = load_glossary(_glossary_path())
    return {"people": g.get("people", {}), "terms": g.get("terms", {})}


@router.put("/api/glossary")
def put_glossary(
    payload: GlossaryIn, request: Request, user: str = Depends(require_session)
) -> dict:
    from gigaam_transcriber.glossary import lint, load_en_words, load_ru_words

    glossary = {"people": payload.people, "terms": payload.terms}
    blocked = lint(glossary, load_ru_words(), load_en_words())
    if blocked:
        raise HTTPException(
            400, f"Term-алиасы совпадают с настоящими словами (нарушение I1): {', '.join(blocked)}"
        )
    path = _glossary_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(glossary, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return {"people": payload.people, "terms": payload.terms}
