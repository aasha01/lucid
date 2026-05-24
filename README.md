# Lucid

> Understand long, dense research papers — locally, privately, without any cloud services.

Upload a PDF, get a structured summary, read plain-language explanations of every section, and chat with the paper using natural language. All processing runs on your own machine.

---

## Features

| Feature | Description |
|---|---|
| **Smart section extraction** | GROBID parses research PDFs into a true section hierarchy (section → subsection → sub-subsection) |
| **Plain-language explanations** | Click any section to get an AI explanation in plain English, streamed in real time |
| **Paper Q&A** | Ask anything about the paper; answers are grounded in the source text with citations |
| **Disk cache** | Explanations and summaries are cached — revisiting a section loads instantly |
| **Paper registry** | Previously ingested papers reappear on the home screen after server restart |
| **Fully local** | Ollama (LLM + embeddings) + LanceDB (vector store) + GROBID (PDF parsing) — no API keys, no data leaving your machine |

---

## Stack

```
Frontend          React + TypeScript (Vite)  — port 5173
Backend           FastAPI (Python)            — port 8000
LLM               Ollama — qwen2.5:14b
Embeddings        Ollama — nomic-embed-text
Vector store      LanceDB (embedded, no server)
PDF parsing       GROBID (Docker)             — port 8070
```

---

## Quick start

### 1. Install Ollama and pull models

Install Ollama from [ollama.com](https://ollama.com), then pull the required models:

```powershell
ollama pull qwen2.5:14b
ollama pull nomic-embed-text
```

### 2. Start GROBID (Docker)

GROBID extracts structured sections from research PDFs. Run it as a persistent named container:

```powershell
docker run -d -p 8070:8070 --name grobid lfoppiano/grobid:0.8.1
```

Verify it is running: open `http://localhost:8070` in a browser — you should see the GROBID web UI.

> GROBID only needs to run during PDF ingestion. Once a paper is ingested and cached, GROBID is not needed for explanations or Q&A.

### 3. Set up the Python environment

From the project root:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
```

> Requires Python 3.12. If you have multiple versions installed, use `py -3.12 -m venv .venv`.

### 4. Start the backend

```powershell
uvicorn backend.main:app --reload --port 8000
```

On first startup the backend scans existing uploads and cache files, building a registry of all previously ingested papers. You will see log output like:

```
LUCID  Restored 4 paper(s) from cache
LUCID  Registry backfilled: 39 new entries added
```

### 5. Start the frontend

In a second terminal:

```powershell
cd frontend
npm install       # first time only
npm run dev
```

Open `http://localhost:5173`.

---

## How it works

### Ingestion (one-time per paper)

```
PDF
 └─ GROBID → TEI XML
              └─ Parse sections (title, level, section_number, text)
                  └─ Chunk each section (800 words, 100-word overlap)
                      └─ Embed chunks → nomic-embed-text
                          └─ Store in LanceDB
                          └─ Save ParsedPaper to disk cache
                          └─ Register in data/registry.json
```

### Section explanation (on demand, cached)

```
User clicks section
 └─ Check data/cache/{paper_id}.json
     ├─ Cache hit  → stream cached text instantly  [⚡ cached]
     └─ Cache miss → call qwen2.5:14b → stream tokens → save to cache
```

### Q&A (RAG)

```
User asks a question
 └─ Embed question → nomic-embed-text
     └─ Search LanceDB → top 6 semantically similar chunks
         └─ Send chunks + question to qwen2.5:14b
             └─ Answer with page and section citations
```

---

## Project layout

```
backend/
├── main.py                 # FastAPI app — all endpoints
├── requirements.txt
├── prompts/
│   └── explain_paper.j2    # Jinja2 template for deep explanation
└── src/
    ├── cache.py            # Disk cache + paper registry
    ├── explainer.py        # 8-section deep explanation
    ├── llm.py              # Ollama HTTP client (chat + embeddings)
    ├── pdf_loader.py       # GROBID integration + PyMuPDF fallback
    ├── prompt_manager.py   # Jinja2 template loader
    ├── qa.py               # RAG Q&A
    ├── summarizer.py       # Paper summary + section explanation
    └── vector_store.py     # LanceDB wrapper

frontend/
└── src/
    ├── App.tsx             # Root — health, model selector, paper registry
    ├── api.ts              # Typed fetch wrappers for all backend endpoints
    ├── streamSSE.ts        # POST-based SSE stream parser
    ├── styles.css          # Dark theme design system
    └── components/
        ├── ChatTab.tsx     # Q&A with source citations
        ├── ExplainTab.tsx  # 8-section deep breakdown
        ├── HealthBadge.tsx # Ollama connectivity indicator
        ├── PaperPanel.tsx  # Main paper workspace layout
        ├── SectionsTab.tsx # Section accordion with inline explanations
        ├── SummaryTab.tsx  # Structured paper summary
        └── Uploader.tsx    # Drag-and-drop PDF upload

data/                       # Runtime data (git-ignored)
├── uploads/                # Original PDFs saved on ingest
├── lancedb/                # Vector embeddings (binary)
├── cache/                  # Parsed papers + AI explanations (JSON)
└── registry.json           # Index of all ingested papers

docs/
└── vector_db_concepts.md   # Explanation of Vector DB, embeddings, RAG
```

---

## API endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Ollama reachability + available models |
| `GET` | `/papers` | List all previously ingested papers |
| `POST` | `/papers/{id}/load` | Load a paper into memory (re-parses if needed) |
| `POST` | `/ingest` | Upload and process a PDF |
| `GET` | `/sections/{id}` | Get detected sections with hierarchy |
| `POST` | `/summarize/{id}/stream` | Stream a structured paper summary |
| `POST` | `/explain/stream` | Stream a plain-language section explanation |
| `POST` | `/explain-paper/{id}/stream` | Stream an 8-section deep breakdown |
| `POST` | `/ask` | Ask a question (RAG with citations) |

Interactive API docs available at `http://localhost:8000/docs`.

---

## VS Code extensions

| Extension | Purpose |
|---|---|
| Docker (`ms-azuretools.vscode-docker`) | Manage the GROBID container from the sidebar |
| REST Client (`humao.rest-client`) | Test API endpoints from `.http` files |
| XML (`redhat.vscode-xml`) | Syntax highlighting for GROBID TEI XML output |

---

## Notes

- **GROBID fallback**: if GROBID is unreachable at ingest time, the backend falls back to PyMuPDF font-size heuristics. Section quality will be lower for complex papers but the app remains functional.
- **Model swap**: the chat model can be changed per-request from the model selector in the UI header. Any model pulled in Ollama is available.
- **Re-ingestion**: uploading the same PDF again creates a new `paper_id` and fresh embeddings. The old paper remains in the registry.
