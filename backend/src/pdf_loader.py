"""
PDF parsing for Lucid.

Primary path: GROBID REST API → TEI XML → structured sections with hierarchy.
Fallback: PyMuPDF font-size heuristics (used when GROBID is unreachable).
"""
from __future__ import annotations

import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import requests
from lxml import etree

_GROBID_URL = os.getenv("GROBID_URL", "http://localhost:8070")
_TEI = "http://www.tei-c.org/ns/1.0"

try:
    import fitz as _fitz
    _PYMUPDF_OK = True
except ImportError:
    _PYMUPDF_OK = False


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
    level: int = 1            # 1 = section, 2 = subsection, 3 = subsubsection
    section_number: str = ""  # "1", "2.1", "3.2.1" from GROBID <head n="…">
    parent_title: str = ""    # empty for top-level sections


@dataclass
class ParsedPaper:
    filename: str
    num_pages: int
    full_text: str
    pages: list[Page] = field(default_factory=list)
    sections: list[Section] = field(default_factory=list)
    title: str = ""
    abstract: str = ""


def extract_text_from_pdf(pdf_path: str | Path) -> ParsedPaper:
    """Parse a PDF via GROBID, falling back to PyMuPDF on failure."""
    pdf_path = Path(pdf_path)
    try:
        tei_xml = _call_grobid(pdf_path)
        return _parse_tei(tei_xml, pdf_path.name)
    except Exception as exc:
        print(f"[pdf_loader] GROBID unavailable ({exc}), using PyMuPDF fallback")
        return _pymupdf_fallback(pdf_path)


# ── GROBID path ────────────────────────────────────────────────────────────────

def _call_grobid(pdf_path: Path) -> str:
    with open(pdf_path, "rb") as f:
        resp = requests.post(
            f"{_GROBID_URL}/api/processFulltextDocument",
            files={"input": (pdf_path.name, f, "application/pdf")},
            data={"consolidateCitations": "0"},
            timeout=120,
        )
    resp.raise_for_status()
    return resp.text


def _parse_tei(tei_xml: str, filename: str) -> ParsedPaper:
    root = etree.fromstring(tei_xml.encode())

    paper_title = _all_text(root.find(f".//{{{_TEI}}}titleStmt/{{{_TEI}}}title"))
    abstract = _extract_abstract(root)

    sections: list[Section] = []
    order = 0

    if abstract:
        sections.append(Section(
            title="Abstract", start_page=1, end_page=1,
            text=abstract, order=order, level=1, section_number="0",
        ))
        order += 1

    body = root.find(f".//{{{_TEI}}}body")
    if body is not None:
        for div in body.findall(f"{{{_TEI}}}div"):
            order = _process_div(div, sections, order, level=1, parent_title="")

    _assign_hierarchy(sections)

    full_text = "\n\n".join(s.text for s in sections)
    num_pages = max((s.start_page for s in sections), default=1)

    return ParsedPaper(
        filename=filename,
        num_pages=num_pages,
        full_text=full_text.strip(),
        pages=[],
        sections=sections,
        title=paper_title,
        abstract=abstract,
    )


def _process_div(
    div, sections: list[Section], order: int, level: int, parent_title: str
) -> int:
    head_el = div.find(f"{{{_TEI}}}head")
    title = _all_text(head_el)
    section_number = head_el.get("n", "") if head_el is not None else ""
    start_page = _page_from_coords(head_el) if head_el is not None else 1

    paragraphs = [
        _all_text(p)
        for p in div.findall(f"{{{_TEI}}}p")
        if _all_text(p)
    ]
    section_text = "\n\n".join(paragraphs)

    if title and section_text:
        sections.append(Section(
            title=title,
            start_page=start_page,
            end_page=start_page,
            text=section_text,
            order=order,
            level=level,
            section_number=section_number,
            parent_title=parent_title,
        ))
        order += 1

    for child in div.findall(f"{{{_TEI}}}div"):
        order = _process_div(
            child, sections, order, level + 1,
            parent_title=title or parent_title,
        )

    return order


def _assign_hierarchy(sections: list[Section]) -> None:
    """Derive level and parent_title from section_number (e.g. '3.2.1' → level 3)."""
    # Build a map from section_number → title for parent lookup
    num_to_title: dict[str, str] = {s.section_number: s.title for s in sections if s.section_number}
    for s in sections:
        if s.section_number and s.section_number != "0":
            parts = s.section_number.split(".")
            s.level = len(parts)
            if len(parts) > 1:
                parent_num = ".".join(parts[:-1])
                s.parent_title = num_to_title.get(parent_num, "")


