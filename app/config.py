"""Settings loaded from .env. Import this module before crewai so the env flags apply."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Keep CrewAI quiet: no tracing prompts, no telemetry.
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


class Settings:
    pinecone_api_key: str = os.getenv("PINECONE_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # Agents + baseline, in CrewAI's provider/model format: openai/... or gemini/...
    llm_model: str = os.getenv("LLM_MODEL", "openai/gpt-5.4-mini")
    llm_timeout: float = float(os.getenv("LLM_TIMEOUT", "60"))  # seconds per attempt
    # RAGAS judge: provider + model name without prefix
    eval_provider: str = os.getenv("EVAL_PROVIDER", "openai").lower()
    eval_llm_model: str = os.getenv("EVAL_LLM_MODEL", "gpt-5.4-mini")

    index_name: str = os.getenv("PINECONE_INDEX", "card-advisor")
    namespace: str = os.getenv("PINECONE_NAMESPACE", "cards")
    embed_model: str = os.getenv("PINECONE_EMBED_MODEL", "llama-text-embed-v2")
    top_k: int = int(os.getenv("TOP_K", "6"))
    # How PDFs are parsed + chunked at ingestion: v2.1 = Markdown sections + merged table cells
    # restored (default), v2 = Markdown sections, v1 = page windows
    chunker: str = os.getenv("CHUNKER", "v2.1").lower()
    # Rules Researcher prompt: v3 = v2 + category-coverage / base-rate / fee-row rules (LOG-23)
    researcher_prompt: str = os.getenv("RESEARCHER_PROMPT", "v3").lower()
    max_rpm: int = int(os.getenv("MAX_RPM", "10"))
    verbose: bool = os.getenv("CREW_VERBOSE", "true").lower() == "true"

    data_dir: Path = ROOT / "data"
    pdf_dir: Path = ROOT / "data" / "pdfs"
    manifest_path: Path = ROOT / "data" / "cards.yaml"
    rules_cache_dir: Path = ROOT / "data" / "rules_cache"
    extracted_dir: Path = ROOT / "data" / "extracted"

    @property
    def llm_provider(self) -> str:
        prefix = self.llm_model.split("/", 1)[0].lower() if "/" in self.llm_model else "openai"
        return "gemini" if prefix in ("gemini", "google") else prefix


settings = Settings()

KEY_FOR_PROVIDER = {"openai": "openai_api_key", "gemini": "gemini_api_key"}


def require(*names: str) -> None:
    """Fail early with a readable message when a key is missing."""
    missing = [n for n in names if not getattr(settings, n)]
    if missing:
        env_names = ", ".join(n.upper() for n in missing)
        raise RuntimeError(f"Missing {env_names}. Add it to your .env file (see .env.example).")


def require_provider_key(provider: str) -> None:
    if provider not in KEY_FOR_PROVIDER:
        raise RuntimeError(f"Unsupported provider '{provider}'. Use openai or gemini.")
    require(KEY_FOR_PROVIDER[provider])
