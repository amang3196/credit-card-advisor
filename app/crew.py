"""The agentic pipeline, built as a CrewAI Flow.

    profile_spend  ->  research_rules  ->  calculate  ->  advise
    (agent)            (agent + Pinecone    (plain        (agent + Pinecone
                        tool, cached per     Python)       tool)
                        card)

Why this shape:
- Card rules don't depend on the user, so they are extracted once per card and cached.
- LLMs are unreliable at arithmetic, so the money maths is deterministic code.
- The advisor explains the ranking; it doesn't produce it.
"""

import json
import time
from datetime import datetime, timezone
from typing import Optional

from crewai import Agent, Crew, Process, Task
from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel, Field

from app import usage
from app.calculator import rank
from app.config import settings
from app.llm import _strip_fences, get_llm
from app.manifest import load_manifest
from app.schemas import CATEGORIES, CATEGORY_HELP, Advice, CardRules, CardValue, SpendProfile
from app.tools import CardSearchTool

CATEGORY_LIST = "\n".join(f"- {c}: {CATEGORY_HELP[c]}" for c in CATEGORIES)

# v3 additions to the Rules Researcher prompt (ENGINEERING_LOG LOG-23). Each line targets an error class
# seen in the v1/v2/v2.1 benchmarks. RESEARCHER_PROMPT=v2 drops them to reproduce earlier runs.
V3_RULES = """
Extra rules (read carefully; these are the mistakes most often made on these documents):
- BASE RATE: a rate for "other spends" / "all other spends" / "non-<partner> spends" is the base rate. Put it
  in base_rate_pct and base_cap_inr, NOT in a reward rule for one category. base_rate_pct is 0 only if the
  documents say other spends earn nothing.
- EXCLUSIONS: put a category in excluded_categories only if the documents name it as excluded for this card
  (e.g. "Fuel, Rent" or the MCC list for utilities/insurance). A revision notice that does not mention a
  category does NOT remove its rewards; that category keeps the base rate.
- MERCHANT LISTS: if a rule's table lists merchants (e.g. "Amazon, Flipkart, Nykaa ..."), map every listed
  merchant to its category (Amazon -> amazon, Flipkart -> flipkart, Cleartrip/airlines/hotels -> travel).
- ONLINE-ONLY: a rule for online spends or for listed online merchants never covers offline.
- MERCHANT-SPECIFIC RATES: a higher rate for one or two named merchants (Myntra, a partner brand) does not
  apply to the whole category; the category keeps its normal rate and the merchant rate goes in gotchas.
- FEE TABLES list many cards. Take the fee only from the row that names "{card_name}" exactly. If that row is
  not in your results, search again with the exact card name plus "annual fee renewal fee". If it is still
  missing, set annual_fee_inr to 0 and write "annual fee not found in the documents" in gotchas. Never use
  another card's row.
- VERIFY: after reading your first results, run a second round of searches to confirm (a) the fee row,
  (b) every category you are about to exclude or leave at 0%.
- CHECKLIST before answering: go through all 11 categories one by one and make sure each one is either in a
  reward rule, in excluded_categories, or meant to earn base_rate_pct."""


def _safe(text: str) -> str:
    """CrewAI treats {name} in task text as a template variable, so strip braces from dynamic content."""
    return text.replace("{", "(").replace("}", ")")


def _agent(role: str, goal: str, backstory: str, tools: list | None = None) -> Agent:
    return Agent(
        role=role,
        goal=goal,
        backstory=backstory,
        tools=tools or [],
        llm=get_llm(),
        max_rpm=settings.max_rpm,
        max_iter=10,
        allow_delegation=False,
        verbose=settings.verbose,
    )


def _run_task(agent: Agent, description: str, expected_output: str, output_model: type[BaseModel] | None):
    task = Task(
        description=_safe(description),
        expected_output=expected_output,
        agent=agent,
        output_pydantic=output_model,
    )
    result = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=settings.verbose).kickoff()
    # Each agent gets its own LLM instance, so its counters are exactly this task's usage
    # (including the extra call CrewAI makes to convert the answer into the Pydantic model).
    usage.record_crewai_llm(agent.role, agent.llm)
    if output_model is None:
        return result.raw
    if result.pydantic is not None:
        return output_model.model_validate(result.pydantic.model_dump())
    return output_model.model_validate(json.loads(_strip_fences(result.raw)))


# ---------------------------------------------------------------- agents


def spend_profiler() -> Agent:
    return _agent(
        role="Spend Profiler",
        goal="Turn a person's free-text description of their spending into accurate monthly amounts per category",
        backstory="You are a meticulous personal-finance analyst. You never invent spends the user did not mention.",
    )


def rules_researcher(tool: CardSearchTool) -> Agent:
    return _agent(
        role="Card Rules Researcher",
        goal="Extract exact, cited reward rules, caps, exclusions and fees from official card documents",
        backstory=(
            "You read Indian credit card T&Cs and MITC documents for a living. You know banks revise terms "
            "with separate notices, that one PDF can cover several card variants, and that a number you "
            "cannot cite is a number you do not report."
        ),
        tools=[tool],
    )


