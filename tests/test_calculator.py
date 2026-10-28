"""Calculator tests - run with `pytest`. No API keys needed."""

import pytest

from app.calculator import calculate, rank
from app.schemas import CardRules, RewardRule, SpendProfile


def sbi_like() -> CardRules:
    return CardRules(
        card_id="sbi",
        card_name="SBI-like",
        annual_fee_inr=999,
        fee_waiver_spend_inr=200000,
        reward_rules=[
            RewardRule(categories=["amazon", "other_online"], rate_pct=5, cap_inr=2000, description="online"),
            RewardRule(categories=["offline"], rate_pct=1, cap_inr=2000, description="offline"),
        ],
        excluded_categories=["fuel", "rent"],
        overall_cap_inr=4000,
    )


def test_simple_rates_and_gst_on_fee():
    v = calculate(SpendProfile(amazon=10000, offline=5000), sbi_like())
    # 10000*12*5% = 6000, 5000*12*1% = 600; annual spend 1.8L < 2L waiver -> fee 999 * 1.18
    assert v.gross_rewards == pytest.approx(6600)
    assert v.fee_paid == pytest.approx(999 * 1.18)
    assert not v.fee_waived


def test_category_cap_applies_per_rule_group():
    # online: (50000 + 10000) * 12 * 5% = 36000 > 24000 cap
    v = calculate(SpendProfile(amazon=50000, other_online=10000), sbi_like())
    assert v.gross_rewards == pytest.approx(24000)
    assert any("online" in c for c in v.caps_hit)
    # capped reward is split across the categories in proportion to spend
    amazon = next(ln for ln in v.lines if ln.category == "amazon")
    assert amazon.reward == pytest.approx(20000)


def test_overall_cap():
    rules = sbi_like().model_copy(update={"overall_cap_inr": 1000})
    v = calculate(SpendProfile(amazon=50000, offline=50000), rules)
    assert v.gross_rewards == pytest.approx(12000)


def test_excluded_categories_earn_nothing_but_count_for_waiver():
    v = calculate(SpendProfile(rent=20000), sbi_like())  # 2.4L of rent
    assert v.gross_rewards == 0
    assert v.fee_waived  # this card does not exclude rent from the waiver


def test_waiver_exclusions():
    rules = sbi_like().model_copy(update={"fee_waiver_excluded_categories": ["rent"]})
    v = calculate(SpendProfile(rent=20000), rules)
    assert not v.fee_waived


def test_prime_only_rule_falls_back_for_non_prime():
    rules = CardRules(
        card_id="amz",
        card_name="Amazon-like",
        annual_fee_inr=0,
        reward_rules=[
            RewardRule(categories=["amazon"], rate_pct=5, requires_prime=True),
            RewardRule(categories=["amazon"], rate_pct=3),
        ],
        base_rate_pct=1,
    )
    assert calculate(SpendProfile(amazon=1000, amazon_prime_member=True), rules).gross_rewards == pytest.approx(600)
    assert calculate(SpendProfile(amazon=1000), rules).gross_rewards == pytest.approx(360)


def test_quarterly_cap_is_annualised():
    rules = CardRules(
        card_id="fk",
        card_name="Flipkart-like",
        annual_fee_inr=0,
        reward_rules=[RewardRule(categories=["flipkart"], rate_pct=5, cap_inr=1000, cap_period="quarter")],
    )
    v = calculate(SpendProfile(flipkart=20000), rules)  # 12000 uncapped vs 4000 cap
    assert v.gross_rewards == pytest.approx(4000)


def test_rank_orders_by_net_value():
    free = CardRules(card_id="free", card_name="Free 1%", annual_fee_inr=0, base_rate_pct=1)
    ranking = rank(SpendProfile(offline=10000), [sbi_like(), free])
    assert [v.card_id for v in ranking] == ["free", "sbi"]


def test_zero_cap_means_uncapped():
    rules = CardRules(card_id="z", card_name="Zero cap", annual_fee_inr=0, base_rate_pct=1,
                      base_cap_inr=0, overall_cap_inr=0)
    assert calculate(SpendProfile(offline=10000), rules).gross_rewards == pytest.approx(1200)
