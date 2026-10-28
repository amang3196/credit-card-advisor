"""PDF -> chunks -> Pinecone (integrated embedding).

Chunkers (CHUNKER in .env, see ENGINEERING_LOG LOG-19 and LOG-21):
  v2.1 (default) v2 + vertically merged table cells restored (e.g. one cap shared by 4 rows).
  v2   PDF -> Markdown with pymupdf4llm -> section-aware chunks, tables kept
       whole, context line (card + section) on each chunk. See app/chunking_v2.py
  v1           PDF -> plain page text -> 180-word windows with 40-word overlap, per page.

Run:  python -m app.ingest --reset   (wipe the namespace first; use this when switching chunker)
      python -m app.ingest           (upsert; re-running overwrites the same ids)
"""

import argparse
import json
import re
import time
from datetime import datetime

import pymupdf as fitz

from app import chunking_v2
from app.config import require, settings
from app.manifest import Document, load_manifest

CHUNK_WORDS = 180
OVERLAP_WORDS = 40
BATCH_SIZE = 90  # integrated-embedding upserts allow up to 96 records per call


def extract_pages(doc: Document) -> list[tuple[int, str]]:
    pages = []
    with fitz.open(doc.path) as pdf:
        for i, page in enumerate(pdf, start=1):
            text = re.sub(r"[ \t]+", " ", page.get_text("text"))
            text = re.sub(r"\n{2,}", "\n", text).strip()
            if text:
                pages.append((i, text))
    return pages


def chunk_page(text: str) -> list[str]:
    words = text.split()
    if len(words) <= CHUNK_WORDS:
        return [" ".join(words)]
    step = CHUNK_WORDS - OVERLAP_WORDS
    return [" ".join(words[i : i + CHUNK_WORDS]) for i in range(0, len(words) - OVERLAP_WORDS, step)]


def build_records(doc: Document, card_names: dict[str, str]) -> list[dict]:
    if settings.chunker in ("v2", "v2.1"):
        return build_records_v2(doc, card_names)
    return build_records_v1(doc, card_names)


def build_records_v2(doc: Document, card_names: dict[str, str]) -> list[dict]:
    pages = chunking_v2.pdf_to_markdown_pages(doc.path, fill_merged=settings.chunker == "v2.1")

    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    (settings.extracted_dir / f"{doc.path.stem}.md").write_text(
        "\n\n".join(f"<!-- page {p} -->\n{t}" for p, t in pages), encoding="utf-8"
    )

    doc_cards = {c: card_names[c] for c in doc.card_ids}
    chunks = chunking_v2.chunk_blocks(chunking_v2.to_blocks(pages), doc_cards)
    records = []
    for n, ch in enumerate(chunks):
        records.append(
            {
                "_id": f"{doc.path.stem}-s{n:03d}",
                # The context line is embedded with the body, so "BLCK 5% online" finds the BLCK table.
                "chunk_text": f"{chunking_v2.context_line(ch, card_names)}\n\n{ch.body}",
                "card_ids": ch.card_ids,
                "card_names": [card_names[c] for c in ch.card_ids],
                "doc_type": doc.doc_type,
                "effective_date": doc.effective_date or "unknown",
                "source_file": doc.file,
                "page": ch.page,
                "page_end": ch.page_end,
                "section": ch.section[:200],
                "chunker": settings.chunker,
            }
        )
    return records


def build_records_v1(doc: Document, card_names: dict[str, str]) -> list[dict]:
    records = []
    pages = extract_pages(doc)

    # Keep a plain-text copy: handy for checking what the PDF parser actually saw.
    settings.extracted_dir.mkdir(parents=True, exist_ok=True)
    (settings.extracted_dir / f"{doc.path.stem}.txt").write_text(
        "\n\n".join(f"--- page {p} ---\n{t}" for p, t in pages), encoding="utf-8"
    )

    for page_no, text in pages:
        for j, chunk in enumerate(chunk_page(text)):
            records.append(
                {
                    "_id": f"{doc.path.stem}-p{page_no}-c{j}",
                    "chunk_text": chunk,
                    "card_ids": doc.card_ids,
                    "card_names": [card_names[c] for c in doc.card_ids],
                    "doc_type": doc.doc_type,
                    "effective_date": doc.effective_date or "unknown",
                    "source_file": doc.file,
                    "page": page_no,
                    "chunker": "v1",
                }
            )
    return records


def get_or_create_index():
    from pinecone import Pinecone

    require("pinecone_api_key")
    pc = Pinecone(api_key=settings.pinecone_api_key)
    if not pc.has_index(settings.index_name):
        print(f"Creating index '{settings.index_name}' with integrated model {settings.embed_model} ...")
        pc.create_index_for_model(
            name=settings.index_name,
            cloud="aws",
            region="us-east-1",  # the only region on the free Starter plan
            embed={"model": settings.embed_model, "field_map": {"text": "chunk_text"}},
        )
    return pc.Index(settings.index_name)


def _upsert_with_retry(index, batch: list[dict], attempts: int = 5) -> None:
    for attempt in range(attempts):
        try:
            index.upsert_records(namespace=settings.namespace, records=batch)
            return
        except Exception as e:  # rate limits on the free tier show up as 429s
            if attempt == attempts - 1:
                raise
            wait = 2 ** attempt * 5
            print(f"  upsert failed ({type(e).__name__}: {str(e)[:120]}), retrying in {wait}s")
            time.sleep(wait)


def run_ingestion(reset: bool = False) -> dict:
    manifest = load_manifest()
    card_names = {c.card_id: c.card_name for c in manifest.cards}
    index = get_or_create_index()

    if reset:
        try:
            index.delete(delete_all=True, namespace=settings.namespace)
            print(f"Cleared namespace '{settings.namespace}'")
        except Exception as e:
            print(f"Nothing to clear ({type(e).__name__})")

    print(f"Chunker: {settings.chunker}")
    stats = {"ingested": [], "skipped": [], "chunks": 0, "chunker": settings.chunker}
    all_records = []
    for doc in manifest.documents:
        if not doc.path.exists():
            stats["skipped"].append(doc.file)
            print(f"SKIP  {doc.file} (not found in data/pdfs)")
            continue
        recs = build_records(doc, card_names)
        all_records.extend(recs)
        stats["ingested"].append({"file": doc.file, "chunks": len(recs)})
        print(f"READ  {doc.file}: {len(recs)} chunks")

    for i in range(0, len(all_records), BATCH_SIZE):
        _upsert_with_retry(index, all_records[i : i + BATCH_SIZE])
        print(f"  upserted {min(i + BATCH_SIZE, len(all_records))}/{len(all_records)}")
        time.sleep(1)

    stats["chunks"] = len(all_records)
    with open(settings.data_dir / "chunks.jsonl", "w", encoding="utf-8") as f:
        for r in all_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    # Recorded so eval runs can say which chunker the index was built with.
    (settings.data_dir / "index_info.json").write_text(json.dumps(
        {"chunker": settings.chunker, "chunks": len(all_records), "reset": reset,
         "built_at": datetime.now().isoformat(timespec="seconds")}, indent=2))
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="delete existing vectors in the namespace first")
    args = parser.parse_args()
    result = run_ingestion(reset=args.reset)
    print(json.dumps({k: v for k, v in result.items() if k != "ingested"}, indent=2))
    print("Note: new records can take a few seconds to become searchable.")
