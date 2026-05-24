"""
RAG Q&A engine for Lucid.

Given a question and a paper's VectorStore:
1. Retrieve the top-k most semantically relevant chunks.
2. Build a prompt that grounds the LLM strictly in those chunks.
3. Generate an answer that cites page numbers and section names.
"""
from __future__ import annotations

import json as _json
from typing import Generator

from .llm import OllamaClient
from .vector_store import VectorStore


QA_SYSTEM = (
    "You are a precise research assistant answering questions about an academic white paper. "
    "You must answer ONLY using the provided context excerpts. "
    "If the context does not contain enough information to answer, say so explicitly — "
    "do not guess and do not use outside knowledge. "
    "When you make a claim, cite the supporting page(s) inline like (p. 4) or (pp. 4-5). "
    "Be concise but thorough. Define jargon when it first appears."
)


QA_PROMPT = """A user has asked a question about a white paper. Use ONLY the context excerpts below.

QUESTION: {question}

CONTEXT EXCERPTS (each is a passage from the paper, with its page number):

{context}

Instructions:
- Answer the question using only the excerpts above.
- Cite supporting page numbers inline, e.g. (p. 3) or (pp. 5-6).
- If the excerpts do not answer the question, say: "The paper doesn't appear to address this directly based on the retrieved passages."
- Do not invent numbers, citations, or claims that aren't in the excerpts.

ANSWER:"""


def answer_question(
    question: str,
    store: VectorStore,
    ollama: OllamaClient,
    top_k: int = 6,
    model: str | None = None,
) -> dict:
    """Run RAG: retrieve relevant chunks and generate an answer.

    Returns a dict:
        {
          "answer": str,
          "sources": [ {page, section, text, distance}, ... ]
        }
    """
    hits = store.query(question, top_k=top_k)
    if not hits:
        return {
            "answer": "No content has been indexed yet for this paper.",
            "sources": [],
        }

    context_block = _format_context(hits)
    prompt = QA_PROMPT.format(question=question, context=context_block)
    answer = ollama.chat(
        prompt=prompt,
        system=QA_SYSTEM,
        model=model,
        temperature=0.2,
        num_ctx=8192,
    )

    return {
        "answer": answer.strip(),
        "sources": hits,
    }


def answer_question_stream(
    question: str,
    store: VectorStore,
    ollama: OllamaClient,
    top_k: int = 6,
    model: str | None = None,
) -> Generator[str, None, None]:
    """RAG Q&A with streaming. Yields SSE-formatted strings (tokens then a done event with sources)."""
    hits = store.query(question, top_k=top_k)
    if not hits:
        yield f"data: {_json.dumps({'type': 'token', 'text': 'No content has been indexed yet for this paper.'})}\n\n"
        yield f"data: {_json.dumps({'type': 'done', 'sources': []})}\n\n"
        return

    context_block = _format_context(hits)
    prompt = QA_PROMPT.format(question=question, context=context_block)

    for chunk in ollama.chat_stream(
        prompt=prompt,
        system=QA_SYSTEM,
        model=model,
        temperature=0.2,
        num_ctx=8192,
    ):
        yield f"data: {_json.dumps({'type': 'token', 'text': chunk})}\n\n"

    sources = [
        {
            "page": h.get("page"),
            "section": h.get("section"),
            "text": h.get("text", ""),
            "distance": h.get("distance"),
        }
        for h in hits
    ]
    yield f"data: {_json.dumps({'type': 'done', 'sources': sources})}\n\n"


def _format_context(hits: list[dict]) -> str:
    """Pretty-format retrieved chunks for the LLM prompt."""
    parts: list[str] = []
    for i, h in enumerate(hits, start=1):
        page = h.get("page", "?")
        section = h.get("section", "Unknown")
        text = h.get("text", "").strip()
        parts.append(
            f"--- Excerpt {i} | Page {page} | Section: {section} ---\n{text}"
        )
    return "\n\n".join(parts)
