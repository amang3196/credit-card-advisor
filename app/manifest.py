"""Reads data/cards.yaml."""

from functools import lru_cache

import yaml
from pydantic import BaseModel

from app.config import settings


class Card(BaseModel):
    card_id: str
    card_name: str
    bank: str


class Document(BaseModel):
    file: str
    card_ids: list[str]
    doc_type: str = "base"
    effective_date: str = "unknown"
    url: str = ""

    @property
    def path(self):
        return settings.pdf_dir / self.file


class Manifest(BaseModel):
    cards: list[Card]
    documents: list[Document]

    def card(self, card_id: str) -> Card:
        for c in self.cards:
            if c.card_id == card_id:
                return c
        raise KeyError(f"Unknown card_id '{card_id}'. Known: {[c.card_id for c in self.cards]}")

    def docs_for(self, card_id: str) -> list[Document]:
        return [d for d in self.documents if card_id in d.card_ids]

    @property
    def card_ids(self) -> list[str]:
        return [c.card_id for c in self.cards]


@lru_cache
def load_manifest() -> Manifest:
    with open(settings.manifest_path, encoding="utf-8") as f:
        return Manifest(**yaml.safe_load(f))
