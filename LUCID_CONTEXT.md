# Lucid — Project Context & Memory

> **Purpose of this file:** Complete handoff document for the Lucid project.
> Read this first if you (or any AI assistant in a future chat) need to pick
> up where we left off. Every design decision, why we made it, and what's
> next is captured here.

---

## 1. What is Lucid?

**Lucid** is a Chrome extension (with a Python backend) that helps users
understand long, dense technical white papers. It is the sibling of an
existing app called **Distill** (which converts transcriptions into summaries).

- **Distill** = spoken content → summary
- **Lucid** = written content (papers) → clarity

### Core features

1. **Summary + key points** — high-level overview with structured sections
2. **Section-by-section explanation** — plain-language rewrite of each section
3. **Q&A chat** — RAG-powered conversation with the paper, grounded in cited passages

### Project location

```
D:\Work\Inceptez_GenAI\Project\Lucid
```

### Naming etymology

"Lucid" — Latin *lucidus* = "full of light." Tamil equivalent: **தெளிவான**
(*theḷivāṉa*) = clear, transparent. Both languages share the metaphor:
clarity = letting light through.

---

## 2. Architecture (final design)

```
┌─────────────────────────────────────────────────────────────┐
│  Chrome Extension (TO BUILD LATER)                          │
│  • Side panel UI (Manifest V3)                              │
│  • Content script (detects PDFs on arXiv, IEEE, etc.)       │
│  • Background service worker                                │
└────────────────────┬────────────────────────────────────────┘
                     │ HTTP (CORS)
                     ▼
┌─────────────────────────────────────────────────────────────┐
│  FastAPI Backend (BUILT — Phase 1)                          │
│  http://localhost:8000                                      │
│                                                             │
│  Endpoints:                                                 │
│  • GET  /health                                             │
│  • POST /ingest         (multipart file upload)             │
│  • GET  /sections/{paper_id}                                │
│  • POST /summarize/{paper_id}                               │
│  • POST /explain        (body: paper_id, section_order)     │
│  • POST /ask            (body: paper_id, question)          │
└──────┬──────────────────────────────┬───────────────────────┘
       │                              │
       ▼                              ▼
┌──────────────────┐         ┌───────────────────────┐
│  ChromaDB        │         │  Ollama               │
│  (persistent)    │         │  localhost:11434      │
│                  │         │                       │
│  Chunks +        │  ◀──── │  • qwen2.5:14b (LLM)  │
│  embeddings      │  embed  │  • llama3.1:8b (alt)  │
│  + metadata      │         │  • nomic-embed-text   │
│  (page, section) │         │    (embeddings)       │
└──────────────────┘         └───────────────────────┘
```

### Why this design

- **Local-first.** No cloud, no API keys, no data leaves the machine.
- **RAG over stuff-everything-into-context.** Papers are 20–80 pages.
  Retrieval lets a 14B model handle any length while staying focused.
- **Separation of concerns.** Extension is a thin UI. All ML logic lives
  in the Python backend, where it's easier to test and iterate.
- **Chrome extension comes second.** Build and harden the backend first,
  then put a nice UI on top.

---

## 3. Tech stack & key choices

| Component | Choice | Why |
|---|---|---|
| LLM runtime | **Ollama** | Already used in Distill; simple HTTP API; local |
| Primary LLM | **qwen2.5:14b** (~9 GB) | Best balance for 24GB RAM; strong on technical content |
| Fast LLM | **llama3.1:8b** (~4.7 GB) | Fallback for speed; 128k context window |
| Embeddings | **nomic-embed-text** (~270 MB) | Compact, strong on technical text |
| Vector DB | **ChromaDB** (PersistentClient) | Local, in-process, persists to disk, metadata-aware |
| PDF parsing | **PyMuPDF (fitz)** | Best academic-paper layout handling |
| Backend framework | **FastAPI + Uvicorn** | Async, simple, OpenAPI docs free |
| Chunking | 800 words, 100-word overlap | ~1000 tokens; tested sweet spot for papers |
| Retrieval | **top-k=6** chunks | Enough cross-referencing without context overload |
| Q&A temperature | **0.2** | Factual answers, low hallucination |
| Summary temperature | **0.3** | Slight fluency room while staying grounded |

### Hardware target

