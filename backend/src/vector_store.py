"""
LanceDB vector store wrapper for Lucid.

Drop-in replacement for the ChromaDB implementation.
Public API is identical: VectorStore(collection_name, ollama), .reset(),
.ingest_paper(paper), .query(question, top_k), .count().

LanceDB stores tables as directories inside data/lancedb/ — no C++ build
required, pure Python wheels on all platforms.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from pathlib import Path

import lancedb


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] STORE  {msg}", flush=True)

from .llm import OllamaClient
from .pdf_loader import ParsedPaper, chunk_text


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "lancedb"


class VectorStore:
    """LanceDB-backed vector store for a single paper."""

    def __init__(
        self,
        collection_name: str,
        ollama: OllamaClient,
        db_path: str | Path = DEFAULT_DB_PATH,
    ) -> None:
        self.collection_name = _sanitize_name(collection_name)
        self.ollama = ollama
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(self.db_path))

    # ---------- Ingestion ----------

    def reset(self) -> None:
        """Drop the table so a fresh ingest starts clean."""
        try:
            self.db.drop_table(self.collection_name)
        except Exception:
            pass

    def ingest_paper(
        self, paper: ParsedPaper, chunk_size: int = 800, overlap: int = 100
    ) -> int:
        """Chunk every section, embed, and store in a LanceDB table.
        Returns the number of chunks stored."""
        texts: list[str] = []
        metas: list[dict] = []

        for section in paper.sections:
            for chunk_idx, chunk in enumerate(
                chunk_text(section.text, chunk_size=chunk_size, overlap=overlap)
            ):
                if not chunk.strip():
                    continue
                texts.append(chunk)
                metas.append(
                    {
                        "id": _stable_id(paper.filename, section.order, chunk_idx, chunk),
                        "source": paper.filename,
                        "page": section.start_page,
                        "chunk_index": chunk_idx,
                        "section": section.title,
                        "section_level": section.level,
                        "section_number": section.section_number,
                        "parent_section": section.parent_title,
                    }
                )

        if not texts:
            return 0

        total = len(texts)
        _log(f"Embedding {total} chunks via Ollama…")
        embeddings: list[list[float]] = []
        for i, text in enumerate(texts, start=1):
            if i == 1 or i % 10 == 0 or i == total:
                _log(f"  chunk {i}/{total}")
            embeddings.append(self.ollama.embed(text))

        _log(f"All {total} chunks embedded — writing to LanceDB…")
        records = [
            {**meta, "text": text, "vector": emb}
            for meta, text, emb in zip(metas, texts, embeddings)
        ]

        self.db.create_table(self.collection_name, data=records, mode="overwrite")
        _log(f"Stored {len(records)} chunks → '{self.collection_name}'")
        return len(records)

    # ---------- Retrieval ----------

    def query(self, question: str, top_k: int = 6) -> list[dict]:
        """Return the top_k most relevant chunks for a question."""
        try:
            tbl = self.db.open_table(self.collection_name)
        except Exception:
            return []

        query_emb = self.ollama.embed(question)
        results = (
            tbl.search(query_emb, vector_column_name="vector")
            .metric("cosine")
            .limit(top_k)
            .to_list()
        )
        return [
            {
                "text": r["text"],
                "page": r["page"],
                "section": r["section"],
                "section_level": r.get("section_level", 1),
                "section_number": r.get("section_number", ""),
                "parent_section": r.get("parent_section", ""),
                "source": r.get("source"),
                "distance": r.get("_distance"),
            }
            for r in results
        ]

    def count(self) -> int:
        try:
            return self.db.open_table(self.collection_name).count_rows()
        except Exception:
            return 0


# ---------- Helpers ----------

def _sanitize_name(name: str) -> str:
    """LanceDB table names: alphanumeric, hyphen, underscore, max 63 chars."""
    name = Path(name).stem
    name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    name = name.strip("-_")
    if not name:
        name = "table"
    if len(name) > 63:
        suffix = hashlib.md5(name.encode()).hexdigest()[:8]
        name = name[:54] + "_" + suffix
    return name


def _stable_id(filename: str, section_order: int, chunk_idx: int, text: str) -> str:
    h = hashlib.md5(text.encode()).hexdigest()[:8]
    return f"{Path(filename).stem}_s{section_order}_c{chunk_idx}_{h}"
