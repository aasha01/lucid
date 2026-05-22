"""
Deep paper explanation for Lucid.

Uses the explain_paper.j2 Jinja2 template to produce an 8-section structured
explanation: problem statement, core idea, architecture walkthrough, numbers,
mental model, implications, limitations, and 3 things to remember.

Selects the most signal-dense sections so the prompt stays within 8K context.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Generator

from .llm import OllamaClient
from .pdf_loader import ParsedPaper
from .prompt_manager import prompt_manager


def _log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] EXPLAIN {msg}", flush=True)

# Safety net: trim any single section that exceeds this word count (~3000 tokens)
_SECTION_WORD_LIMIT = 2200
# Total content budget for the whole prompt (8K context - template - response headroom)
_TOTAL_WORD_BUDGET = 4000


def _trim_at_sentence(text: str, max_words: int) -> str:
    """Trim to max_words at the nearest sentence boundary.

    Never cuts mid-sentence — searches backwards from the word limit for
    the last sentence-ending punctuation (. ? !).
    Falls back to word boundary only if no sentence end is found.
    """
    words = text.split()
    if len(words) <= max_words:
        return text

    candidate = " ".join(words[:max_words])

    # Search backwards for a sentence boundary
    for marker in (". ", "? ", "! ", ".\n", "?\n", "!\n"):
        pos = candidate.rfind(marker)
        # Only use if we're keeping at least 60% of the candidate
        if pos > len(candidate) * 0.6:
            return candidate[: pos + 1].strip()

    return candidate.strip()  # fallback: word boundary


def _select_sections(paper: ParsedPaper) -> list[dict]:
    """Select sections in document order, trimming each at a sentence boundary.

    Architecture:
    1. Headings first — sections are already split at logical boundaries
    2. Trim any section that exceeds _SECTION_WORD_LIMIT at the nearest sentence end
    3. Fill the total budget left to right — early sections get full allocation,
       later ones share whatever remains
    """
    sections = [s for s in paper.sections if s.text.strip()]
    result: list[dict] = []
    budget = _TOTAL_WORD_BUDGET

    for section in sections:          # preserve document order
        if budget <= 0:
            break
        per_section_limit = min(_SECTION_WORD_LIMIT, budget)
        trimmed = _trim_at_sentence(section.text, per_section_limit)
        result.append({
            "title": section.title,
            "start_page": section.start_page,
            "end_page": section.end_page,
            "text": trimmed,
        })
        budget -= len(trimmed.split())

    return result


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _milestone(step: str, status: str, message: str, detail: list[str] | None = None) -> str:
    data: dict = {"type": "milestone", "step": step, "status": status, "message": message}
    if detail is not None:
        data["detail"] = detail
    return _sse(data)


def explain_paper_stream(
    paper: ParsedPaper,
    ollama: OllamaClient,
    model: str | None = None,
) -> Generator[str, None, None]:
    """Yield SSE milestone + token events for the 8-section explanation."""

    # ── Milestone 1: section selection ──────────────────────────────────────
    yield _milestone("sections", "start", "Detecting sections…")
    sections = _select_sections(paper)
    total_words = sum(len(s["text"].split()) for s in sections)
    section_titles = [s["title"] for s in sections]
    _log(f"Selected {len(sections)} sections, ~{total_words} words: {section_titles}")
    yield _milestone(
        "sections", "done",
        f"{len(sections)} section(s) selected — ~{total_words} words",
        detail=section_titles,
    )

    # ── Milestone 2: prompt rendering ────────────────────────────────────────
    yield _milestone("prompt", "start", "Rendering prompt template…")
    prompt = prompt_manager.render(
        "explain_paper.j2",
        paper_title=paper.filename.replace(".pdf", ""),
        num_pages=paper.num_pages,
        sections=sections,
    )
    prompt_words = len(prompt.split())
    _log(f"Prompt rendered: ~{prompt_words} words (~{prompt_words * 4 // 3} tokens)")
    yield _milestone("prompt", "done", f"Prompt ready — ~{prompt_words} words (~{prompt_words * 4 // 3} tokens)")

    # ── Milestone 3: LLM generation ──────────────────────────────────────────
    yield _milestone("generate", "start", "Sending to LLM — streaming explanation…")
    _log("LLM generation started — tokens will stream now")
    token_count = 0
    for token in ollama.chat_stream(
        prompt=prompt,
        model=model,
        temperature=0.3,
        num_ctx=8192,
    ):
        yield _sse({"type": "token", "text": token})
        token_count += 1

    _log(f"LLM generation complete — {token_count} tokens")
    yield _milestone("generate", "done", f"Complete — {token_count} tokens generated")
    yield _sse({"type": "done"})
