"""
Summarization and section explanation for Lucid.

Two strategies:
1. summarize_paper: map-reduce. Summarize each chunk, then summarize summaries.
   Works regardless of paper length.
2. explain_section: single-shot rewrite of a specific section in plain language.
"""
from __future__ import annotations

import json
from typing import Generator

from .llm import OllamaClient
from .pdf_loader import ParsedPaper, Section, chunk_text


# ---------- Prompts ----------

CHUNK_SUMMARY_SYSTEM = (
    "You are a research assistant summarizing parts of an academic white paper. "
    "Be concise, factual, and faithful to the source. Do not invent information."
)

CHUNK_SUMMARY_PROMPT = """Summarize the following excerpt from a white paper in 3-5 bullet points.
Capture the key claims, methods, or findings. Stay strictly within what the text says.

EXCERPT:
{chunk}

SUMMARY (bullets):"""


REDUCE_SUMMARY_SYSTEM = (
    "You are a research assistant producing the final summary of an academic white paper "
    "from partial summaries of its sections. Be clear, structured, and faithful."
)

REDUCE_SUMMARY_PROMPT = """Below are bullet-point summaries of consecutive parts of a white paper.
Combine them into a single coherent summary of the whole paper.

Format your output as:

## Overview
2-3 sentence high-level description of what the paper is about.

## Key Contributions
- 3-5 bullets covering the main contributions or claims.

## Approach / Method
- 2-4 bullets on how the authors did it.

## Results / Findings
- 2-4 bullets on what they found.

## Limitations or Open Questions
- 1-3 bullets, only if mentioned in the source.

PARTIAL SUMMARIES:
{partial_summaries}

FINAL SUMMARY:"""


EXPLAIN_SECTION_SYSTEM = (
    "You are an expert tutor who explains technical white-paper sections in plain language "
    "for someone with general technical background but not a domain specialist. "
    "Stay faithful to the source. Define jargon. Use short paragraphs."
)

EXPLAIN_SECTION_PROMPT = """Explain the following section of a white paper in plain language.

Guidelines:
- Lead with a 1-2 sentence "what this section is about" overview.
- Then walk through the key ideas in order, defining any jargon.
- If equations or methods appear, explain what they do, not just what they are.
- End with "Why this matters" — 1-2 sentences on the section's role in the paper.
- Use clear, conversational language. Avoid academic stiffness.

SECTION TITLE: {title}

SECTION TEXT:
{text}

PLAIN-LANGUAGE EXPLANATION:"""


# ---------- Functions ----------


def summarize_paper(
    paper: ParsedPaper,
    ollama: OllamaClient,
    map_chunk_size: int = 1500,
    map_overlap: int = 150,
    model: str | None = None,
) -> str:
    """Map-reduce summary of the entire paper."""
    # Step 1 (MAP): break the full text into largish chunks and summarize each
    chunks = chunk_text(paper.full_text, chunk_size=map_chunk_size, overlap=map_overlap)
    if not chunks:
        return "(Empty paper — nothing to summarize.)"

    partial_summaries: list[str] = []
    for i, chunk in enumerate(chunks):
        prompt = CHUNK_SUMMARY_PROMPT.format(chunk=chunk)
        summary = ollama.chat(
            prompt=prompt,
            system=CHUNK_SUMMARY_SYSTEM,
            model=model,
            temperature=0.2,
        )
        partial_summaries.append(f"[Part {i+1}]\n{summary.strip()}")

    # Step 2 (REDUCE): combine partials into a structured final summary
    combined = "\n\n".join(partial_summaries)
    final_prompt = REDUCE_SUMMARY_PROMPT.format(partial_summaries=combined)
    final = ollama.chat(
        prompt=final_prompt,
        system=REDUCE_SUMMARY_SYSTEM,
        model=model,
        temperature=0.3,
        # Reduce step may need bigger context window
        num_ctx=16384,
    )
    return final.strip()


