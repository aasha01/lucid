"""
Lucid FastAPI backend.

Exposes endpoints the Chrome extension (or any client) calls to:
- Upload/ingest a PDF
- Get a summary
- List sections
- Explain a section
- Ask a question (RAG)

Run from project root:
    uvicorn backend.main:app --reload --port 8000

CORS is open for chrome-extension://* and localhost, which is fine for local dev.
Tighten before any public deployment.
"""
from __future__ import annotations

import shutil
import uuid
from datetime import datetime


def log(msg: str) -> None:
    """Print a timestamped log line that always appears in the uvicorn terminal."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] LUCID  {msg}", flush=True)
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.src.llm import OllamaClient
from backend.src.pdf_loader import ParsedPaper, extract_text_from_pdf
from backend.src.qa import answer_question
from backend.src.explainer import explain_paper_stream
from backend.src.summarizer import (
    explain_section,
    explain_section_stream,
    summarize_paper,
    summarize_paper_stream,
)
from backend.src.vector_store import VectorStore
from backend.src.cache import PaperCache, load_all_papers, load_registry, update_registry


# ---------- Config ----------
PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPLOAD_DIR = PROJECT_ROOT / "data" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ---------- App ----------
app = FastAPI(title="Lucid", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # dev only; lock down later
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- In-memory paper registry ----------
# paper_id -> ParsedPaper (populated at startup from disk cache, then kept live)
PAPERS: dict[str, ParsedPaper] = {}


@app.on_event("startup")
def _restore_papers() -> None:
    # Load full ParsedPaper objects from cache files that have the "paper" key
    restored = load_all_papers()
    PAPERS.update(restored)
    log(f"Restored {len(restored)} paper(s) from cache")

    # Backfill registry for any paper not yet registered
    _backfill_registry(restored)


def _backfill_registry(loaded: dict) -> None:
    """Populate registry.json from existing cache files and upload folder."""
    import lancedb as _lancedb

    registry = load_registry()
    added = 0

    # Papers with full cache — register with complete metadata
    for paper_id, paper in loaded.items():
        if paper_id not in registry:
            chunks = 0
            try:
                db = _lancedb.connect(str(PROJECT_ROOT / "data" / "lancedb"))
                chunks = db.open_table(paper_id).count_rows()
            except Exception:
                chunks = PaperCache(paper_id).get_chunks_count()
            update_registry(
                paper_id=paper_id,
                filename=paper.filename,
                title=paper.title or paper.filename,
                num_pages=paper.num_pages,
                num_sections=len(paper.sections),
                num_chunks_indexed=chunks,
            )
            added += 1

    # Uploads without any cache — register with filename only (parsed on Open)
    for pdf_path in UPLOAD_DIR.glob("*__*.pdf"):
        parts = pdf_path.stem.split("__", 1)
        if len(parts) != 2:
            continue
        paper_id, name_stem = parts
        if paper_id in registry or paper_id in loaded:
            continue
        chunks = 0
        try:
            db = _lancedb.connect(str(PROJECT_ROOT / "data" / "lancedb"))
            chunks = db.open_table(paper_id).count_rows()
        except Exception:
            pass
        update_registry(
            paper_id=paper_id,
            filename=name_stem + ".pdf",
            title=name_stem.replace("-", " ").replace("_", " "),
            num_pages=0,
            num_sections=0,
            num_chunks_indexed=chunks,
        )
        added += 1

    if added:
        log(f"Registry backfilled: {added} new entries added")


# ---------- Pydantic models ----------
class IngestResponse(BaseModel):
    paper_id: str
    filename: str
    num_pages: int
    num_sections: int
    num_chunks_indexed: int


class SectionInfo(BaseModel):
    order: int
    title: str
    start_page: int
    end_page: int
    text: str
    level: int = 1
    section_number: str = ""


class SectionsResponse(BaseModel):
    paper_id: str
    sections: list[SectionInfo]


class SummaryResponse(BaseModel):
    paper_id: str
    summary: str


class ExplainRequest(BaseModel):
    paper_id: str
    section_order: int
    model: Optional[str] = None


class ExplainResponse(BaseModel):
    paper_id: str
    section_title: str
    explanation: str


class AskRequest(BaseModel):
    paper_id: str
    question: str
    top_k: int = 6
    model: Optional[str] = None


class SourceInfo(BaseModel):
    page: Optional[int] = None
    section: Optional[str] = None
    text: str
    distance: Optional[float] = None


class AskResponse(BaseModel):
    paper_id: str
    question: str
    answer: str
    sources: list[SourceInfo]


class PaperListItem(BaseModel):
    paper_id: str
    filename: str
    num_pages: int
    num_sections: int
    num_chunks_indexed: int
    title: str


class HealthResponse(BaseModel):
    ollama_reachable: bool
    available_models: list[str]
    papers_loaded: int


# ---------- Helpers ----------
def _get_paper(paper_id: str) -> ParsedPaper:
    if paper_id not in PAPERS:
        raise HTTPException(status_code=404, detail="paper_id not found. Ingest the PDF first.")
    return PAPERS[paper_id]


def _make_vector_store(paper_id: str, ollama: OllamaClient) -> VectorStore:
    return VectorStore(collection_name=paper_id, ollama=ollama)


# ---------- Endpoints ----------


@app.get("/health", response_model=HealthResponse)
def health():
    ollama = OllamaClient()
    reachable = ollama.ping()
    models = ollama.list_models() if reachable else []
    log(f"GET /health — ollama={reachable}, models={models}, papers_loaded={len(PAPERS)}")
    return HealthResponse(
        ollama_reachable=reachable,
        available_models=models,
        papers_loaded=len(PAPERS),
    )


@app.get("/papers", response_model=list[PaperListItem])
def list_papers():
    """Return metadata for all ever-ingested papers from the registry."""
    registry = load_registry()
    items = [
        PaperListItem(paper_id=pid, **meta)
        for pid, meta in registry.items()
    ]
    return sorted(items, key=lambda x: x.filename)


@app.post("/papers/{paper_id}/load", response_model=IngestResponse)
def load_paper(paper_id: str):
    """Load a previously ingested paper into memory (re-parses from upload if needed)."""
    if paper_id in PAPERS:
        paper = PAPERS[paper_id]
        return IngestResponse(
            paper_id=paper_id,
            filename=paper.filename,
            num_pages=paper.num_pages,
            num_sections=len(paper.sections),
            num_chunks_indexed=PaperCache(paper_id).get_chunks_count(),
        )
    matches = list(UPLOAD_DIR.glob(f"{paper_id}__*.pdf"))
    if not matches:
        raise HTTPException(status_code=404, detail="Upload file not found. Please re-upload the PDF.")
    try:
        log(f"[{paper_id}] Re-parsing from upload…")
        paper = extract_text_from_pdf(matches[0])
        PAPERS[paper_id] = paper
        PaperCache(paper_id).set_paper(paper)
        log(f"[{paper_id}] Re-parsed: {len(paper.sections)} sections")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Re-parse failed: {e}")
    return IngestResponse(
        paper_id=paper_id,
        filename=paper.filename,
        num_pages=paper.num_pages,
        num_sections=len(paper.sections),
        num_chunks_indexed=PaperCache(paper_id).get_chunks_count(),
    )


@app.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)):
    """Upload a PDF, parse it, embed all chunks, store in ChromaDB."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    paper_id = uuid.uuid4().hex[:12]
    safe_name = Path(file.filename).name
    saved_path = UPLOAD_DIR / f"{paper_id}__{safe_name}"
    log(f"POST /ingest — file={safe_name}, paper_id={paper_id}")

    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    log(f"[{paper_id}] Saved to {saved_path}")

    try:
        paper = extract_text_from_pdf(saved_path)
        log(f"[{paper_id}] Parsed: {paper.num_pages} pages, {len(paper.sections)} sections detected")
        for s in paper.sections:
            log(f"[{paper_id}]   § {s.order+1:02d}  {s.title!r:40s}  p.{s.start_page}–{s.end_page}")
    except Exception as e:
        log(f"[{paper_id}] PDF parsing failed: {e}")
        raise HTTPException(status_code=500, detail=f"PDF parsing failed: {e}")

    ollama = OllamaClient()
    if not ollama.ping():
        raise HTTPException(status_code=503, detail="Ollama not reachable on localhost:11434.")

    store = _make_vector_store(paper_id, ollama)
    store.reset()
    try:
        log(f"[{paper_id}] Embedding chunks…")
        n_chunks = store.ingest_paper(paper)
        log(f"[{paper_id}] Indexed {n_chunks} chunks → LanceDB")
    except Exception as e:
        log(f"[{paper_id}] Embedding/storage failed: {e}")
        raise HTTPException(status_code=500, detail=f"Embedding/storage failed: {e}")

    PAPERS[paper_id] = paper
    pc = PaperCache(paper_id)
    pc.set_paper(paper)
    pc.set_chunks_count(n_chunks)
    update_registry(
        paper_id=paper_id,
        filename=paper.filename,
        title=paper.title or paper.filename,
        num_pages=paper.num_pages,
        num_sections=len(paper.sections),
        num_chunks_indexed=n_chunks,
    )
    log(f"[{paper_id}] Ingest complete ✓")

    return IngestResponse(
        paper_id=paper_id,
        filename=paper.filename,
        num_pages=paper.num_pages,
        num_sections=len(paper.sections),
        num_chunks_indexed=n_chunks,
    )


