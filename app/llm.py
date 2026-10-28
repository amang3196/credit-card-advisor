"""One place to build the LLM so agents and the baseline use the same model."""

import json
import re
from typing import TypeVar

from pydantic import BaseModel

from app import usage
from app.config import require_provider_key, settings

T = TypeVar("T", bound=BaseModel)


def get_llm(temperature: float = 0.1):
    """LLM for the agents and the baseline. The provider comes from the LLM_MODEL prefix."""
    from crewai import LLM

    provider = settings.llm_provider
    require_provider_key(provider)

    if provider == "openai":
        # GPT-5.x through Chat Completions: function tools only work with reasoning effort "none"
        # (also the model's default), and not every GPT-5.x model accepts a custom temperature,
        # so neither is sent. The OpenAI SDK retries 429/5xx with backoff.
        return LLM(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            timeout=settings.llm_timeout,
            max_retries=6,
        )

    from google.genai import types

    # Retry Gemini's transient "503 high demand" and "429 rate limit" errors with backoff.
    retry = types.HttpRetryOptions(
        attempts=6, initial_delay=2.0, max_delay=30.0, exp_base=2.0, http_status_codes=[429, 500, 502, 503, 504]
    )
    return LLM(
        model=settings.llm_model,
        temperature=temperature,
        api_key=settings.gemini_api_key,
        client_params={"http_options": types.HttpOptions(timeout=int(settings.llm_timeout * 1000), retry_options=retry)},
    )


def _strip_fences(text: str) -> str:
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    return m.group(1).strip() if m else text


def call_text(prompt: str, step: str = "baseline") -> str:
    llm = get_llm()
    out = llm.call([{"role": "user", "content": prompt}])
    usage.record_crewai_llm(step, llm)
    return str(out)


def call_structured(prompt: str, model: type[T], step: str = "baseline") -> T:
    """Single LLM call that returns a validated Pydantic object."""
    llm = get_llm()
    out = llm.call([{"role": "user", "content": prompt}], response_model=model)
    usage.record_crewai_llm(step, llm)
    if isinstance(out, model):
        return out
    if isinstance(out, BaseModel):
        return model.model_validate(out.model_dump())
    if isinstance(out, dict):
        return model.model_validate(out)
    return model.model_validate(json.loads(_strip_fences(str(out))))
