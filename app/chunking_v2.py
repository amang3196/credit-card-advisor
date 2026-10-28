"""v2 parsing + chunking: PDF -> Markdown (pymupdf4llm) -> section-aware chunks.

Why (see ENGINEERING_LOG LOG-19):
  * v1 used plain page text and 180-word windows per page. Table rows lost their
    columns (SBI fee table: wrong card's fee picked) and a table that continued
    onto the next page was cut in two (Swiggy Ornge Table 2.1).
  * v2 keeps tables as Markdown rows, chunks along the document's own headings
    (a section may cross a page boundary), never cuts a table row, and prefixes
    each chunk with a short context line (card + section) so both the embedding
    and the LLM know what the passage is about.
  * v2.1 (LOG-21) also restores vertically merged table cells, which Markdown cannot
    represent (Swiggy Ornge: one "Rs.1500 per billing cycle" cap cell shared by four rows).

Pure functions only (except the two PDF readers at the top) (no Pinecone, no network) so it can be unit-tested offline.
"""

import re
from collections import Counter
from dataclasses import dataclass, field

MAX_CHARS = 1800        # soft target for one chunk (v1 chunks were ~1,100 chars)
TABLE_MAX_CHARS = 2400  # a table up to this size is kept in one piece
MIN_CHARS = 300         # a smaller section is merged into the next one


@dataclass
class Block:
    kind: str           # "heading" | "table" | "text"
    text: str
    page: int
    page_end: int = 0   # set when a table continues onto later pages

    @property
    def last_page(self) -> int:
        return max(self.page, self.page_end)


@dataclass
class Chunk:
    body: str
    section: str
    page: int
    page_end: int
    card_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- parsing

def pdf_to_markdown_pages(path, fill_merged: bool = False) -> list[tuple[int, str]]:
    """[(page_no, markdown)] using pymupdf4llm. Running headers/footers dropped, no OCR
    (these PDFs are digital, and OCR would add a Tesseract dependency and nondeterminism).
    fill_merged=True (chunker v2.1) restores vertically merged table cells."""
    import pymupdf4llm

    pages = pymupdf4llm.to_markdown(
        str(path), page_chunks=True, header=False, footer=False, use_ocr=False, show_progress=False
    )
    out = [(int(p["metadata"]["page_number"]), p["text"]) for p in pages]
    if fill_merged:
        out = fill_merged_cells(path, out)
    return out


# ---------------------------------------------------------------- merged cells (v2.1)

def merged_groups(path, page_no: int) -> list[tuple[int, int, list[list[tuple[int, int]]]]]:
    """For each table PyMuPDF finds on the page: (n_rows, n_cols, vertical merge groups).
    A group is the list of (row, col) positions covered by ONE merged cell in one column.
    PyMuPDF reports a covered position as None (an empty cell is "")."""
    import pymupdf

    result = []
    with pymupdf.open(path) as pdf:
        for t in pdf[page_no - 1].find_tables().tables:
            cells = [r.cells for r in t.rows]
            if not any(c is None for row in cells for c in row):
                result.append((len(cells), t.col_count, []))
                continue
            xc = [None] * t.col_count  # x centre of each column
            for row in cells:
                for j, c in enumerate(row):
                    if c is not None and xc[j] is None:
                        xc[j] = (c[0] + c[2]) / 2
            groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
            for i, row in enumerate(cells):
                yc = (t.rows[i].bbox[1] + t.rows[i].bbox[3]) / 2
                for j, c in enumerate(row):
                    if c is not None or xc[j] is None:
                        continue
                    for oi, orow in enumerate(cells):
                        oc = orow[j]  # same column only: vertical merges
                        if oc is not None and oc[0] <= xc[j] <= oc[2] and oc[1] <= yc <= oc[3]:
                            groups.setdefault((oi, j), [(oi, j)]).append((i, j))
                            break
            result.append((len(cells), t.col_count, list(groups.values())))
    return result


def _split_row(line: str) -> list[str]:
    return line.strip()[1:-1].split("|")


def _is_separator(line: str) -> bool:
    return set(line.strip()) <= set("|-: ")