@app.get("/sections/{paper_id}", response_model=SectionsResponse)
def get_sections(paper_id: str):
    paper = _get_paper(paper_id)
    return SectionsResponse(
        paper_id=paper_id,
        sections=[
            SectionInfo(
                order=s.order,
                title=s.title,
                start_page=s.start_page,
                end_page=s.end_page,
                text=s.text,
                level=s.level,
                section_number=s.section_number,
            )
            for s in paper.sections
        ],
    )


@app.post("/summarize/{paper_id}", response_model=SummaryResponse)
def post_summarize(paper_id: str, model: Optional[str] = None):
    paper = _get_paper(paper_id)
    cache = PaperCache(paper_id)
    cached = cache.get_summary()
    if cached:
        log(f"[{paper_id}] summary cache hit")
        return SummaryResponse(paper_id=paper_id, summary=cached)
    ollama = OllamaClient()
    try:
        summary = summarize_paper(paper, ollama, model=model)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summarization failed: {e}")
    cache.set_summary(summary)
    return SummaryResponse(paper_id=paper_id, summary=summary)


_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",   # disables nginx / proxy buffering
    "Connection": "keep-alive",
}


@app.post("/explain-paper/{paper_id}/stream")
def post_explain_paper_stream(paper_id: str, model: Optional[str] = None):
    """Streaming 8-section deep explanation using the Jinja2 template."""
    paper = _get_paper(paper_id)
    ollama = OllamaClient()

    log(f"[{paper_id}] POST /explain-paper/stream — model={model or 'default'}")

    def _generate():
        try:
            yield from explain_paper_stream(paper, ollama, model=model)
        except Exception as e:
            import json as _json
            log(f"[{paper_id}] explain_paper_stream error: {e}")
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/summarize/{paper_id}/stream")
def post_summarize_stream(paper_id: str, model: Optional[str] = None):
    """Streaming SSE version of summarize. Yields progress + tokens."""
    import json as _json
    paper = _get_paper(paper_id)
    cache = PaperCache(paper_id)
    log(f"[{paper_id}] POST /summarize/stream — model={model or 'default'}")

    cached = cache.get_summary()
    if cached:
        log(f"[{paper_id}] summary cache hit")
        def _from_cache():
            yield f"data: {_json.dumps({'type': 'map_start', 'total': 1})}\n\n"
            yield f"data: {_json.dumps({'type': 'reduce_start'})}\n\n"
            yield f"data: {_json.dumps({'type': 'token', 'text': cached})}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'cached': True})}\n\n"
        return StreamingResponse(_from_cache(), media_type="text/event-stream", headers=_SSE_HEADERS)

    ollama = OllamaClient()
    def _generate():
        tokens: list[str] = []
        try:
            for event in summarize_paper_stream(paper, ollama, model=model):
                yield event
                try:
                    data = _json.loads(event.removeprefix("data: ").strip())
                    if data.get("type") == "token":
                        tokens.append(data["text"])
                except Exception:
                    pass
        except Exception as e:
            log(f"[{paper_id}] summarize_paper_stream error: {e}")
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if tokens:
                cache.set_summary("".join(tokens))

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/explain/stream")
def post_explain_stream(req: ExplainRequest):
    """Streaming SSE version of explain. Yields progress + tokens."""
    import json as _json
    paper = _get_paper(req.paper_id)
    if req.section_order < 0 or req.section_order >= len(paper.sections):
        raise HTTPException(status_code=400, detail="section_order out of range")
    section = paper.sections[req.section_order]
    cache = PaperCache(req.paper_id)

    cached = cache.get_explanation(req.section_order)
    if cached:
        log(f"[{req.paper_id}] explain cache hit — section {req.section_order} '{section.title}'")
        def _from_cache():
            yield f"data: {_json.dumps({'type': 'progress', 'message': 'Loading cached explanation…'})}\n\n"
            yield f"data: {_json.dumps({'type': 'token', 'text': cached})}\n\n"
            yield f"data: {_json.dumps({'type': 'done', 'cached': True})}\n\n"
        return StreamingResponse(_from_cache(), media_type="text/event-stream", headers=_SSE_HEADERS)

    ollama = OllamaClient()
    def _generate():
        tokens: list[str] = []
        try:
            for event in explain_section_stream(section, ollama, model=req.model):
                yield event
                try:
                    data = _json.loads(event.removeprefix("data: ").strip())
                    if data.get("type") == "token":
                        tokens.append(data["text"])
                except Exception:
                    pass
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        finally:
            if tokens:
                cache.set_explanation(req.section_order, section.title, "".join(tokens))

    return StreamingResponse(_generate(), media_type="text/event-stream", headers=_SSE_HEADERS)


