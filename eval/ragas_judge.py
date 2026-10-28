"""RAGAS metrics with an LLM judge (OpenAI by default, Gemini optional).

RAGAS 0.4 metrics expect an Instructor-style client, so the judge always talks through the OpenAI SDK:
directly to OpenAI, or to Gemini's OpenAI-compatible endpoint. Set EVAL_PROVIDER=openai|gemini.
Judge token usage is recorded from every response (see app/usage.py).
"""

import asyncio
import os
import re
import time

from app import usage
from app.config import require_provider_key, settings

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
METRIC_NAMES = ["faithfulness", "context_recall", "context_precision", "factual_correctness"]

# Each RAGAS metric makes several LLM calls, so judge calls are spaced out. The Gemini free tier allows
# ~15 requests/minute per model (default 12); paid OpenAI tiers allow far more (default 60).
EVAL_RPM = int(os.getenv("EVAL_RPM", "12" if settings.eval_provider == "gemini" else "60"))


class _RateLimiter:
    """Spaces calls evenly: at most `rpm` requests per minute across all concurrent tasks."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / max(rpm, 1)
        self.lock = asyncio.Lock()
        self.next_slot = 0.0

    async def wait(self):
        async with self.lock:
            now = time.monotonic()
            if self.next_slot > now:
                await asyncio.sleep(self.next_slot - now)
            self.next_slot = max(now, self.next_slot) + self.interval


def _throttle(client, rpm: int):
    """Wrap client.chat.completions.create so every judge call waits for a rate-limit slot."""
    limiter = _RateLimiter(rpm)
    original = client.chat.completions.create

    async def limited_create(*args, **kwargs):
        await limiter.wait()
        response = await original(*args, **kwargs)
        usage.record_openai_response("ragas_judge", settings.eval_llm_model, response)
        return response

    client.chat.completions.create = limited_create
    return client


def _is_openai_reasoning_model(model: str) -> bool:
    return settings.eval_provider == "openai" and bool(re.match(r"^(gpt-([5-9]|1\d)|o\d)", model.lower()))


def _fix_reasoning_model_args(llm, model: str) -> None:
    """OpenAI reasoning-family models (gpt-5.x, o-series) reject max_tokens, custom temperature and top_p.
    RAGAS 0.4.3 always adds temperature=0.01, top_p=0.1, max_tokens=1024 and is meant to remap them for
    these models, but its check parses the version with int(), so dotted names like 'gpt-5.4-mini' slip
    through and every judge call would fail. Remove them ourselves."""
    if not _is_openai_reasoning_model(model):
        return
    for key in ("temperature", "top_p", "max_tokens"):
        llm.model_args.pop(key, None)
    llm.model_args["max_completion_tokens"] = 4096


def build_metrics() -> dict:
    from openai import AsyncOpenAI
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        ContextPrecisionWithReference,
        ContextRecall,
        FactualCorrectness,
        Faithfulness,
    )

    require_provider_key(settings.eval_provider)
    if settings.eval_provider == "gemini":
        key, base_url = settings.gemini_api_key, GEMINI_OPENAI_BASE_URL
    else:
        key, base_url = settings.openai_api_key, None
    # max_retries: the OpenAI SDK retries 429/5xx with exponential backoff.
    client = AsyncOpenAI(api_key=key, base_url=base_url, max_retries=8)
    client = _throttle(client, EVAL_RPM)  # must happen before llm_factory wraps the client
    llm = llm_factory(settings.eval_llm_model, client=client, temperature=0, max_tokens=4096)
    _fix_reasoning_model_args(llm, settings.eval_llm_model)
    return {
        "faithfulness": Faithfulness(llm=llm),
        "context_recall": ContextRecall(llm=llm),
        "context_precision": ContextPrecisionWithReference(llm=llm),
        "factual_correctness": FactualCorrectness(llm=llm, mode="f1"),
    }


async def _score_one(metrics: dict, s: dict, sem: asyncio.Semaphore) -> dict:
    """s has: question, answer, contexts, reference."""
    args = {
        "faithfulness": dict(user_input=s["question"], response=s["answer"], retrieved_contexts=s["contexts"]),
        "context_recall": dict(user_input=s["question"], retrieved_contexts=s["contexts"], reference=s["reference"]),
        "context_precision": dict(user_input=s["question"], retrieved_contexts=s["contexts"], reference=s["reference"]),
        "factual_correctness": dict(response=s["answer"], reference=s["reference"]),
    }
    scores, errors = {}, {}
    for name, metric in metrics.items():
        if not s["contexts"] and name != "factual_correctness":
            scores[name] = 0.0
            continue
        async with sem:
            try:
                scores[name] = float((await metric.ascore(**args[name])).value)
            except Exception as e:  # one bad judge call shouldn't kill the run
                scores[name] = None
                errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
    return {"scores": scores, "errors": errors}


async def _score_all(samples: list[dict], concurrency: int) -> list[dict]:
    metrics = build_metrics()
    sem = asyncio.Semaphore(concurrency)
    return await asyncio.gather(*[_score_one(metrics, s, sem) for s in samples])


def score_samples(samples: list[dict], concurrency: int = 1) -> list[dict]:
    return asyncio.run(_score_all(samples, concurrency))
