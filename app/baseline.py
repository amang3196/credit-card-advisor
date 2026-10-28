"""Plain RAG baseline: one retrieval (no card filter, no query rewriting) + one LLM call.
This is what the agentic version is measured against."""

from app.config import settings
from app.llm import call_structured, call_text
from app.manifest import load_manifest
from app.retriever import format_chunks, search
from app.schemas import BaselineRecommendation

QA_PROMPT = """You answer questions about Indian credit card terms using ONLY the passages below.
Cite passages like [file.pdf p.2]. If the passages do not contain the answer, say so.

Passages:
{context}

Question: {question}
Answer:"""

RECOMMEND_PROMPT = """You are a credit card advisor for India. Using ONLY the passages below, estimate the
yearly value of each card for this user and pick the best one.

Cards to compare (use these card_id values): {cards}

User's spending: {spend}

For each card give estimated_annual_rewards_inr, annual_fee_inr (what the user would actually pay
in a year, including 18% GST, or 0 if waived) and net_annual_value_inr = rewards - fee.

Passages:
{context}
"""


def baseline_answer(question: str) -> dict:
    chunks = search(question, top_k=settings.top_k)
    answer = call_text(QA_PROMPT.format(context=format_chunks(chunks), question=question), step="baseline_qa")
    return {
        "mode": "baseline",
        "answer": answer.strip(),
        "contexts": [f"[{c.citation}] {c.text}" for c in chunks],
        "sources": sorted({c.citation for c in chunks}),
    }


def baseline_recommend(spend_text: str) -> dict:
    manifest = load_manifest()
    query = spend_text + " cashback rate cap exclusions annual fee waiver"
    chunks = search(query, top_k=12)
    cards = ", ".join(f"{c.card_id} ({c.card_name})" for c in manifest.cards)
    rec = call_structured(
        RECOMMEND_PROMPT.format(cards=cards, spend=spend_text, context=format_chunks(chunks)),
        BaselineRecommendation,
        step="baseline_recommend",
    )
    return {
        "mode": "baseline",
        "recommendation": rec.model_dump(),
        "contexts": [f"[{c.citation}] {c.text}" for c in chunks],
    }