@app.post("/explain", response_model=ExplainResponse)
def post_explain(req: ExplainRequest):
    paper = _get_paper(req.paper_id)
    if req.section_order < 0 or req.section_order >= len(paper.sections):
        raise HTTPException(status_code=400, detail="section_order out of range")
    section = paper.sections[req.section_order]
    cache = PaperCache(req.paper_id)

    cached = cache.get_explanation(req.section_order)
    if cached:
        log(f"[{req.paper_id}] explain cache hit — section {req.section_order} '{section.title}'")
        return ExplainResponse(
            paper_id=req.paper_id,
            section_title=section.title,
            explanation=cached,
        )

    ollama = OllamaClient()
    try:
        explanation = explain_section(section, ollama, model=req.model)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Explanation failed: {e}")
    cache.set_explanation(req.section_order, section.title, explanation)
    return ExplainResponse(
        paper_id=req.paper_id,
        section_title=section.title,
        explanation=explanation,
    )


@app.post("/ask", response_model=AskResponse)
def post_ask(req: AskRequest):
    _get_paper(req.paper_id)  # validate paper_id
    ollama = OllamaClient()
    store = _make_vector_store(req.paper_id, ollama)
    try:
        result = answer_question(
            question=req.question,
            store=store,
            ollama=ollama,
            top_k=req.top_k,
            model=req.model,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Q&A failed: {e}")
    return AskResponse(
        paper_id=req.paper_id,
        question=req.question,
        answer=result["answer"],
        sources=[SourceInfo(**s) for s in result["sources"]],
    )


@app.get("/")
def root():
    return {
        "name": "Lucid",
        "version": "0.1.0",
        "description": "Local RAG backend for understanding white papers.",
        "endpoints": [
            "GET /health",
            "POST /ingest (multipart: file)",
            "GET /sections/{paper_id}",
            "POST /summarize/{paper_id}",
            "POST /explain",
            "POST /ask",
        ],
    }
