"""Deterministic rewards calculator.

Design choice: the LLM extracts the rules, plain Python does the arithmetic.
Assumes spend is spread evenly across the year, so a monthly cap of X equals 12*X a year.
"""

from collections import defaultdict

from app.schemas import CATEGORIES, CardRules, CardValue, CategoryLine, RewardRule, SpendProfile

PERIODS_PER_YEAR = {"statement_cycle": 12, "month": 12, "quarter": 4, "year": 1}
GST = 0.18  # Indian card fees are quoted "+ applicable taxes"


def _annual_cap(cap: float | None, period: str) -> float | None:
    # LLMs sometimes write 0 to mean "no cap". A real cap of 0 never happens (non-earning
    # categories go in excluded_categories), so treat 0 or less as uncapped.
    return None if cap is None or cap <= 0 else cap * PERIODS_PER_YEAR[period]


def _best_rule(rules: CardRules, category: str, is_prime: bool) -> tuple[int, RewardRule] | None:
    """Highest-rate rule that covers this category and applies to this user."""
    candidates = [
        (i, r)
        for i, r in enumerate(rules.reward_rules)
        if category in r.categories and (is_prime or not r.requires_prime)
    ]
    return max(candidates, key=lambda x: x[1].rate_pct) if candidates else None


def calculate(profile: SpendProfile, rules: CardRules) -> CardValue:
    # 1. Assign every category to a rule group (or "excluded" / "base").
    lines: list[dict] = []
    for cat in CATEGORIES:
        annual = profile.spend(cat) * 12
        if annual <= 0:
            continue
        if cat in rules.excluded_categories:
            lines.append(dict(category=cat, annual_spend=annual, rate_pct=0.0, rule="excluded", group=None))
            continue
        match = _best_rule(rules, cat, profile.amazon_prime_member)
        if match:
            i, r = match
            label = r.description or f"{r.rate_pct}% rule"
            lines.append(dict(category=cat, annual_spend=annual, rate_pct=r.rate_pct, rule=label, group=f"rule_{i}"))
        else:
            lines.append(
                dict(category=cat, annual_spend=annual, rate_pct=rules.base_rate_pct, rule="base rate", group="base")
            )

    # 2. Apply caps per group.
    caps: dict[str, tuple[float | None, str]] = {"base": (_annual_cap(rules.base_cap_inr, rules.base_cap_period), "base rate")}
    for i, r in enumerate(rules.reward_rules):
        caps[f"rule_{i}"] = (_annual_cap(r.cap_inr, r.cap_period), r.description or f"{r.rate_pct}% rule")

    uncapped_by_group: dict[str, float] = defaultdict(float)
    for ln in lines:
        ln["uncapped"] = ln["annual_spend"] * ln["rate_pct"] / 100
        if ln["group"]:
            uncapped_by_group[ln["group"]] += ln["uncapped"]

    caps_hit: list[str] = []
    scale: dict[str, float] = {}
    for group, uncapped in uncapped_by_group.items():
        cap, label = caps[group]
        if cap is not None and uncapped > cap:
            scale[group] = cap / uncapped
            caps_hit.append(f"{label}: capped at Rs {cap:,.0f}/yr (would have earned Rs {uncapped:,.0f})")
        else:
            scale[group] = 1.0

    for ln in lines:
        ln["reward"] = ln["uncapped"] * scale.get(ln["group"], 0.0) if ln["group"] else 0.0

    gross = sum(ln["reward"] for ln in lines)

    # 3. Overall cap on total cashback.
    overall = _annual_cap(rules.overall_cap_inr, rules.overall_cap_period)
    if overall is not None and gross > overall:
        caps_hit.append(f"Overall cap: Rs {overall:,.0f}/yr (would have earned Rs {gross:,.0f})")
        factor = overall / gross
        for ln in lines:
            ln["reward"] *= factor
        gross = overall

    # 4. Annual fee and waiver.
    waiver_spend = sum(
        profile.spend(c) * 12 for c in CATEGORIES if c not in rules.fee_waiver_excluded_categories
    )
    fee_waived = rules.fee_waiver_spend_inr is not None and waiver_spend >= rules.fee_waiver_spend_inr
    fee_paid = 0.0 if (fee_waived or rules.annual_fee_inr == 0) else rules.annual_fee_inr * (1 + GST)

    return CardValue(
        card_id=rules.card_id,
        card_name=rules.card_name,
        annual_spend=round(profile.monthly_total() * 12, 2),
        gross_rewards=round(gross, 2),
        fee_paid=round(fee_paid, 2),
        fee_waived=fee_waived,
        net_value=round(gross - fee_paid, 2),
        lines=[
            CategoryLine(
                category=ln["category"],
                annual_spend=round(ln["annual_spend"], 2),
                rate_pct=ln["rate_pct"],
                rule=ln["rule"],
                reward=round(ln["reward"], 2),
            )
            for ln in lines
        ],
        caps_hit=caps_hit,
    )


def rank(profile: SpendProfile, all_rules: list[CardRules]) -> list[CardValue]:
    return sorted((calculate(profile, r) for r in all_rules), key=lambda v: v.net_value, reverse=True)


def effective_category_terms(rules: CardRules, is_prime: bool = True) -> dict[str, dict]:
    """Per-category (rate, annual cap) as the calculator would apply them. Used by the extraction eval."""
    out = {}
    for cat in CATEGORIES:
        if cat in rules.excluded_categories:
            out[cat] = {"rate_pct": 0.0, "annual_cap": 0.0}
            continue
        match = _best_rule(rules, cat, is_prime)
        if match:
            _, r = match
            out[cat] = {"rate_pct": r.rate_pct, "annual_cap": _annual_cap(r.cap_inr, r.cap_period)}
        else:
            out[cat] = {"rate_pct": rules.base_rate_pct, "annual_cap": _annual_cap(rules.base_cap_inr, rules.base_cap_period)}
    return out
