"""
Disk-based cache for parsed papers and AI-generated content.

Each paper gets one JSON file at data/cache/{paper_id}.json:
{
  "paper": { "filename": "...", "sections": [...], ... },
  "summary": "## Overview ...",
  "sections": {
    "0": {"title": "Abstract",  "explanation": "..."},
    "3": {"title": "Methods",   "explanation": "..."}
  }
}

The "paper" key lets the server restore ParsedPaper objects on startup without
re-parsing or re-calling GROBID, so previously ingested papers survive restarts.
Only sections the user has actually viewed are stored under "sections".
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .pdf_loader import ParsedPaper

_CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache"


class PaperCache:
    def __init__(self, paper_id: str) -> None:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._path = _CACHE_DIR / f"{paper_id}.json"
        self._data: dict = self._load()

    def _load(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"paper": None, "summary": None, "sections": {}}

    def _save(self) -> None:
        self._path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # ── Parsed paper (for restart restoration) ────────────────────────────────

    def set_paper(self, paper: "ParsedPaper") -> None:
        self._data["paper"] = dataclasses.asdict(paper)
        self._save()

    def get_paper(self) -> Optional["ParsedPaper"]:
        raw = self._data.get("paper")
        if not raw:
            return None
        from .pdf_loader import ParsedPaper, Page, Section
        try:
            return ParsedPaper(
                filename=raw["filename"],
                num_pages=raw["num_pages"],
                full_text=raw["full_text"],
                title=raw.get("title", ""),
                abstract=raw.get("abstract", ""),
                pages=[Page(**p) for p in raw.get("pages", [])],
                sections=[Section(**s) for s in raw.get("sections", [])],
            )
        except Exception:
            return None

    # ── Summary ───────────────────────────────────────────────────────────────

    def get_summary(self) -> Optional[str]:
        return self._data.get("summary") or None

    def set_summary(self, text: str) -> None:
        self._data["summary"] = text
        self._save()

    # ── Section explanations ──────────────────────────────────────────────────

    def get_explanation(self, section_order: int) -> Optional[str]:
        entry = self._data.get("sections", {}).get(str(section_order))
        return entry.get("explanation") if entry else None

    def set_explanation(self, section_order: int, title: str, explanation: str) -> None:
        self._data.setdefault("sections", {})[str(section_order)] = {
            "title": title,
            "explanation": explanation,
        }
        self._save()

    # ── Chunk count ───────────────────────────────────────────────────────────

    def get_chunks_count(self) -> int:
        return self._data.get("num_chunks_indexed", 0)

    def set_chunks_count(self, n: int) -> None:
        self._data["num_chunks_indexed"] = n
        self._save()


# ── Registry (persistent metadata index) ─────────────────────────────────────

_REGISTRY_PATH = _CACHE_DIR.parent / "registry.json"


def update_registry(
    paper_id: str,
    filename: str,
    title: str,
    num_pages: int,
    num_sections: int,
    num_chunks_indexed: int,
) -> None:
    """Upsert one entry in the registry file."""
    registry = _load_registry_raw()
    registry[paper_id] = {
        "filename": filename,
        "title": title,
        "num_pages": num_pages,
        "num_sections": num_sections,
        "num_chunks_indexed": num_chunks_indexed,
    }
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def load_registry() -> dict[str, dict]:
    return _load_registry_raw()


def _load_registry_raw() -> dict:
    if not _REGISTRY_PATH.exists():
        return {}
    try:
        return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_all_papers() -> dict[str, "ParsedPaper"]:
    """Scan data/cache/ and restore all previously ingested ParsedPaper objects."""
    from .pdf_loader import ParsedPaper  # noqa: F401
    papers: dict[str, ParsedPaper] = {}
    if not _CACHE_DIR.exists():
        return papers
    for path in _CACHE_DIR.glob("*.json"):
        paper_id = path.stem
        paper = PaperCache(paper_id).get_paper()
        if paper is not None:
            papers[paper_id] = paper
    return papers
