"""
PDF parsing for Lucid.

Uses PyMuPDF (fitz) to extract text and detect section headings.
Heading detection uses font-size analysis as the primary signal — lines
with a font size larger than the median body text are treated as headings.
Falls back to text-pattern heuristics for PDFs where font data is sparse.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


@dataclass
class Page:
    page_num: int   # 1-indexed
    text: str


@dataclass
class Section:
    title: str
    start_page: int
    end_page: int
    text: str = ""
    order: int = 0


@dataclass
class ParsedPaper:
    filename: str
    num_pages: int
    full_text: str
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)


def extract_text_from_pdf(pdf_path: str | Path) -> ParsedPaper:
    """Open a PDF, extract text per page, detect sections, return ParsedPaper."""
    pdf_path = Path(pdf_path)
    doc = fitz.open(str(pdf_path))

    pages: list[Page] = []
    full_text_parts: list[str] = []

    # Collect per-line font metadata alongside plain text in one pass.
    # block_idx resets per page so we can group lines that share a block.
    # raw_lines: (line_text, page_num, max_font_size, is_bold, block_idx)
    raw_lines: list[tuple[str, int, float, bool, int]] = []

    for i, page in enumerate(doc):
        page_num = i + 1
        text = _clean_text(page.get_text("text"))
        pages.append(Page(page_num=page_num, text=text))
        full_text_parts.append(text)

        try:
            for block_idx, block in enumerate(page.get_text("dict")["blocks"]):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    if not spans:
                        continue
                    line_text = "".join(s.get("text", "") for s in spans).strip()
                    if not line_text:
                        continue
                    sizes = [s.get("size", 0) for s in spans if s.get("size", 0) > 0]
                    max_size = max(sizes) if sizes else 0
                    is_bold = any(bool(s.get("flags", 0) & 16) for s in spans)
                    raw_lines.append((line_text, page_num, max_size, is_bold, block_idx))
        except Exception:
            pass

    doc.close()

    full_text = "\n\n".join(full_text_parts)
    sections = _detect_sections(pages, raw_lines)

    return ParsedPaper(
        filename=pdf_path.name,
        num_pages=len(pages),
        full_text=full_text,
        pages=pages,
        sections=sections,
    )


def _detect_sections(
    pages: list[Page],
    raw_lines: list[tuple[str, int, float, bool, int]],
) -> list[Section]:
    """Detect sections using font-size analysis, falling back to text heuristics."""
    heading_hits = _headings_by_font(raw_lines)

    if heading_hits:
        heading_hits = _filter_author_page_clusters(heading_hits, len(pages))

    if not heading_hits:
        heading_hits = _headings_by_text(pages)

    if not heading_hits:
        full_text = "\n\n".join(p.text for p in pages)
        return [Section(title="Full Paper", start_page=1, end_page=len(pages),
                        text=full_text, order=0)]

    sections: list[Section] = []
    for idx, (title, start_page) in enumerate(heading_hits):
        end_page = (
            heading_hits[idx + 1][1] if idx + 1 < len(heading_hits)
            else pages[-1].page_num
        )
        section_text = _gather_section_text(pages, title, start_page, end_page)
        sections.append(Section(
            title=title,
            start_page=start_page,
            end_page=end_page,
            text=section_text,
            order=idx,
        ))
    return sections


_KNOWN_SECTION_KEYWORDS = {
    "abstract", "introduction", "background", "related work", "motivation",
    "preliminaries", "methodology", "methods", "approach", "architecture",
    "design", "implementation", "system overview", "experiments",
    "evaluation", "results", "discussion", "analysis", "limitations",
    "future work", "conclusion", "conclusions", "references",
    "acknowledgments", "appendix",
}


def _filter_author_page_clusters(
    hits: list[tuple[str, int]], total_pages: int
) -> list[tuple[str, int]]:
    """Drop non-section hits from pages that look like title/author pages.

    A page in the first 20 % of the document with 4+ heading candidates is
    almost certainly a title page. Only keep hits whose text matches a known
    section keyword; discard the rest (author names, affiliations, paper title).
    """
    from collections import Counter
    early_cutoff = max(2, total_pages // 5)
    page_counts = Counter(page for _, page in hits)

    result: list[tuple[str, int]] = []
    for text, page in hits:
        if page <= early_cutoff and page_counts[page] >= 4:
            lower = text.lower().strip(":.")
            is_known = any(
                lower == kw
                or lower.endswith(" " + kw)
                or lower.startswith(kw + " ")
                for kw in _KNOWN_SECTION_KEYWORDS
            )
            if not is_known:
                continue
        result.append((text, page))
    return result


def _headings_by_font(
    raw_lines: list[tuple[str, int, float, bool, int]],
    max_sections: int = 30,
) -> list[tuple[str, int]]:
    """Detect headings by font size, then merge lines that share a block.

    PyMuPDF splits one visual heading like '3.2 Attention' into multiple
    lines inside the same block. We collect all heading-candidate lines with
    their (page, block_idx), then join consecutive lines that share the same
    block into a single heading — the block boundary is the true paragraph
    separator.
    """
    if not raw_lines:
        return []

    sizes = [sz for _, _, sz, _, _ in raw_lines if sz > 0]
    if not sizes:
        return []

    body_size = statistics.median(sizes)
    heading_threshold = body_size * 1.12  # 12% larger than median = heading

    # hits carry block_idx so we can merge within a block later
    hits: list[tuple[str, int, int]] = []  # (text, page_num, block_idx)
    seen: set[str] = set()

    for line_text, page_num, font_size, is_bold, block_idx in raw_lines:
        text = line_text.strip()
        if not text or len(text) > 100:
            continue
        if _is_noise_line(text):
            continue

        is_large = font_size >= heading_threshold
        is_bold_heading = is_bold and 3 <= len(text) <= 80

        if is_large or is_bold_heading:
            key = re.sub(r"\s+", " ", text.lower())
            if key not in seen:
                seen.add(key)
                hits.append((text, page_num, block_idx))

    # Merge consecutive hits that belong to the same (page, block).
    # "3.2" and "Attention" are two lines in one block → "3.2 Attention".
    merged: list[tuple[str, int]] = []
    i = 0
    while i < len(hits):
        text, page, block = hits[i]
        parts = [text]
        j = i + 1
        while j < len(hits) and hits[j][1] == page and hits[j][2] == block:
            parts.append(hits[j][0])
            j += 1
        merged.append((" ".join(parts), page))
        i = j

    return merged[:max_sections]


def _is_noise_line(text: str) -> bool:
    """Return True for lines that are clearly not section headings."""
    t = text.strip()
    # Pure numbers, page numbers, URLs
    if re.match(r"^\d+$", t):
        return True
    # Figure/Table captions
    if re.match(r"^(fig(ure)?|table|eq(uation)?|algorithm)[\s\.\d]", t, re.IGNORECASE):
        return True
    # Very short single character or symbol
    if len(t) < 3:
        return True
    # Email address present → author byline
    if "@" in t:
        return True
    # Author contribution markers (∗ † ‡) → author name line
    if re.search(r"[∗†‡]", t):
        return True
    # Short affiliation-only lines (institution names without section-like content)
    if (len(t.split()) <= 5
            and re.match(
                r"^(Google|Microsoft|OpenAI|Meta|Amazon|Apple|DeepMind|"
                r"University|Institut|Department|Lab\b|Brain|Research\b)",
                t, re.IGNORECASE,
            )):
        return True
    return False


def _headings_by_text(pages: list[Page]) -> list[tuple[str, int]]:
    """Legacy text-pattern fallback for PDFs without useful font data."""
    COMMON = {
        "abstract", "introduction", "background", "related work", "motivation",
        "preliminaries", "methodology", "methods", "approach", "architecture",
        "design", "implementation", "system overview", "experiments",
        "evaluation", "results", "discussion", "analysis", "limitations",
        "future work", "conclusion", "conclusions", "references",
        "acknowledgments", "appendix",
    }
    PATTERN = re.compile(
        r"^\s*(?:(?:\d+(?:\.\d+)*\.?)|(?:[IVX]+\.))?\s*([A-Z][A-Za-z\s\-&/]{2,60})\s*$",
        re.MULTILINE,
    )

    hits: list[tuple[str, int]] = []
    seen: set[str] = set()

    for page in pages:
        for line in page.text.split("\n"):
            t = line.strip()
            if not t or len(t) > 80:
                continue
            lower = t.lower().strip(":.")
            is_common = any(
                lower == s or lower.endswith(" " + s) or lower.startswith(s + " ")
                for s in COMMON
            )
            is_pattern = bool(PATTERN.match(t)) and len(t.split()) <= 8
            if (is_common or is_pattern) and t.lower() not in seen:
                seen.add(t.lower())
                hits.append((t, page.page_num))
    return hits


def _gather_section_text(
    pages: list[Page], title: str, start_page: int, end_page: int
) -> str:
    collected = []
    for p in pages:
        if p.page_num < start_page or p.page_num > end_page:
            continue
        collected.append(p.text)
    joined = "\n\n".join(collected)
    idx = joined.find(title)
    if idx >= 0:
        joined = joined[idx:]
    return joined.strip()


def _clean_text(text: str) -> str:
    text = re.sub(r"-\n(\w)", r"\1", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping word-count chunks."""
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = chunk_size - overlap
    for i in range(0, len(words), step):
        chunks.append(" ".join(words[i: i + chunk_size]))
        if i + chunk_size >= len(words):
            break
    return chunks


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m backend.src.pdf_loader <pdf_path>")
        sys.exit(1)
    paper = extract_text_from_pdf(sys.argv[1])
    print(f"File: {paper.filename}  |  Pages: {paper.num_pages}")
    print(f"Sections found: {len(paper.sections)}")
    for s in paper.sections:
        print(f"  [{s.order}] {s.title!r:40s} p.{s.start_page}–{s.end_page}")