def explain_section(
    section: Section,
    ollama: OllamaClient,
    model: str | None = None,
) -> str:
    """Plain-language rewrite of a single section."""
    # If section is very long, summarize chunks first then explain the summary
    text = section.text
    word_count = len(text.split())
    if word_count > 2500:
        # Summarize first to fit in context
        partial_chunks = chunk_text(text, chunk_size=1500, overlap=150)
        partials = []
        for chunk in partial_chunks:
            s = ollama.chat(
                prompt=CHUNK_SUMMARY_PROMPT.format(chunk=chunk),
                system=CHUNK_SUMMARY_SYSTEM,
                model=model,
                temperature=0.2,
            )
            partials.append(s)
        text = "\n\n".join(partials)

    prompt = EXPLAIN_SECTION_PROMPT.format(title=section.title, text=text)
    return ollama.chat(
        prompt=prompt,
        system=EXPLAIN_SECTION_SYSTEM,
        model=model,
        temperature=0.4,
        num_ctx=8192,
    ).strip()


# ---------- Streaming versions (SSE) ----------


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def summarize_paper_stream(
    paper: ParsedPaper,
    ollama: OllamaClient,
    map_chunk_size: int = 1500,
    map_overlap: int = 150,
    model: str | None = None,
) -> Generator[str, None, None]:
    """Yields SSE-formatted strings for the map-reduce summary pipeline.

    Phase 1 (MAP): each chunk streams its bullet summary live so the user
    sees text immediately. Accumulated text is cleared when the reduce starts.
    Phase 2 (REDUCE): final structured summary streams token-by-token.
    """
    chunks = chunk_text(paper.full_text, chunk_size=map_chunk_size, overlap=map_overlap)
    if not chunks:
        yield _sse({"type": "token", "text": "(Empty paper — nothing to summarize.)"})
        yield _sse({"type": "done"})
        return

    total = len(chunks)
    partial_summaries: list[str] = []

    yield _sse({"type": "map_start", "total": total})

    # MAP: stream each chunk summary so text appears immediately
    for i, chunk in enumerate(chunks):
        yield _sse({"type": "map_chunk_start", "chunk": i + 1, "total": total})
        chunk_tokens: list[str] = []
        for token in ollama.chat_stream(
            prompt=CHUNK_SUMMARY_PROMPT.format(chunk=chunk),
            system=CHUNK_SUMMARY_SYSTEM,
            model=model,
            temperature=0.2,
        ):
            yield _sse({"type": "token", "text": token})
            chunk_tokens.append(token)
        partial_summaries.append(f"[Part {i + 1}]\n{''.join(chunk_tokens).strip()}")

    # REDUCE: clear the map output and stream the final structured summary
    yield _sse({"type": "reduce_start"})
    combined = "\n\n".join(partial_summaries)
    final_prompt = REDUCE_SUMMARY_PROMPT.format(partial_summaries=combined)
    for token in ollama.chat_stream(
        prompt=final_prompt,
        system=REDUCE_SUMMARY_SYSTEM,
        model=model,
        temperature=0.3,
        num_ctx=16384,
    ):
        yield _sse({"type": "token", "text": token})

    yield _sse({"type": "done"})


def explain_section_stream(
    section: Section,
    ollama: OllamaClient,
    model: str | None = None,
) -> Generator[str, None, None]:
    """Yields SSE-formatted strings for section explanation."""
    text = section.text
    word_count = len(text.split())

    if word_count > 2500:
        partial_chunks = chunk_text(text, chunk_size=1500, overlap=150)
        total = len(partial_chunks)
        partials: list[str] = []
        for i, chunk in enumerate(partial_chunks):
            yield _sse({"type": "progress", "message": f"Processing part {i + 1} of {total}…"})
            s = ollama.chat(
                prompt=CHUNK_SUMMARY_PROMPT.format(chunk=chunk),
                system=CHUNK_SUMMARY_SYSTEM,
                model=model,
                temperature=0.2,
            )
            partials.append(s)
        text = "\n\n".join(partials)

    yield _sse({"type": "progress", "message": "Generating explanation…"})
    prompt = EXPLAIN_SECTION_PROMPT.format(title=section.title, text=text)
    for token in ollama.chat_stream(
        prompt=prompt,
        system=EXPLAIN_SECTION_SYSTEM,
        model=model,
        temperature=0.4,
        num_ctx=8192,
    ):
        yield _sse({"type": "token", "text": token})

    yield _sse({"type": "done"})