def fine_print_advisor(tool: CardSearchTool) -> Agent:
    return _agent(
        role="Fine-print Advisor",
        goal="Explain which card suits this user and warn them about terms that affect their specific spends",
        backstory=(
            "You are a straight-talking advisor. You trust the calculator's numbers, check fine print in "
            "the documents before mentioning it, and cite every claim."
        ),
        tools=[tool],
    )


# ---------------------------------------------------------------- steps


def profile_spend(spend_text: str) -> SpendProfile:
    description = f"""Convert the user's description of their credit card spending into MONTHLY amounts in INR
for each of these categories:
{CATEGORY_LIST}

Rules:
- Convert yearly or weekly amounts to monthly. '20k' = 20000, '1 lakh' = 100000.
- If something does not fit a category, put it in other_online or offline and record that in assumptions.
- Set amazon_prime_member to true only if the user says they have Prime.
- Do not invent spends that were not mentioned; leave those at 0.

User's description:
<<<
{spend_text}
>>>"""
    return _run_task(spend_profiler(), description, "A SpendProfile with monthly INR per category.", SpendProfile)


def _cache_path(card_id: str):
    return settings.rules_cache_dir / f"{card_id}.json"


def load_cached_rules(card_id: str) -> Optional[dict]:
    path = _cache_path(card_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def extract_card_rules(card_id: str, refresh: bool = False) -> tuple[CardRules, list[str]]:
    """Run the Rules Researcher for one card. Returns (rules, retrieved contexts). Cached on disk."""
    if not refresh and (cached := load_cached_rules(card_id)):
        return CardRules(**cached["rules"]), cached.get("contexts", [])

    card = load_manifest().card(card_id)
    tool = CardSearchTool(allowed_card_ids=[card_id])
    description = f"""Extract the reward rules for the card "{card.card_name}" (card_id: {card_id}) from its official
documents. Use the search_card_terms tool with card_id="{card_id}". Run several focused searches, at least:
1. cashback / reward rates by merchant or category
2. caps per statement cycle, month or quarter
3. transactions or merchant categories that earn no rewards (exclusions)
4. annual / renewal fee and the spend needed to waive it, and spends that don't count toward the waiver
5. revisions or changes to the terms

Map the rules onto these spend categories:
{CATEGORY_LIST}

How to fill the fields:
- rate_pct is rupees earned per 100 rupees spent (5% cashback -> 5). Convert reward points with the stated value.
- Put each rate in reward_rules with its cap (cap_inr + cap_period). If two categories share one cap, put them
  in the same rule. Use base_rate_pct / base_cap_inr for "all other spends".
- Categories that earn nothing go in excluded_categories (e.g. fuel, rent) - not in a 0% rule.
- If a rule covers only some merchants inside a category (e.g. Myntra inside other_online), map it only if
  those merchants are most of that category; otherwise keep the lower rate and add a gotcha.
- If a rate applies only to Amazon Prime members, set requires_prime=true and add the non-Prime rate as a separate rule.
- annual_fee_inr is the renewal fee before GST. 0 means lifetime free.
- Some PDFs cover several card variants. Use only numbers stated for "{card.card_name}".
- If a 'revision' document or a later effective date changes a rule, the newer rule wins; note the change in gotchas.
- If something is not in the documents, use null / 0 and say so in gotchas. Never guess a number.
- Cite every rule in its source field like 'file.pdf p.2', and list all files used in sources."""
    if settings.researcher_prompt == "v3":
        description += V3_RULES.replace("{card_name}", card.card_name)
    started = time.time()
    with usage.collect() as u:
        rules: CardRules = _run_task(
            rules_researcher(tool), description, "A complete CardRules object with citations.", CardRules
        )
    rules.card_id, rules.card_name = card_id, card.card_name  # never trust the LLM with identifiers
    run_usage = u.summary()
    print(f"[usage] {card_id}: {usage.fmt(run_usage)}")

    settings.rules_cache_dir.mkdir(parents=True, exist_ok=True)
    _cache_path(card_id).write_text(
        json.dumps(
            {
                "card_id": card_id,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "model": settings.llm_model,
                "seconds": round(time.time() - started, 1),
                "usage": run_usage["total"],
                "rules": rules.model_dump(),
                "contexts": tool.contexts(),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return rules, tool.contexts()


def _ranking_text(profile: SpendProfile, ranking: list[CardValue], rules: dict[str, CardRules]) -> str:
    lines = []
    for i, v in enumerate(ranking, 1):
        excluded = [ln.category for ln in v.lines if ln.rule == "excluded"]
        lines.append(
            f"{i}. {v.card_name} (card_id {v.card_id}): net Rs {v.net_value:,.0f}/yr = rewards Rs {v.gross_rewards:,.0f} "
            f"- fee Rs {v.fee_paid:,.0f}{' (fee waived)' if v.fee_waived else ''}. "
            f"Caps hit: {'; '.join(v.caps_hit) or 'none'}. Spends earning nothing: {', '.join(excluded) or 'none'}. "
            f"Known fine print: {' | '.join(rules[v.card_id].gotchas) or 'none'}"
        )
    spends = ", ".join(f"{c} Rs {profile.spend(c):,.0f}" for c in CATEGORIES if profile.spend(c) > 0)
    return f"User's monthly spends: {spends}. Amazon Prime: {profile.amazon_prime_member}.\n\n" + "\n".join(lines)


def advise(profile: SpendProfile, ranking: list[CardValue], rules: dict[str, CardRules]) -> tuple[Advice, list[str]]:
    tool = CardSearchTool(allowed_card_ids=list(rules))
    description = f"""A deterministic calculator has already valued each card for this user. The numbers are exact:
do not recompute or change them.

{_ranking_text(profile, ranking, rules)}

Your job:
- top_pick_card_id must be the card ranked 1 above ({ranking[0].card_id}).
- summary: 2-3 sentences a normal person understands, with the rupee difference between the top two cards.
- For every card, a short verdict and watch_outs that matter for THIS user's spends (e.g. 'your Rs 25,000 rent
  earns nothing on this card'). Before stating fine print, confirm it with search_card_terms and cite it
  like 'file.pdf p.2' in citations.
- assumptions: anything the user should know about how this was calculated (even monthly spending, fees include 18% GST,
  joining-year offers ignored)."""
    advice = _run_task(fine_print_advisor(tool), description, "An Advice object.", Advice)
    advice.top_pick_card_id = ranking[0].card_id
    return advice, tool.contexts()


def answer_question(question: str) -> dict:
    """Agentic Q&A over the documents (used by /ask and by the RAGAS eval)."""
    manifest = load_manifest()
    tool = CardSearchTool(allowed_card_ids=manifest.card_ids)
    cards = "\n".join(f"- {c.card_id}: {c.card_name}" for c in manifest.cards)
    description = f"""Answer the question using the search_card_terms tool. First work out which card(s) the
question is about and pass that card_id, then search as many times as you need with focused queries.
If a revision document changes a rule, give the newer rule. Answer in 1-4 sentences, using only facts
from the passages, and cite them like [file.pdf p.2]. If the documents don't say, reply that they don't.

Cards:
{cards}

Question: {question}"""
    answer = _run_task(rules_researcher(tool), description, "A short, cited answer.", None)
    return {
        "mode": "agent",
        "answer": str(answer).strip(),
        "contexts": tool.contexts(),
        "sources": sorted({c.citation for c in tool.retrieved}),
    }


# ---------------------------------------------------------------- flow


class RecommendState(BaseModel):
    spend_text: str = ""
    spend_profile: Optional[SpendProfile] = None
    card_ids: list[str] = Field(default_factory=list)
    refresh_rules: bool = False
    rules: dict[str, CardRules] = Field(default_factory=dict)
    ranking: list[CardValue] = Field(default_factory=list)
    advice: Optional[Advice] = None
    contexts: list[str] = Field(default_factory=list)
    timings: dict[str, float] = Field(default_factory=dict)


class RecommendFlow(Flow[RecommendState]):
    def _timed(self, name: str, started: float) -> None:
        self.state.timings[name] = round(time.time() - started, 2)

    @start()
    def profile(self):
        t = time.time()
        if self.state.spend_profile is None:
            self.state.spend_profile = profile_spend(self.state.spend_text)
        self._timed("profile_spend", t)

    @listen(profile)
    def research(self):
        t = time.time()
        for card_id in self.state.card_ids or load_manifest().card_ids:
            rules, contexts = extract_card_rules(card_id, refresh=self.state.refresh_rules)
            self.state.rules[card_id] = rules
            self.state.contexts.extend(contexts)
        self._timed("research_rules", t)

    @listen(research)
    def calculate(self):
        t = time.time()
        self.state.ranking = rank(self.state.spend_profile, list(self.state.rules.values()))
        self._timed("calculate", t)

    @listen(calculate)
    def explain(self):
        t = time.time()
        advice, contexts = advise(self.state.spend_profile, self.state.ranking, self.state.rules)
        self.state.advice = advice
        self.state.contexts.extend(contexts)
        self._timed("advise", t)


def recommend(
    spend_text: str = "",
    spend_profile: Optional[SpendProfile] = None,
    refresh_rules: bool = False,
    card_ids: Optional[list[str]] = None,
) -> RecommendState:
    flow = RecommendFlow()
    flow.kickoff(
        inputs={
            "spend_text": spend_text,
            "spend_profile": spend_profile.model_dump() if spend_profile else None,
            "refresh_rules": refresh_rules,
            "card_ids": card_ids or [],
        }
    )
    return flow.state
