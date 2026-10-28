"""Data models shared by the agents, the calculator, the API and the eval scripts."""

from typing import Literal, Optional, get_args

from pydantic import BaseModel, Field

# A fixed spend taxonomy keeps the calculator simple and the agents consistent.
Category = Literal[
    "amazon",
    "flipkart",
    "swiggy",
    "zomato",
    "other_online",
    "offline",
    "travel",
    "fuel",
    "utilities",
    "insurance",
    "rent",
]
CATEGORIES: list[str] = list(get_args(Category))

CATEGORY_HELP: dict[str, str] = {
    "amazon": "Shopping on Amazon.in",
    "flipkart": "Shopping on Flipkart",
    "swiggy": "Swiggy food delivery, Instamart, Dineout",
    "zomato": "Zomato / Blinkit and other food or quick-commerce apps",
    "other_online": "Other online shopping, OTT, cab apps, Myntra, Nykaa, etc.",
    "offline": "In-store shopping, groceries in store, restaurants (card swipe / tap)",
    "travel": "Flights, hotels, trains booked by card",
    "fuel": "Petrol / diesel at fuel stations",
    "utilities": "Electricity, gas, mobile, broadband, DTH bills",
    "insurance": "Insurance premiums",
    "rent": "House rent paid by credit card",
}

CapPeriod = Literal["statement_cycle", "month", "quarter", "year"]


class SpendProfile(BaseModel):
    """Monthly spend in INR per category."""

    amazon: float = 0
    flipkart: float = 0
    swiggy: float = 0
    zomato: float = 0
    other_online: float = 0
    offline: float = 0
    travel: float = 0
    fuel: float = 0
    utilities: float = 0
    insurance: float = 0
    rent: float = 0
    amazon_prime_member: bool = Field(False, description="True only if the user says they have Amazon Prime")
    assumptions: list[str] = Field(default_factory=list, description="Assumptions made while structuring the spend")

    def spend(self, category: str) -> float:
        return float(getattr(self, category) or 0)

    def monthly_total(self) -> float:
        return sum(self.spend(c) for c in CATEGORIES)


def render_spend_text(profile: SpendProfile) -> str:
    """Turn a structured profile back into plain text (used to give the baseline the same info)."""
    parts = [f"{CATEGORY_HELP[c]}: Rs {profile.spend(c):,.0f}" for c in CATEGORIES if profile.spend(c) > 0]
    prime = "I am an Amazon Prime member." if profile.amazon_prime_member else "I am not an Amazon Prime member."
    return "My monthly credit card spends are - " + "; ".join(parts) + ". " + prime


class RewardRule(BaseModel):
    """One earning rule, e.g. '5% on online spends, max Rs 2,000 per statement cycle'."""

    categories: list[Category]
    rate_pct: float = Field(..., description="Cashback / reward value as a percent of spend, e.g. 5 for 5%")
    cap_inr: Optional[float] = Field(None, description="Max cashback this rule can earn per cap_period. null if uncapped")
    cap_period: CapPeriod = "statement_cycle"
    requires_prime: bool = Field(False, description="True if this rate applies only to Amazon Prime members")
    description: str = ""
    source: str = Field("", description="Citation, e.g. 'sbi_cashback_revision_2026.pdf p.1'")


class CardRules(BaseModel):
    """Everything the calculator needs to value one card. Extracted by the Rules Researcher."""

    card_id: str
    card_name: str
    annual_fee_inr: float = Field(..., description="Renewal fee per year before GST. 0 if lifetime free")
    fee_waiver_spend_inr: Optional[float] = Field(None, description="Annual spend that waives the renewal fee. null if none")
    fee_waiver_excluded_categories: list[Category] = Field(default_factory=list)
    reward_rules: list[RewardRule] = Field(default_factory=list)
    base_rate_pct: float = Field(0, description="Rate on eligible spends not covered by any reward rule")
    base_cap_inr: Optional[float] = None
    base_cap_period: CapPeriod = "statement_cycle"
    excluded_categories: list[Category] = Field(default_factory=list, description="Categories that earn nothing")
    overall_cap_inr: Optional[float] = Field(None, description="Max total cashback per overall_cap_period. null if none")
    overall_cap_period: CapPeriod = "statement_cycle"
    gotchas: list[str] = Field(default_factory=list, description="Fine print a user could miss, with citation")
    sources: list[str] = Field(default_factory=list)


class CategoryLine(BaseModel):
    category: str
    annual_spend: float
    rate_pct: float
    rule: str
    reward: float


class CardValue(BaseModel):
    """Output of the deterministic calculator for one card and one spend profile."""

    card_id: str
    card_name: str
    annual_spend: float
    gross_rewards: float
    fee_paid: float
    fee_waived: bool
    net_value: float
    lines: list[CategoryLine]
    caps_hit: list[str] = Field(default_factory=list)


class CardAdvice(BaseModel):
    card_id: str
    verdict: str = Field(..., description="One or two sentences on fit for this user")
    watch_outs: list[str] = Field(default_factory=list, description="Fine print relevant to this user's spends")
    citations: list[str] = Field(default_factory=list)


class Advice(BaseModel):
    top_pick_card_id: str
    summary: str
    cards: list[CardAdvice]
    assumptions: list[str] = Field(default_factory=list)


class BaselineCardEstimate(BaseModel):
    card_id: str
    estimated_annual_rewards_inr: float
    annual_fee_inr: float
    net_annual_value_inr: float
    reasoning: str = ""


class BaselineRecommendation(BaseModel):
    top_pick_card_id: str
    cards: list[BaselineCardEstimate]
    summary: str = ""
