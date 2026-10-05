"""
tocgen -- build a .toc topic list for a PDF that doesn't have one.

Strategy, best first:
  1. the PDF's embedded bookmarks (outline), if any;
  2. otherwise headings detected from the text (larger / bold font, with
     split section numbers re-joined and title-block noise filtered);
  3. otherwise a single "title" entry pointing at page 1.

Public API:
  generate_entries(pdf_path) -> (entries, method)   # entries = [(topic, page)]
  write_toc(pdf_path)        -> (count, method)      # writes <base>.toc
"""

from __future__ import annotations

import collections
import os
import re

try:
    import fitz  # PyMuPDF
except ImportError:  # pragma: no cover
    fitz = None


NUM_ONLY = re.compile(r"^\d+(\.\d+)*\.?$")
NUM_PREFIX = re.compile(r"^\d+(\.\d+)*\.?\s+\S")
DATE_LIKE = re.compile(r"^\d{1,2}\s+\w+\s+\d{4}$")
MONTH_DATE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}")
# Recurring author / organisation / page-footer lines to drop from headings.
# The names that used to be here were the author's own, in a public repo, and
# were removed; this frozen reference now matches nothing.  The C++ app reads
# its list from a local toc-noise.txt instead (TocGen.h).
AUTHOR_ORG = re.compile(r"(?!)")


def _clean(text: str) -> str:
    return " ".join(text.replace("�", "-").replace("�", "-").split())


def _body_size(doc) -> float:
    sizes = collections.Counter()
    for page in doc:
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                for sp in line["spans"]:
                    sizes[round(sp["size"], 1)] += len(sp["text"].strip())
    return sizes.most_common(1)[0][0] if sizes else 0


def _is_noise(text: str) -> bool:
    if "@" in text or "http" in text.lower():
        return True
    if DATE_LIKE.match(text) or MONTH_DATE.search(text):
        return True
    if AUTHOR_ORG.search(text):
        return True
    if len(text) < 3 or len(text) > 90:
        return True
    if sum(c.isalpha() for c in text) < 2:
        return True
    return False


def _stem_title(pdf_path: str) -> str:
    stem = os.path.splitext(os.path.basename(pdf_path))[0]
    stem = re.sub(r"^\d+[-_]?(TN\d+[A-Za-z]?)?[-_]?", "", stem, flags=re.I)
    return stem.replace("_", " ").strip() or \
        os.path.splitext(os.path.basename(pdf_path))[0]


def _best_title(doc, fallback: str) -> str:
    best = (0.0, None)
    for b in doc[0].get_text("dict")["blocks"]:
        for line in b.get("lines", []):
            for sp in line["spans"]:
                t = _clean(sp["text"])
                if 4 <= len(t) <= 80 and sum(c.isalpha() for c in t) >= 3:
                    if sp["size"] > best[0]:
                        best = (sp["size"], t)
    title = best[1] or fallback
    return title if len(title) >= 6 else fallback


def _extract_headings(doc, body: float) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    seen: set[str] = set()
    pending = None
    for pno, page in enumerate(doc, start=1):
        for b in page.get_text("dict")["blocks"]:
            for line in b.get("lines", []):
                spans = line["spans"]
                if not spans:
                    continue
                text = _clean(" ".join(s["text"] for s in spans))
                if not text:
                    continue
                size = max(s["size"] for s in spans)
                bold = any("bold" in s["font"].lower() or (s["flags"] & 16)
                           for s in spans)
                if not (size >= body + 1.0 or (bold and size >= body + 0.3)):
                    continue
                if NUM_ONLY.match(text):
                    pending = text.rstrip(".")
                    continue
                if pending and not NUM_PREFIX.match(text):
                    text = f"{pending} {text}"
                pending = None
                if _is_noise(text):
                    continue
                key = text.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append((text, pno))
    return out


def generate_entries(pdf_path: str) -> tuple[list[tuple[str, int]], str]:
    """Return (entries, method) where method is 'bookmarks'|'headings'|'title'."""
    if fitz is None:
        raise RuntimeError("PyMuPDF (pymupdf) is required to build a TOC.")
    doc = fitz.open(pdf_path)
    try:
        toc = doc.get_toc()
        if toc:
            entries = [(_clean(title), max(1, page)) for _, title, page in toc]
            entries = [(t, p) for t, p in entries if t]
            if entries:
                return entries, "bookmarks"

        body = _body_size(doc)
        headings = _extract_headings(doc, body)
        if doc.page_count > 1 and len(headings) >= 2:
            return headings, "headings"

        return [(_best_title(doc, _stem_title(pdf_path)), 1)], "title"
    finally:
        doc.close()


def write_toc(pdf_path: str) -> tuple[int, str]:
    """Generate and write <base>.toc for pdf_path. Returns (count, method)."""
    entries, method = generate_entries(pdf_path)
    name = os.path.basename(pdf_path)
    lines = [
        f"# Topics for {name}",
        f"# Auto-generated ({method}). Format: 'Topic: page' (1-based). "
        f"Edit freely.",
        "",
    ]
    for topic, page in entries:
        lines.append(f"{topic}: {page}")
    out_path = os.path.splitext(pdf_path)[0] + ".toc"
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return len(entries), method
