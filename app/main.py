"""FastAPI app. Run:  uvicorn app.main:app --reload   then open http://127.0.0.1:8000/docs"""

import time
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from app import usage
from app.config import settings
from app.manifest import load_manifest
from app.schemas import SpendProfile

app = FastAPI(
    title="Credit Card Fit Advisor",
    description=(
        "Agents read Indian credit card T&Cs, a calculator values each card for your spending, "
        "and an advisor explains the fine print. Compare `mode=crew` with the plain-RAG `mode=baseline`."
    ),
    version="0.1.0",
)

EXAMPLE_SPEND = (
    "I spend around 12k a month on Amazon (I have Prime), about 6,000 on Swiggy, 3k on Zomato, "
    "15,000 in shops and restaurants, 4,000 on petrol, 3,000 on phone and electricity bills, "
    "and I pay my 25k rent with my card. Two flights a year, roughly 24,000 in total."
)


class RecommendRequest(BaseModel):
    spend_description: str = Field(..., examples=[EXAMPLE_SPEND])
    mode: Literal["crew", "baseline"] = "crew"
    refresh_rules: bool = Field(False, description="Re-extract card rules instead of using the cache (slow)")


class AskRequest(BaseModel):
    question: str = Field(..., examples=["What is the maximum cashback per statement cycle on the Cashback SBI Card?"])
    mode: Literal["agent", "baseline"] = "agent"


class IngestRequest(BaseModel):
    reset: bool = False


def _fail(e: Exception):
    if isinstance(e, RuntimeError) and "Missing" in str(e):
        raise HTTPException(status_code=400, detail=str(e))
    raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "llm_model": settings.llm_model,
        "llm_provider": settings.llm_provider,
        "eval_model": f"{settings.eval_provider}/{settings.eval_llm_model}",
        "pinecone_index": settings.index_name,
        "pinecone_key_set": bool(settings.pinecone_api_key),
        "llm_key_set": bool(getattr(settings, f"{settings.llm_provider}_api_key", "")),
        "openai_key_set": bool(settings.openai_api_key),
        "gemini_key_set": bool(settings.gemini_api_key),
    }


@app.get("/cards")
def cards():
    from app.crew import load_cached_rules

    m = load_manifest()
    out = []
    for c in m.cards:
        docs = m.docs_for(c.card_id)
        cached = load_cached_rules(c.card_id)
        out.append(
            {
                **c.model_dump(),
                "documents": [{"file": d.file, "doc_type": d.doc_type, "present": d.path.exists()} for d in docs],
                "rules_cached": bool(cached),
                "rules_extracted_at": cached.get("extracted_at") if cached else None,
            }
        )
    return out


@app.post("/ingest")
def ingest(req: IngestRequest):
    from app.ingest import run_ingestion

    try:
        return run_ingestion(reset=req.reset)
    except Exception as e:
        _fail(e)


@app.get("/rules/{card_id}")
def get_rules(card_id: str):
    from app.crew import load_cached_rules

    cached = load_cached_rules(card_id)
    if not cached:
        raise HTTPException(404, f"No cached rules for {card_id}. POST /rules/{card_id}/refresh first.")
    return cached


@app.post("/rules/{card_id}/refresh")
def refresh_rules(card_id: str):
    from app.crew import extract_card_rules

    if card_id not in load_manifest().card_ids:
        raise HTTPException(404, f"Unknown card_id {card_id}")
    try:
        t = time.time()
        with usage.collect() as u:
            rules, contexts = extract_card_rules(card_id, refresh=True)
        return {"seconds": round(time.time() - t, 1), "usage": u.summary()["total"], "rules": rules,
                "contexts_used": len(contexts)}
    except Exception as e:
        _fail(e)


@app.post("/ask")
def ask(req: AskRequest):
    try:
        t = time.time()
        with usage.collect() as u:
            if req.mode == "baseline":
                from app.baseline import baseline_answer

                out = baseline_answer(req.question)
            else:
                from app.crew import answer_question

                out = answer_question(req.question)
        return {**out, "seconds": round(time.time() - t, 1), "usage": u.summary()["total"]}
    except Exception as e:
        _fail(e)


@app.post("/recommend")
def recommend(req: RecommendRequest):
    try:
        t = time.time()
        if req.mode == "baseline":
            from app.baseline import baseline_recommend

            with usage.collect() as u:
                out = baseline_recommend(req.spend_description)
            return {**out, "seconds": round(time.time() - t, 1), "usage": u.summary()["total"]}

        from app.crew import recommend as run_crew

        with usage.collect() as u:
            state = run_crew(spend_text=req.spend_description, refresh_rules=req.refresh_rules)
        return {
            "mode": "crew",
            "seconds": round(time.time() - t, 1),
            "usage": u.summary()["total"],
            "timings": state.timings,
            "spend_profile": state.spend_profile,
            "top_pick": state.ranking[0].card_name if state.ranking else None,
            "ranking": state.ranking,
            "advice": state.advice,
            "rules_used": state.rules,
            "contexts": state.contexts,
        }
    except Exception as e:
        _fail(e)


@app.post("/calculate")
def calculate_only(profile: SpendProfile, card_ids: Optional[list[str]] = Query(None)):
    """No LLM calls: value a structured spend profile against the cached rules."""
    from app.calculator import rank
    from app.crew import load_cached_rules
    from app.schemas import CardRules

    ids = card_ids or load_manifest().card_ids
    rules = [CardRules(**c["rules"]) for i in ids if (c := load_cached_rules(i))]
    if not rules:
        raise HTTPException(404, "No cached rules yet. Run /recommend once or POST /rules/{card_id}/refresh.")
    return rank(profile, rules)