def fill_table(lines: list[str], groups: list[list[tuple[int, int]]]) -> list[str]:
    """Write the merged cell's text into every row it covers, tagged so the model sees it is
    ONE shared cell (e.g. one cap shared by several reward rows)."""
    data_idx = [k for k, l in enumerate(lines) if not _is_separator(l)]
    rows = [_split_row(lines[k]) for k in data_idx]
    for g in groups:
        texts = [rows[i][j].strip() for i, j in g if i < len(rows) and j < len(rows[i])]
        value = " ".join(t for t in texts if t)
        if not value:
            continue
        for i, j in g:
            if i < len(rows) and j < len(rows[i]):
                rows[i][j] = f"{value} [one merged cell across {len(g)} rows]"
    out = list(lines)
    for k, row in zip(data_idx, rows):
        out[k] = "|" + "|".join(row) + "|"
    return out


def fill_merged_cells(path, pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
    """Match each Markdown table to the PyMuPDF table of the same shape on the same page
    (in order) and fill its vertically merged cells. Tables that don't match are left as is."""
    out = []
    for page_no, md in pages:
        lines = md.split("\n")
        tables, cur = [], []
        for k, l in enumerate(lines + [""]):
            if l.strip().startswith("|"):
                cur.append(k)
            elif cur:
                tables.append(cur)
                cur = []
        if not tables:
            out.append((page_no, md))
            continue
        found = [g for g in merged_groups(path, page_no)]
        used: set[int] = set()
        for idx in tables:
            tl = [lines[k] for k in idx]
            data = [l for l in tl if not _is_separator(l)]
            shape = (len(data), len(_split_row(data[0])))
            for n, (nr, nc, groups) in enumerate(found):
                if n not in used and (nr, nc) == shape:
                    used.add(n)
                    if groups:
                        for k, new in zip(idx, fill_table(tl, groups)):
                            lines[k] = new
                    break
        out.append((page_no, "\n".join(lines)))
    return out


_INLINE = re.compile(r"</?u>|</?sup>|</?sub>|\*\*|(?<!\w)_+|_+(?!\w)")


def clean_inline(s: str) -> str:
    """Drop Markdown/HTML emphasis that only costs tokens; keep table pipes."""
    s = s.replace("<br>", " ")
    s = _INLINE.sub("", s)
    return re.sub(r"[ \t]+", " ", s).strip()


def _norm(line: str) -> str:
    return re.sub(r"^#+\s*", "", clean_inline(line)).strip().lower()


def find_boilerplate(pages: list[tuple[int, str]]) -> set[str]:
    """Short non-table lines repeated on 3+ pages (e.g. 'Classification - Internal').
    pymupdf4llm sometimes renders them as headings, which would split a section."""
    seen = Counter()
    for _, md in pages:
        lines = {_norm(l) for l in md.splitlines()
                 if l.strip() and not l.lstrip().startswith(("|", "-", "*"))}
        seen.update(l for l in lines if l and len(l.split()) <= 6)
    return {l for l, n in seen.items() if n >= 3}


def to_blocks(pages: list[tuple[int, str]]) -> list[Block]:
    boiler = find_boilerplate(pages)
    blocks: list[Block] = []
    for page_no, md in pages:
        para: list[str] = []
        table: list[str] = []

        def flush_para():
            if para:
                blocks.append(Block("text", "\n".join(para), page_no))
                para.clear()

        def flush_table():
            if table:
                blocks.append(Block("table", "\n".join(table), page_no))
                table.clear()

        for raw in md.splitlines():
            line = raw.strip()
            if not line:
                flush_para()
                flush_table()
                continue
            if _norm(line) in boiler:
                continue
            if line.startswith("|"):
                flush_para()
                table.append(clean_inline(line))
                continue
            flush_table()
            if line.startswith("#"):
                title = clean_inline(line.lstrip("#"))
                # Captions like "Table 1.1" and long misdetected sentences stay as text.
                if title and not re.match(r"(?i)table\s*\d", title) and len(title.split()) <= 20:
                    flush_para()
                    blocks.append(Block("heading", title, page_no))
                    continue
                line = title
            para.append(clean_inline(line))
        flush_para()
        flush_table()
    return _merge_continued_tables(blocks)


def _cols(table: str) -> int:
    return table.split("\n", 1)[0].count("|")


def _merge_continued_tables(blocks: list[Block]) -> list[Block]:
    """Glue a table back together when it runs onto the next page WITHOUT repeating its
    header (Swiggy Ornge Table 2.1): the continuation is the first block of the next
    page, has the same number of columns, and pymupdf4llm has mistaken its first data
    row for a header (that separator line is dropped). Tables that repeat their header
    on every page (SBI fee schedule) stay one block per page: each piece already has
    its header and keeps an exact page number for citations."""
    out: list[Block] = []
    seen_pages: set[int] = set()
    for b in blocks:
        first_on_page = b.page not in seen_pages
        seen_pages.add(b.page)
        prev = out[-1] if out else None
        if (b.kind == "table" and first_on_page and prev is not None and prev.kind == "table"
                and b.page == prev.last_page + 1 and _cols(b.text) == _cols(prev.text)
                and b.text.split("\n", 1)[0] != prev.text.split("\n", 1)[0]):
            rows = [l for l in b.text.split("\n") if not set(l) <= set("|-: ")]
            out[-1] = Block("table", prev.text + "\n" + "\n".join(rows), prev.page, b.page)
            continue
        out.append(b)
    return out


# ---------------------------------------------------------------- chunking

def _split_table(text: str, limit: int) -> list[str]:
    """Split a big table between rows, repeating the header row on every piece."""
    lines = text.split("\n")
    head, rows = (lines[:2], lines[2:]) if len(lines) > 2 and set(lines[1]) <= set("|-: ") else ([], lines)
    pieces, cur = [], []
    for r in rows:
        if cur and len("\n".join(head + cur + [r])) > limit:
            pieces.append("\n".join(head + cur))
            cur = []
        cur.append(r)
    if cur:
        pieces.append("\n".join(head + cur))
    return pieces


def _split_text(text: str, limit: int) -> list[str]:
    """Split long prose on line/sentence boundaries."""
    units = re.split(r"(?<=[.;:])\s+|\n", text)
    pieces, cur = [], ""
    for u in units:
        if cur and len(cur) + len(u) + 1 > limit:
            pieces.append(cur)
            cur = ""
        cur = f"{cur} {u}".strip() if cur else u
    if cur:
        pieces.append(cur)
    return pieces


def _scope_card(heading: str, card_names: dict[str, str]) -> str | None:
    """If a heading names exactly one of the document's cards, the section is about that card."""
    h = heading.lower()
    hits = [cid for cid, name in card_names.items() if name.lower() in h]
    return hits[0] if len(hits) == 1 else None


def chunk_blocks(blocks: list[Block], card_names: dict[str, str]) -> list[Chunk]:
    """card_names: {card_id: card_name} for the cards this document covers."""
    all_ids = list(card_names)
    scope: list[str] = all_ids
    chunks: list[Chunk] = []
    buf: list[Block] = []
    section = ""
    buf_section = ""

    def size(bs):
        return sum(len(b.text) + 1 for b in bs)

    def emit(text: str, bs: list[Block]):
        end = max(b.last_page for b in bs)
        chunks.append(Chunk(text.strip(), buf_section or section, bs[0].page, end, list(scope)))

    def flush():
        nonlocal buf
        if buf:
            emit("\n\n".join(b.text if b.kind != "heading" else f"## {b.text}" for b in buf), buf)
        buf = []

    for b in blocks:
        if b.kind == "heading":
            # New section: close the current chunk unless it is too small to stand alone.
            if size(buf) >= MIN_CHARS:
                flush()
            if len(scope) > 1 or len(all_ids) > 1:
                new_scope = _scope_card(b.text, card_names)
                if new_scope and [new_scope] != scope:
                    flush()  # never let one chunk straddle two cards
                    scope = [new_scope]
            section = b.text
            if not buf:
                buf_section = section
            buf.append(b)
            continue

        limit = TABLE_MAX_CHARS if b.kind == "table" else MAX_CHARS
        if len(b.text) > limit:
            parts = _split_table(b.text, MAX_CHARS) if b.kind == "table" else _split_text(b.text, MAX_CHARS)
        else:
            parts = [b.text]
        for part in parts:
            piece = Block(b.kind, part, b.page, b.page_end)
            if buf and size(buf) + len(part) > (TABLE_MAX_CHARS if b.kind == "table" else MAX_CHARS):
                headings_only = all(x.kind == "heading" for x in buf)
                if not headings_only:
                    flush()
                    buf_section = section
            if not buf:
                buf_section = section
            buf.append(piece)
    flush()
    return chunks


def context_line(chunk: Chunk, card_names: dict[str, str]) -> str:
    cards = "; ".join(card_names[c] for c in chunk.card_ids)
    return f"{cards} | Section: {chunk.section}" if chunk.section else cards