def _extract_abstract(root) -> str:
    abstract_el = root.find(f".//{{{_TEI}}}abstract")
    if abstract_el is None:
        return ""
    parts = [_all_text(p) for p in abstract_el.iter(f"{{{_TEI}}}p")]
    return "\n\n".join(p for p in parts if p)


def _all_text(element) -> str:
    if element is None:
        return ""
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def _page_from_coords(element) -> int:
    """GROBID @coords format: 'pageNo,x1,y1,x2,y2'"""
    coords = element.get("coords", "")
    if coords:
        try:
            return int(coords.split(",")[0])
        except (ValueError, IndexError):
            pass
    return 1


# ── PyMuPDF fallback ───────────────────────────────────────────────────────────

def _pymupdf_fallback(pdf_path: Path) -> ParsedPaper:
    if not _PYMUPDF_OK:
        raise RuntimeError("PyMuPDF not installed and GROBID is unreachable")

    doc = _fitz.open(str(pdf_path))
    pages: list[Page] = []
    full_text_parts: list[str] = []
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
    sections = _detect_sections_pymupdf(pages, raw_lines)

    return ParsedPaper(
        filename=pdf_path.name,
        num_pages=len(pages),
        full_text=full_text,
        pages=pages,
        sections=sections,
    )


_KNOWN_SECTION_KEYWORDS = {
    "abstract", "introduction", "background", "related work", "motivation",
    "preliminaries", "methodology", "methods", "approach", "architecture",
    "design", "implementation", "system overview", "experiments",
    "evaluation", "results", "discussion", "analysis", "limitations",
    "future work", "conclusion", "conclusions", "references",
    "acknowledgments", "appendix",
}


def _detect_sections_pymupdf(
    pages: list[Page],
    raw_lines: list[tuple[str, int, float, bool, int]],
) -> list[Section]:
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
            title=title, start_page=start_page, end_page=end_page,
            text=section_text, order=idx,
        ))
    return sections


def _filter_author_page_clusters(
    hits: list[tuple[str, int]], total_pages: int
) -> list[tuple[str, int]]:
    from collections import Counter
    early_cutoff = max(2, total_pages // 5)
    page_counts = Counter(page for _, page in hits)
    result: list[tuple[str, int]] = []
    for text, page in hits:
        if page <= early_cutoff and page_counts[page] >= 4:
            lower = text.lower().strip(":.")
            is_known = any(
                lower == kw or lower.endswith(" " + kw) or lower.startswith(kw + " ")
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
    if not raw_lines:
        return []
    sizes = [sz for _, _, sz, _, _ in raw_lines if sz > 0]
    if not sizes:
        return []
    body_size = statistics.median(sizes)
    heading_threshold = body_size * 1.12

    hits: list[tuple[str, int, int]] = []
    seen: set[str] = set()
    for line_text, page_num, font_size, is_bold, block_idx in raw_lines:
        text = line_text.strip()
        if not text or len(text) > 100 or _is_noise_line(text):
            continue
        if font_size >= heading_threshold or (is_bold and 3 <= len(text) <= 80):
            key = re.sub(r"\s+", " ", text.lower())
            if key not in seen:
                seen.add(key)
                hits.append((text, page_num, block_idx))

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
    t = text.strip()
    if re.match(r"^\d+$", t):
        return True
    if re.match(r"^(fig(ure)?|table|eq(uation)?|algorithm)[\s\.\d]", t, re.IGNORECASE):
        return True
    if len(t) < 3:
        return True
    if "@" in t:
        return True
    if re.search(r"[∗†‡]", t):
        return True
    if (len(t.split()) <= 5 and re.match(
        r"^(Google|Microsoft|OpenAI|Meta|Amazon|Apple|DeepMind|"
        r"University|Institut|Department|Lab\b|Brain|Research\b)",
        t, re.IGNORECASE,
    )):
        return True
    return False


def _headings_by_text(pages: list[Page]) -> list[tuple[str, int]]:
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
            is_known = any(
                lower == s or lower.endswith(" " + s) or lower.startswith(s + " ")
                for s in _KNOWN_SECTION_KEYWORDS
            )
            is_pattern = bool(PATTERN.match(t)) and len(t.split()) <= 8
            if (is_known or is_pattern) and t.lower() not in seen:
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


# ── Chunking ───────────────────────────────────────────────────────────────────

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
    print(f"File: {paper.filename}  |  Pages: {paper.num_pages}  |  Title: {paper.title}")
    print(f"Sections found: {len(paper.sections)}")
    for s in paper.sections:
        print(f"  [{s.order}] L{s.level} {s.section_number:6s} {s.title!r:40s} p.{s.start_page}")
