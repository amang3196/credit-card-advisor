"""Token and cost tracking for every LLM call (agents, baseline, RAGAS judge).

Usage:
    with usage.collect() as u:      # collects everything recorded inside the block (also across threads/tasks)
        ...run something...
    u.summary()                     # {"total": {...tokens, est_cost_usd}, "by_step": {...}}

Prices are USD per 1M tokens and only an estimate; check the provider's pricing page.
Override with PRICE_INPUT_PER_M / PRICE_CACHED_INPUT_PER_M / PRICE_OUTPUT_PER_M in .env.
"""

import contextvars
import os
import threading
from collections import defaultdict
from contextlib import contextmanager

# (input, cached input, output) per 1M tokens
PRICES = {
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gemini-3.5-flash-lite": (0.30, 0.03, 2.50),
}

_FIELDS = ("prompt_tokens", "cached_prompt_tokens", "completion_tokens", "reasoning_tokens", "requests")


def _model_name(model: str) -> str:
    return model.split("/", 1)[1] if "/" in model else model


def price_for(model: str) -> tuple[float, float, float] | None:
    env = [os.getenv(k) for k in ("PRICE_INPUT_PER_M", "PRICE_CACHED_INPUT_PER_M", "PRICE_OUTPUT_PER_M")]
    if all(env):
        return tuple(float(x) for x in env)  # type: ignore[return-value]
    return PRICES.get(_model_name(model))


def cost_usd(model: str, u: dict) -> float | None:
    """Reasoning tokens are already included in completion_tokens for OpenAI."""
    p = price_for(model)
    if p is None:
        return None
    p_in, p_cached, p_out = p
    cached = u.get("cached_prompt_tokens", 0)
    uncached = max(u.get("prompt_tokens", 0) - cached, 0)
    return round((uncached * p_in + cached * p_cached + u.get("completion_tokens", 0) * p_out) / 1e6, 6)


class Collector:
    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: dict.fromkeys(_FIELDS, 0))

    def add(self, step: str, model: str, **counts: int) -> None:
        with self._lock:
            row = self._data[(step, _model_name(model))]
            for k in _FIELDS:
                row[k] += int(counts.get(k) or 0)

    def summary(self) -> dict:
        by_step, total = {}, dict.fromkeys(_FIELDS, 0)
        total_cost, cost_known = 0.0, True
        with self._lock:
            for (step, model), row in sorted(self._data.items()):
                c = cost_usd(model, row)
                by_step[f"{step} ({model})"] = {**row, "est_cost_usd": c}
                for k in _FIELDS:
                    total[k] += row[k]
                if c is None:
                    cost_known = False
                else:
                    total_cost += c
        total["est_cost_usd"] = round(total_cost, 4) if cost_known else None
        return {"total": total, "by_step": by_step}


_active: contextvars.ContextVar[tuple[Collector, ...]] = contextvars.ContextVar("usage_collectors", default=())


@contextmanager
def collect():
    c = Collector()
    token = _active.set(_active.get() + (c,))
    try:
        yield c
    finally:
        _active.reset(token)


def record(step: str, model: str, **counts: int) -> None:
    for c in _active.get():
        c.add(step, model, **counts)


def record_crewai_llm(step: str, llm) -> dict:
    """Read the cumulative usage of a CrewAI LLM instance (we create one per agent / per baseline call)."""
    m = llm.get_token_usage_summary()
    counts = dict(
        prompt_tokens=m.prompt_tokens,
        cached_prompt_tokens=m.cached_prompt_tokens,
        completion_tokens=m.completion_tokens,
        reasoning_tokens=m.reasoning_tokens,
        requests=m.successful_requests,
    )
    record(step, llm.model, **counts)
    return counts


def record_openai_response(step: str, model: str, response) -> None:
    """Usage from a raw OpenAI chat.completions response (used for the RAGAS judge)."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    details = getattr(u, "prompt_tokens_details", None)
    cdetails = getattr(u, "completion_tokens_details", None)
    record(
        step,
        model,
        prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
        cached_prompt_tokens=(getattr(details, "cached_tokens", 0) or 0) if details else 0,
        completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        reasoning_tokens=(getattr(cdetails, "reasoning_tokens", 0) or 0) if cdetails else 0,
        requests=1,
    )


def fmt(summary: dict) -> str:
    t = summary["total"]
    cost = "unknown price" if t["est_cost_usd"] is None else f"~${t['est_cost_usd']:.4f}"
    return (f"{t['requests']} LLM calls, {t['prompt_tokens']:,} input tokens "
            f"({t['cached_prompt_tokens']:,} cached), {t['completion_tokens']:,} output tokens, {cost}")
