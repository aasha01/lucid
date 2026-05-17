"""
PDF parsing for Lucid.

Uses PyMuPDF (fitz) to extract text with page-number metadata, then
heuristically detects section headings (Abstract, Introduction, Methods, etc.)
so the section-by-section feature works.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF


# Common section headings in academic / technical white papers.
# We match these case-insensitively at the start of a line.
COMMON_SECTIONS = [
    "abstract",
    "introduction",
    "background",
    "related work",
    "motivation",
    "preliminaries",
    "methodology",
    "methods",
    "approach",
    "architecture",
    "design",
    "implementation",
    "system overview",
    "experiments",
    "evaluation",
    "results",
    "discussion",
    "analysis",
    "limitations",
    "future work",
    "conclusion",
    "conclusions",
    "references",
    "acknowledgments",
    "appendix",
]

# Pattern: optional number prefix (1., 1.1, II., etc.) + heading text
# Examples that match:
#   "1. Introduction"
#   "II. Methodology"
#   "3.1 System Design"
#   "Abstract"
SECTION_PATTERN = re.compile(
    r"^\s*(?:(?:\d+(?:\.\d+)*\.?)|(?:[IVX]+\.))?\s*([A-Z][A-Za-z\s\-&/]{2,60})\s*$",
    re.MULTILINE,
)


@dataclass
class Page:
    """One page of extracted text."""
    page_num: int  # 1-indexed
    text: str


@dataclass
class Section:
    """A detected section of the paper."""
    title: str
    start_page: int
    end_page: int
    text: str = ""
    # Order in the document (0 = first section)
    order: int = 0


@dataclass
class ParsedPaper:
    """The fully parsed paper."""
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

    for i, page in enumerate(doc):
        # "text" mode is the default; "blocks" preserves more layout
        # but plain "text" is cleaner for downstream LLM consumption.
        text = page.get_text("text")
        text = _clean_text(text)
        pages.append(Page(page_num=i + 1, text=text))
        full_text_parts.append(text)

    doc.close()
    full_text = "\n\n".join(full_text_parts)
    sections = detect_sections(pages)

    return ParsedPaper(
        filename=pdf_path.name,
        num_pages=len(pages),
        full_text=full_text,
        pages=pages,
        sections=sections,
    )


def _clean_text(text: str) -> str:
    """Light cleanup: collapse runs of whitespace, fix hyphenation at line breaks."""
    # Fix words split across line breaks: "compu-\nter" -> "computer"
    text = re.sub(r"-\n(\w)", r"\1", text)
    # Replace single newlines inside paragraphs with spaces (keep double newlines)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # Collapse excessive whitespace
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_sections(pages: list[Page]) -> list[Section]:
    """Find section headings across all pages and assemble Section objects."""
    # Walk through pages, find heading-like lines, record (title, page_num)
    heading_hits: list[tuple[str, int]] = []  # (title, page_num)

    for page in pages:
        for line in page.text.split("\n"):
            line_stripped = line.strip()
            if not line_stripped or len(line_stripped) > 80:
                continue
            if _looks_like_heading(line_stripped):
                heading_hits.append((line_stripped, page.page_num))

    # If we found nothing, fall back to a single "Full Paper" section
    if not heading_hits:
        full_text = "\n\n".join(p.text for p in pages)
        return [
            Section(
                title="Full Paper",
                start_page=1,
                end_page=len(pages),
                text=full_text,
                order=0,
            )
        ]

    # Build sections from consecutive heading_hits
    sections: list[Section] = []
    for idx, (title, start_page) in enumerate(heading_hits):
        end_page = (
            heading_hits[idx + 1][1] if idx + 1 < len(heading_hits) else pages[-1].page_num
        )
        section_text = _gather_text_for_section(pages, title, start_page, end_page)
        sections.append(
            Section(
                title=title,
                start_page=start_page,
                end_page=end_page,
                text=section_text,
                order=idx,
            )
        )
    return sections


def _looks_like_heading(line: str) -> bool:
    """Heuristic: is this line a section heading?"""
    lower = line.lower().strip(":.")
    # Strong signal: exact match to a common heading
    for s in COMMON_SECTIONS:
        if lower == s or lower.endswith(" " + s) or lower.startswith(s + " "):
            return True
    # Weak signal: matches "N. Title" or "N.N Title" pattern with Title Case
    if SECTION_PATTERN.match(line):
        # Require the captured title to be Title Case or ALL CAPS
        words = line.split()
        if len(words) <= 8 and (
            all(w[0].isupper() or not w[0].isalpha() for w in words)
            or line.isupper()
        ):
            return True
    return False


def _gather_text_for_section(
    pages: list[Page], title: str, start_page: int, end_page: int
) -> str:
    """Pull text between this heading and the next heading."""
    collected: list[str] = []
    for p in pages:
        if p.page_num < start_page or p.page_num > end_page:
            continue
        collected.append(p.text)
    # Optional refinement: trim text before the heading on start_page
    joined = "\n\n".join(collected)
    idx = joined.find(title)
    if idx >= 0:
        joined = joined[idx:]
    return joined.strip()


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 100,
) -> list[str]:
    """Split text into overlapping chunks by word count.

    chunk_size and overlap are measured in words (not tokens), which is a
    reasonable approximation: 800 words ≈ 1000–1100 tokens.
    """
    words = text.split()
    if not words:
        return []
    chunks: list[str] = []
    step = chunk_size - overlap
    for i in range(0, len(words), step):
        chunk = " ".join(words[i : i + chunk_size])
        chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
    return chunks


if __name__ == "__main__":
    # Quick smoke test: python -m backend.src.pdf_loader path/to/paper.pdf
    import sys
    if len(sys.argv) < 2:
        print("Usage: python -m backend.src.pdf_loader <pdf_path>")
        sys.exit(1)
    paper = extract_text_from_pdf(sys.argv[1])
    print(f"File: {paper.filename}")
    print(f"Pages: {paper.num_pages}")
    print(f"Sections found: {len(paper.sections)}")
    for s in paper.sections:
        print(f"  [{s.order}] {s.title} (pages {s.start_page}-{s.end_page})")
