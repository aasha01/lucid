# Changelog

All notable changes to Lucid are documented here, in reverse chronological order.

| Version | Date | Summary |
|---|---|---|
| 0.4.0 | 2026-05-24 | Streaming chat Q&A — tokens appear as generated instead of waiting for full response |
| 0.3.0 | 2026-05-24 | GROBID PDF parsing, disk cache, sections accordion UI, paper registry |
| 0.2.0 | 2026-05-23 | Section-aware chunking, ExplainTab, streaming SSE, dark UI overhaul |
| 0.1.0 | 2026-05-17 | Initial release — FastAPI + React, LanceDB RAG, Ollama integration |

---

## [0.4.0] — 2026-05-24

### feat: Streaming chat Q&A

#### Backend — new SSE endpoint (`backend/src/qa.py`, `backend/main.py`)
- Added `answer_question_stream()` to `qa.py` — same RAG pipeline as `answer_question()` but uses `ollama.chat_stream()` to yield tokens as they arrive from the LLM
- Tokens are emitted as `{"type": "token", "text": "..."}` SSE events
- Final `{"type": "done", "sources": [...]}` event carries the retrieved source chunks so the frontend can render citations after generation completes
- Added `POST /ask/stream` endpoint in `main.py` that wraps `answer_question_stream()` in a `StreamingResponse`; errors are caught and emitted as `{"type": "error", "message": "..."}` events
- Old `POST /ask` (non-streaming) endpoint retained for compatibility

#### Frontend — streaming chat UI (`frontend/src/components/ChatTab.tsx`, `frontend/src/streamSSE.ts`)
- `ChatTab` rewritten to use `streamSSE("/api/ask/stream", ...)` instead of `api.ask()`
- An empty assistant message placeholder is added immediately when Send is clicked; the typing-dots animation shows while waiting for the first token
- Tokens accumulate into the message bubble in real time; a blinking cursor is shown while generation is in progress
- `sources` arrive in the `done` event and are attached to the message for the collapsible citations panel
- `SSEEvent` interface in `streamSSE.ts` gains a `sources` field to type the done-event payload

#### Why this matters
- Previously the chat was a blocking HTTP request: the UI froze for 30–90 seconds waiting for `qwen2.5:14b` to finish generating the full answer before anything was returned
- Now the first token appears within ~1 second and the answer streams in word by word, matching the feel of the section explanation and summary features

---

## [0.3.0] — 2026-05-24

### feat: GROBID parsing, disk cache, sections accordion UI

#### PDF Parsing — GROBID integration (`backend/src/pdf_loader.py`)
- Replaced PyMuPDF bold-text heuristics with GROBID REST API (`http://localhost:8070`) for accurate section detection in research papers
- GROBID converts PDFs to TEI XML with true section hierarchy — subsections like `3.2.1` are now correctly identified at level 3, not misclassified as top-level headings
- Fixed the core bug where bold inline paragraph text (e.g. "**Note:** this assumes…") was incorrectly detected as a section heading
- `Section` dataclass gains three new fields: `level` (1/2/3), `section_number` ("3.2.1"), `parent_title`
- `ParsedPaper` dataclass gains `title` and `abstract` fields populated from the TEI header
- `_assign_hierarchy()` derives level and parent from GROBID's `<head n="…">` attribute so flat TEI output is correctly nested
- PyMuPDF kept as automatic fallback — if GROBID is unreachable the existing font-size heuristic path runs transparently
- Added `lxml>=5.0.0` to `requirements.txt` for TEI XML parsing
- `GROBID_URL` is configurable via environment variable (defaults to `http://localhost:8070`; Docker Compose can set it to `http://grobid:8070`)

#### Vector Store — section-based chunking (`backend/src/vector_store.py`)
- Changed ingestion from per-page chunking to per-section chunking — GROBID gives clean section text directly, making page-level chunking redundant
- Chunk metadata now includes `section_level`, `section_number`, and `parent_section` for richer retrieval context
- `_stable_id` updated to use section order instead of page number
- Removed `_build_page_section_map` (no longer needed)

#### Disk Cache (`backend/src/cache.py` — new file)
- `PaperCache` class: one JSON file per paper at `data/cache/{paper_id}.json` storing:
  - `"paper"` — full serialised `ParsedPaper` (sections, text, title, abstract)
  - `"summary"` — AI-generated paper summary
  - `"sections"` — per-section AI explanations keyed by section order
  - `"num_chunks_indexed"` — chunk count from last ingest
- All four AI endpoints check cache before calling the LLM; cache hit returns instantly
- Streaming endpoints accumulate tokens and persist to cache on stream completion
- Cache hit sends `{"type": "done", "cached": true}` so the frontend can show a badge
- `PaperCache.get_paper()` / `set_paper()` serialise `ParsedPaper` via `dataclasses.asdict()` for fast server restart recovery
- `update_registry` / `load_registry`: persistent `data/registry.json` indexes all ever-ingested papers with filename, title, section count, page count, chunk count
- Startup migration `_backfill_registry()` scans existing `data/cache/` files and `data/uploads/` folder to populate the registry on first run — no re-upload needed for previously ingested papers

#### Backend API (`backend/main.py`)
- `GET /papers` — lists all ever-ingested papers from registry; survives server restarts
- `POST /papers/{id}/load` — loads a paper into memory on demand; re-parses from saved upload file via GROBID if not already in memory (used by the "Open" button in the UI)
- `SectionInfo` Pydantic model now includes `level` and `section_number`
- `PaperListItem` Pydantic model added
- `@app.on_event("startup")` restores all `ParsedPaper` objects from cache and backfills registry
- `.gitignore` updated to exclude `data/cache/` and `data/registry.json`

