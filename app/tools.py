"""CrewAI tool over Pinecone. Every passage it returns is logged so RAGAS can score
the answer against exactly what the agent saw."""

from typing import Optional

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.retriever import Chunk, format_chunks, search


class CardSearchInput(BaseModel):
    query: str = Field(..., description="What to look for, e.g. 'maximum cashback per statement cycle on online spends'")
    card_id: Optional[str] = Field(None, description="Restrict the search to one card_id. Leave empty to search all allowed cards.")


class CardSearchTool(BaseTool):
    name: str = "search_card_terms"
    description: str = (
        "Searches the official credit card terms & conditions PDFs. Returns passages with a citation "
        "like [file.pdf p.2], the cards each document covers, doc_type (base or revision) and effective date."
    )
    args_schema: type[BaseModel] = CardSearchInput
    allowed_card_ids: list[str] = Field(default_factory=list)
    top_k: int = 6
    retrieved: list[Chunk] = Field(default_factory=list)

    def __init__(self, allowed_card_ids: list[str], **kwargs):
        super().__init__(allowed_card_ids=allowed_card_ids, **kwargs)
        self.description += f" Valid card_id values: {', '.join(allowed_card_ids)}."

    def _run(self, query: str, card_id: Optional[str] = None) -> str:
        ids = [card_id] if card_id in self.allowed_card_ids else self.allowed_card_ids
        chunks = search(query, card_ids=ids, top_k=self.top_k)
        seen = {c.id for c in self.retrieved}
        self.retrieved.extend(c for c in chunks if c.id not in seen)
        return format_chunks(chunks)

    def contexts(self) -> list[str]:
        return [f"[{c.citation}] {c.text}" for c in self.retrieved]