- **24 GB RAM** (the user's machine) — comfortably runs qwen2.5:14b
  alongside ChromaDB, FastAPI, browser
- Python 3.13 in VS Code (note: may need to fall back to 3.11/3.12 if
  any wheel is missing — see Section 7)

---

## 4. Folder structure

```
D:\Work\Inceptez_GenAI\Project\Lucid\
├── LUCID_CONTEXT.md        ← THIS FILE (read first)
├── README.md               ← user-facing setup & usage
├── .gitignore
├── backend\
│   ├── main.py             ← FastAPI app, all endpoints
│   ├── requirements.txt
│   └── src\
│       ├── __init__.py
│       ├── llm.py          ← Ollama HTTP client (chat + embed)
│       ├── pdf_loader.py   ← PyMuPDF parsing + section detection
│       ├── vector_store.py ← ChromaDB wrapper
│       ├── summarizer.py   ← map-reduce summary + section explainer
│       └── qa.py           ← RAG retrieval + answer generation
├── data\
│   ├── uploads\            ← uploaded PDFs land here
│   └── chroma_db\          ← persistent vector store (created automatically)
└── extension\              ← TO BUILD in Phase 2 (Chrome extension)
```

---

## 5. How each module works

### `src/llm.py`
Thin wrapper around Ollama's REST API.
- `OllamaClient(base_url, chat_model, embed_model)` — main class
- `.ping()` — health check
- `.chat(prompt, system, temperature, num_ctx)` — non-streaming completion
- `.chat_stream(...)` — yields tokens as they arrive (for future SSE)
- `.embed(text)` / `.embed_batch(texts)` — embeddings

### `src/pdf_loader.py`
Parse PDF → `ParsedPaper(filename, num_pages, full_text, pages, sections)`.
- Uses PyMuPDF (`fitz`) per-page extraction
- Cleans hyphenated line breaks and excessive whitespace
- Detects sections via two signals:
  1. Exact match to common section names (`abstract`, `introduction`, etc.)
  2. Numbered/Roman-numeral headings in Title Case
- `chunk_text(text, chunk_size=800, overlap=100)` — word-based chunking

### `src/vector_store.py`
ChromaDB persistent collection per paper.
- Collection name = sanitized filename or paper_id
- Embeddings generated via Ollama and passed in explicitly (Chroma's
  built-in embedding function is bypassed)
- Cosine similarity (`hnsw:space=cosine`)
- Metadata stored per chunk: `source`, `page`, `chunk_index`, `section`
- Stable hash-based IDs so re-ingestion upserts rather than duplicates

### `src/summarizer.py`
**Map-reduce summary** for arbitrary-length papers:
1. MAP: chunk full text (1500 words, 150 overlap), summarize each as bullets
2. REDUCE: combine partial summaries into structured final output
   (Overview / Key Contributions / Approach / Results / Limitations)

**Section explainer:**
- If section < 2500 words: explain directly
- If longer: summarize chunks first, then explain the summary

### `src/qa.py`
RAG pipeline:
1. Embed user question
2. Query ChromaDB for top-k chunks (default 6)
3. Format chunks with page/section labels
4. Prompt LLM with strict "only use context, cite pages" system message
5. Return `{answer, sources}` — sources let the UI show citations

### `main.py`
FastAPI wiring. CORS open for dev. Caches `ParsedPaper` objects in
memory by `paper_id` (so we don't reparse on every call). Vector store
is reconstructed per request (cheap; ChromaDB is persistent).

---

## 6. API contract (for the future Chrome extension)

### Health check
```
GET /health
→ { ollama_reachable, available_models, papers_loaded }
```

### Ingest a paper
```
POST /ingest
Content-Type: multipart/form-data
Body: file=<pdf>
→ { paper_id, filename, num_pages, num_sections, num_chunks_indexed }
```

### List sections
```
GET /sections/{paper_id}
→ { paper_id, sections: [{order, title, start_page, end_page}, ...] }
```

### Get summary
```
POST /summarize/{paper_id}?model=qwen2.5:14b
→ { paper_id, summary }
```

### Explain a section
```
POST /explain
Body: { paper_id, section_order, model? }
→ { paper_id, section_title, explanation }
```

### Ask a question
```
POST /ask
Body: { paper_id, question, top_k?, model? }
→ { paper_id, question, answer, sources: [{page, section, text, distance}, ...] }
```

---

## 7. Setup steps (run these in VS Code terminal)

### Prerequisites
- Ollama installed and running (`ollama serve` or it auto-starts as a service)
- Python 3.10+ (3.11 or 3.12 safest; 3.13 might need fallback)
- Models pulled:
  ```
  ollama pull qwen2.5:14b
  ollama pull llama3.1:8b
  ollama pull nomic-embed-text
  ```

### One-time setup
```powershell
cd D:\Work\Inceptez_GenAI\Project\Lucid
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
```

### If Python 3.13 fails on chromadb install
ChromaDB depends on packages (like onnxruntime) that may lag behind
brand-new Python releases. If install fails:
```powershell
# Install Python 3.11 or 3.12 alongside 3.13
# Then create venv with that specific Python
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
```

### Run the backend
```powershell
# From project root (D:\Work\Inceptez_GenAI\Project\Lucid)
uvicorn backend.main:app --reload --port 8000
```

Then visit `http://localhost:8000/docs` for the auto-generated Swagger UI —
you can test every endpoint from the browser without writing a client.

---

## 8. Testing checklist (Phase 1 acceptance)

Run through these in order. If any fails, fix before moving on.

- [ ] `GET http://localhost:8000/health` → `ollama_reachable: true`,
      and `available_models` contains qwen2.5:14b + nomic-embed-text
- [ ] `POST /ingest` with a real white paper PDF (try arXiv) →
      returns valid `paper_id`, `num_pages > 0`, `num_chunks_indexed > 0`
- [ ] `GET /sections/{paper_id}` → returns at least a few detected sections
      (Abstract, Introduction, etc.)
- [ ] `POST /summarize/{paper_id}` → returns structured summary with
      ## Overview, ## Key Contributions, etc. headers. Takes 30–90 sec
      depending on paper length.
- [ ] `POST /explain` with section_order=0 → returns plain-language
      explanation of the first section
- [ ] `POST /ask` with a real question → returns answer with (p. N)
      citations and a `sources` array

### Useful curl tests (PowerShell-compatible)

```powershell
# Health
curl http://localhost:8000/health

# Ingest
curl.exe -X POST "http://localhost:8000/ingest" -F "file=@C:\path\to\paper.pdf"

# Ask (replace PAPER_ID)
curl.exe -X POST "http://localhost:8000/ask" `
  -H "Content-Type: application/json" `
  -d "{\"paper_id\": \"PAPER_ID\", \"question\": \"What problem does this paper solve?\"}"
```

---

## 9. Known issues / things to watch

1. **Ollama embedding is one-at-a-time.** Ingestion of a 50-page paper
   takes ~30–60 seconds because of this. Acceptable for now.
2. **Section detection is heuristic.** Some papers use unusual heading
   styles (e.g., bolded but not capitalized). Falls back to "Full Paper"
   if nothing is detected — Q&A still works fine, only the section tab
   is affected.
3. **Service worker sleep (future Chrome extension issue).** Manifest V3
   service workers sleep after ~30s idle. Don't store state in module
   variables — use `chrome.storage` instead.
4. **In-memory `PAPERS` dict resets on backend restart.** Vector store
   persists, but the parsed Section objects don't. For Phase 2, persist
   `ParsedPaper` metadata to a small SQLite DB or JSON sidecar.
5. **No streaming yet.** `chat_stream` exists in `llm.py` but isn't
   wired into FastAPI endpoints. Phase 2 enhancement: add SSE endpoints
   for streaming summaries and answers to the UI.

---

## 10. Next phases

### Phase 1 (CURRENT) — Backend ✅
All files written. Goal: prove backend works end-to-end via curl/Swagger.

### Phase 2 — Chrome extension
- `manifest.json` (V3) with `sidePanel`, `activeTab`, `storage`,
  `host_permissions: ["http://localhost:8000/*"]`
- `sidepanel.html/js` — three-tab UI (Summary / Sections / Chat)
- `content.js` — detect PDF URLs on arXiv/IEEE/etc., send to backend
- `background.js` — service worker mediating between sidepanel and content script
- Auto-detect PDF in current tab and offer one-click ingest

### Phase 3 — Polish
- Streaming responses via SSE (FastAPI → fetch with ReadableStream)
- Persist paper metadata to disk so backend restarts don't lose state
- Source citation rendering: clickable "(p. 4)" jumps to that page in the PDF
- Model selector in UI (qwen vs llama)
- Optional: native messaging so users don't manually start the backend

### Phase 4 — Distribution (optional)
- Package backend with PyInstaller into a single .exe
- Publish extension to Chrome Web Store ($5 one-time fee)
- Privacy policy: "all data stays local, never transmitted"

---

## 11. Resuming this project in a new chat

If you start a fresh Claude chat later, paste this into your first message:

> I'm continuing the **Lucid** project — a Chrome extension + FastAPI
> backend for understanding white papers using Ollama + ChromaDB RAG.
> The project is at `D:\Work\Inceptez_GenAI\Project\Lucid`. Read
> `LUCID_CONTEXT.md` in the project root for full context. I'm currently
> on Phase [N]. The specific issue I need help with is: [describe].

That's all the context any future assistant needs.

---

*Last updated: Phase 1 complete — all backend files generated and ready to run.*