#### Frontend — Home page (`frontend/src/App.tsx`)
- "Previously ingested papers" list shown on home page on load via `GET /papers`
- Each row shows paper title, section count, page count, chunk count
- "Open" button calls `POST /papers/{id}/load` with a "Loading…" disabled state while re-parsing
- List refreshes after each new ingest

#### Frontend — Sections UI (`frontend/src/components/SectionsTab.tsx`)
- Replaced 3-panel layout (headings list + sticky pills + section blocks) with a single clean accordion
- Each section is one row; clicking it expands the AI explanation inline below
- Subsections indented 20 px per level so hierarchy (`3.2`, `3.2.1`) is visually clear
- Clicking an already-open section collapses it (toggle behaviour)
- `⚡ cached` green badge shown in section header when explanation is served from cache
- `section_number` and `level` fields from backend now used in the UI

#### Frontend — Other (`frontend/src/`)
- `SummaryTab`: `⚡ cached` badge shown next to Regenerate button on cache hit; `fromCache` state resets on regenerate
- `PaperPanel`: removed Quick Summary panel (SummaryTab component preserved for future use)
- `streamSSE.ts`: added `cached?: boolean` to `SSEEvent` interface
- `api.ts`: added `PaperListItem` interface, `listPapers()`, `loadPaper()` functions; `SectionInfo` gains `level` and `section_number`
- `styles.css`: new accordion styles (`.sections-accordion`, `.accordion-item`, `.accordion-header`, `.accordion-body`); new recent-papers list styles; new `.cache-badge` style; removed old headings-list, sticky-pills, and section-block styles

---

## [0.2.0] — 2026-05-23

### Chunking by Sections and token count

#### PDF Parsing (`backend/src/pdf_loader.py`)
- Rewrote section detection with three-tier strategy: font-size analysis → author-page cluster filtering → text-pattern fallback
- `_headings_by_font()`: detects headings by font size (≥ 1.12× median body text) or bold flag; merges lines sharing the same block index to reconstruct compound headings split by PyMuPDF
- `_filter_author_page_clusters()`: removes false positives from title/author pages (first 20% of document with 4+ heading candidates)
- `_headings_by_text()`: regex fallback for PDFs with no font metadata
- `_is_noise_line()`: filters page numbers, figure/table captions, email addresses, author affiliation lines
- `chunk_text()`: word-count sliding window chunker with configurable size (default 800 words) and overlap (default 100 words)

#### Summarizer & Explainer (`backend/src/summarizer.py`, `backend/src/explainer.py`)
- `summarize_paper()` / `summarize_paper_stream()`: single-call fast summary using a smart excerpt (headings-first, per-section word budget)
- `explain_section()` / `explain_section_stream()`: plain-language rewrite of a single section; map-reduces via chunk summaries for sections over 2500 words
- `_build_excerpt()`: builds a representative excerpt walking sections in order, trimming at sentence boundaries, capped at 3500 total words
- SSE streaming for both summary and explanation with `map_start`, `map_chunk_start`, `token`, `reduce_start`, `done` event types

#### Deep Explanation (`backend/src/explainer.py` — new file)
- `explain_paper_stream()`: 8-section structured breakdown using a Jinja2 template
- `backend/prompts/explain_paper.j2`: Jinja2 template for structured paper explanation
- `backend/src/prompt_manager.py`: template loader utility

#### Frontend
- `ExplainTab.tsx` (new): streaming 8-section deep explanation panel with milestone progress events
- `SummaryTab.tsx`: full rewrite with map→reduce progress bar, log entries, streaming token accumulation
- `SectionsTab.tsx`: section list with per-section explain-on-click, SSE streaming, progress badges
- `PaperPanel.tsx`: two-column workspace layout (left content + right chat panel)
- `streamSSE.ts`: custom POST-based SSE parser (EventSource only supports GET); supports `milestone` event type
- `styles.css`: dark theme design system with CSS variables, two-column workspace, section pills, summary progress bar

---

## [0.1.0] — 2026-05-17

### Initial commit: Lucid — white paper understanding tool

#### Core architecture
- FastAPI backend (`backend/main.py`) with endpoints: `GET /health`, `POST /ingest`, `GET /sections/{id}`, `POST /summarize/{id}`, `POST /explain`, `POST /ask`
- React + TypeScript frontend (Vite, port 5173) proxying to backend at port 8000
- LanceDB vector store replacing an earlier ChromaDB prototype — embedded, no server required, pure Python wheels
- Ollama HTTP client (`backend/src/llm.py`) wrapping chat completion and embedding endpoints; default models `qwen2.5:14b` (chat) and `nomic-embed-text` (embeddings)

#### PDF processing (`backend/src/pdf_loader.py`)
- PyMuPDF (`fitz`) text extraction with per-page font metadata collection
- Basic section detection using font-size threshold and bold flags
- `Page` and `Section` dataclasses; `ParsedPaper` as the unified output type

#### RAG pipeline (`backend/src/vector_store.py`, `backend/src/qa.py`)
- `VectorStore.ingest_paper()`: chunk each page → embed via Ollama → store in LanceDB
- `VectorStore.query()`: cosine similarity search returning top-k chunks with page and section metadata
- `answer_question()`: retrieves relevant chunks, grounds LLM answer in source text with page citations

#### Frontend foundation (`frontend/src/`)
- `App.tsx`: health polling, model selector, paper state management
- `Uploader.tsx`: drag-and-drop PDF upload
- `ChatTab.tsx`: Q&A interface with source citations
- `HealthBadge.tsx`: Ollama connectivity indicator
- `api.ts`: typed fetch wrappers for all backend endpoints
- Dark theme CSS design system with accent gradient (purple → cyan)
