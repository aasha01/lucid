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


FAST_SUMMARY_SYSTEM = (
    "You are a research assistant producing a structured summary of an academic white paper. "
    "Be clear, concise, and faithful to the source text. Do not invent information."
)

FAST_SUMMARY_PROMPT = """Summarize the following white paper excerpt into a structured report.

Format your output exactly as:

## Overview
2-3 sentence description of what this paper is about and why it matters.

## Key Contributions
- 3-5 bullets on the main contributions or claims.

## Approach / Method
- 2-4 bullets on how the authors did it (architecture, algorithm, dataset, etc.).

## Results / Findings
- 2-4 bullets on key results or findings.

## Limitations or Open Questions
- 1-3 bullets (only if mentioned in the text).

PAPER EXCERPT:
{excerpt}

STRUCTURED SUMMARY:"""


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
    model: str | None = None,
    **_kwargs,
) -> str:
    """Single-call summary using a smart excerpt of the paper."""
    excerpt = _build_excerpt(paper)
    prompt = FAST_SUMMARY_PROMPT.format(excerpt=excerpt)
    return ollama.chat(
        prompt=prompt,
        system=FAST_SUMMARY_SYSTEM,
        model=model,
        temperature=0.3,
        num_ctx=8192,
    ).strip()


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


# ---------- Helpers ----------

_SUMMARY_SECTION_LIMIT = 2200   # per-section word cap (~3000 tokens)
_SUMMARY_TOTAL_BUDGET = 3500    # total words for the summary prompt


def _trim_at_sentence(text: str, max_words: int) -> str:
    """Trim to max_words at the nearest sentence boundary (. ? !)."""
    words = text.split()
    if len(words) <= max_words:
        return text
    candidate = " ".join(words[:max_words])
    for marker in (". ", "? ", "! ", ".\n", "?\n", "!\n"):
        pos = candidate.rfind(marker)
        if pos > len(candidate) * 0.6:
            return candidate[: pos + 1].strip()
    return candidate.strip()


def _build_excerpt(paper: ParsedPaper) -> str:
    """Build a representative excerpt using the headings-first architecture.

    1. Sections are already split at logical boundaries — use that structure.
    2. Walk in document order, trim each section at the nearest sentence end
       if it exceeds the per-section limit.
    3. Stop when the total budget is consumed.
    4. Falls back to full_text truncation for papers with no detected sections.
    """
    if not paper.sections or len(paper.sections) <= 1:
        return _trim_at_sentence(paper.full_text, _SUMMARY_TOTAL_BUDGET)

    parts: list[str] = []
    budget = _SUMMARY_TOTAL_BUDGET

    for section in paper.sections:
        if budget <= 0:
            break
        text = section.text.strip()
        if not text:
            continue
        trimmed = _trim_at_sentence(text, min(_SUMMARY_SECTION_LIMIT, budget))
        parts.append(f"[{section.title}]\n{trimmed}")
        budget -= len(trimmed.split())

    return "\n\n".join(parts)


# ---------- Streaming versions (SSE) ----------


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def summarize_paper_stream(
    paper: ParsedPaper,
    ollama: OllamaClient,
    model: str | None = None,
    **_kwargs,
) -> Generator[str, None, None]:
    """Single-call streaming summary — fast (~30-60s vs 3-5min map-reduce).

    Builds a smart excerpt (abstract + intro + key sections + conclusion),
    feeds it to the LLM in one shot, streams tokens back.
    """
    excerpt = _build_excerpt(paper)
    total_sections = len([s for s in paper.sections if s.text.strip()])

    yield _sse({"type": "map_start", "total": 1})
    yield _sse({
        "type": "map_chunk_start",
        "chunk": 1,
        "total": 1,
        "label": f"Building excerpt from {total_sections} section(s)…",
    })

    prompt = FAST_SUMMARY_PROMPT.format(excerpt=excerpt)

    yield _sse({"type": "reduce_start"})

    for token in ollama.chat_stream(
        prompt=prompt,
        system=FAST_SUMMARY_SYSTEM,
        model=model,
        temperature=0.3,
        num_ctx=8192,
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
