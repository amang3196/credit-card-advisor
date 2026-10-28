"""Thin wrapper over Pinecone search with card filters and citation formatting."""

from functools import lru_cache

from pydantic import BaseModel

from app.config import require, settings

FIELDS = ["chunk_text", "card_ids", "card_names", "doc_type", "effective_date", "source_file", "page", "page_end"]


class Chunk(BaseModel):
    id: str
    score: float
    text: str
    card_ids: list[str]
    card_names: list[str]
    doc_type: str
    effective_date: str
    source_file: str
    page: int
    page_end: int = 0  # v2 chunks can run across pages (a section or table continues)

    @property
    def citation(self) -> str:
        if self.page_end > self.page:
            return f"{self.source_file} p.{self.page}-{self.page_end}"
        return f"{self.source_file} p.{self.page}"


@lru_cache
def get_index():
    from pinecone import Pinecone

    require("pinecone_api_key")
    return Pinecone(api_key=settings.pinecone_api_key).Index(settings.index_name)


def search(query: str, card_ids: list[str] | None = None, top_k: int | None = None) -> list[Chunk]:
    """Semantic search. Pass card_ids to restrict to documents about those cards."""
    flt = {"card_ids": {"$in": card_ids}} if card_ids else None
    resp = get_index().search(
        namespace=settings.namespace,
        top_k=top_k or settings.top_k,
        inputs={"text": query},
        filter=flt,
        fields=FIELDS,
    )
    chunks = []
    for hit in resp.result.hits:
        f = dict(hit.fields)
        chunks.append(
            Chunk(
                id=hit.id,
                score=float(hit.score),
                text=f.get("chunk_text", ""),
                card_ids=list(f.get("card_ids", [])),
                card_names=list(f.get("card_names", [])),
                doc_type=f.get("doc_type", "base"),
                effective_date=str(f.get("effective_date", "unknown")),
                source_file=f.get("source_file", ""),
                page=int(f.get("page", 0)),
                page_end=int(f.get("page_end", 0)),
            )
        )
    return chunks


def format_chunks(chunks: list[Chunk]) -> str:
    """Render chunks for an LLM prompt, with the metadata it needs to cite and resolve revisions."""
    if not chunks:
        return "No matching passages found."
    blocks = []
    for c in chunks:
        header = (
            f"[{c.citation}] cards: {', '.join(c.card_names)} | doc_type: {c.doc_type} "
            f"| effective: {c.effective_date}"
        )
        blocks.append(f"{header}\n{c.text}")
    return "\n\n---\n\n".join(blocks)
