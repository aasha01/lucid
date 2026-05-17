# Lucid 💡

> Local, private white-paper analyzer. Upload a PDF, get a summary,
> understand each section in plain language, and chat with the paper.

Sibling app to **Distill** (transcription → summary).

---

## Quick start

### 1. Install Ollama and pull models

Install Ollama from [ollama.com](https://ollama.com), then:

```powershell
ollama pull qwen2.5:14b
ollama pull llama3.1:8b
ollama pull nomic-embed-text
```

### 2. Set up Python environment

From the project root (`D:\Work\Inceptez_GenAI\Project\Lucid`):

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r backend\requirements.txt
```

> If Python 3.13 fails on the install (ChromaDB or its dependencies),
> try Python 3.11 or 3.12: `py -3.11 -m venv .venv`

### 3. Run the backend

```powershell
uvicorn backend.main:app --reload --port 8000
```

Open `http://localhost:8000/docs` to test all endpoints from the
Swagger UI. No client needed.

---

## What you can do right now (Phase 1)

- **Upload a PDF** via `POST /ingest`
- **List sections** via `GET /sections/{paper_id}`
- **Get a structured summary** via `POST /summarize/{paper_id}`
- **Explain any section** in plain language via `POST /explain`
- **Ask anything** about the paper via `POST /ask` (RAG with cited pages)

The Chrome extension UI is **Phase 2** — coming next.

---

## Architecture

Chrome Extension (Phase 2) ↔ **FastAPI** (this code) ↔ **Ollama** + **ChromaDB**

All local. No cloud calls. No API keys.

For full design notes, see [`LUCID_CONTEXT.md`](./LUCID_CONTEXT.md).

---

## Project layout

```
backend/
├── main.py             # FastAPI app
├── requirements.txt
└── src/
    ├── llm.py          # Ollama HTTP client
    ├── pdf_loader.py   # PyMuPDF + section detection
    ├── vector_store.py # ChromaDB wrapper
    ├── summarizer.py   # Map-reduce summary
    └── qa.py           # RAG Q&A
data/
├── uploads/            # PDFs land here
└── chroma_db/          # vector store (auto-created)
```
