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
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from backend.src.llm import OllamaClient
from backend.src.pdf_loader import ParsedPaper, extract_text_from_pdf
from backend.src.qa import answer_question
from backend.src.summarizer import (
    explain_section,
    explain_section_stream,
    summarize_paper,
    summarize_paper_stream,
)
from backend.src.vector_store import VectorStore


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
# paper_id -> ParsedPaper (cached so we don't reparse on every request)
PAPERS: dict[str, ParsedPaper] = {}


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
    return HealthResponse(
        ollama_reachable=reachable,
        available_models=models,
        papers_loaded=len(PAPERS),
    )


@app.post("/ingest", response_model=IngestResponse)
async def ingest(file: UploadFile = File(...)):
    """Upload a PDF, parse it, embed all chunks, store in ChromaDB."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    # Save upload
    paper_id = uuid.uuid4().hex[:12]
    safe_name = Path(file.filename).name
    saved_path = UPLOAD_DIR / f"{paper_id}__{safe_name}"
    with saved_path.open("wb") as f:
        shutil.copyfileobj(file.file, f)

    # Parse
    try:
        paper = extract_text_from_pdf(saved_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF parsing failed: {e}")

    # Embed + store
    ollama = OllamaClient()
    if not ollama.ping():
        raise HTTPException(
            status_code=503,
            detail="Ollama is not reachable on localhost:11434. Start Ollama and try again.",
        )
    store = _make_vector_store(paper_id, ollama)
    store.reset()  # fresh collection for a fresh paper
    try:
        n_chunks = store.ingest_paper(paper)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Embedding/storage failed: {e}")

    PAPERS[paper_id] = paper

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
            )
            for s in paper.sections
        ],
    )


@app.post("/summarize/{paper_id}", response_model=SummaryResponse)
def post_summarize(paper_id: str, model: Optional[str] = None):
    paper = _get_paper(paper_id)
    ollama = OllamaClient()
    try:
        summary = summarize_paper(paper, ollama, model=model)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summarization failed: {e}")
    return SummaryResponse(paper_id=paper_id, summary=summary)


@app.post("/summarize/{paper_id}/stream")
def post_summarize_stream(paper_id: str, model: Optional[str] = None):
    """Streaming SSE version of summarize. Yields progress + tokens."""
    paper = _get_paper(paper_id)
    ollama = OllamaClient()

    def _generate():
        try:
            yield from summarize_paper_stream(paper, ollama, model=model)
        except Exception as e:
            import json
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@app.post("/explain/stream")
def post_explain_stream(req: ExplainRequest):
    """Streaming SSE version of explain. Yields progress + tokens."""
    paper = _get_paper(req.paper_id)
    if req.section_order < 0 or req.section_order >= len(paper.sections):
        raise HTTPException(status_code=400, detail="section_order out of range")
    section = paper.sections[req.section_order]
    ollama = OllamaClient()

    def _generate():
        try:
            yield from explain_section_stream(section, ollama, model=req.model)
        except Exception as e:
            import json
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


@app.post("/explain", response_model=ExplainResponse)
def post_explain(req: ExplainRequest):
    paper = _get_paper(req.paper_id)
    if req.section_order < 0 or req.section_order >= len(paper.sections):
        raise HTTPException(status_code=400, detail="section_order out of range")
    section = paper.sections[req.section_order]
    ollama = OllamaClient()
    try:
        explanation = explain_section(section, ollama, model=req.model)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Explanation failed: {e}")
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
