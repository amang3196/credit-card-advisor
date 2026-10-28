"""Streamlit front end for the Credit Card Fit Advisor.

Run (with the API already running on :8000):
    streamlit run ui/app.py
"""

import json
import sys
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_client as api  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
LATEST_EVAL = ROOT / "eval" / "results" / "latest.json"

st.set_page_config(page_title="Card Fit Advisor", page_icon="💳", layout="wide")

CATEGORIES = {
    "amazon": "Amazon",
    "flipkart": "Flipkart",
    "swiggy": "Swiggy",
    "zomato": "Zomato / Blinkit",
    "other_online": "Other online",
    "offline": "Shops & restaurants",
    "travel": "Flights & hotels",
    "fuel": "Fuel",
    "utilities": "Bills (utilities)",
    "insurance": "Insurance",
    "rent": "Rent",
}

PRESETS = {
    "Amazon Prime shopper": "I shop a lot on Amazon, around 12k a month, and I have Prime. About 2,000 on Swiggy, "
    "8,000 at local shops, 3k on electricity and phone bills and 3k on petrol.",
    "Food delivery lover": "Most of my money goes on food apps: 8,000 a month on Swiggy and 3,000 on Zomato. "
    "Another 4k on Myntra and Nykaa and 6k in shops. No Prime.",
    "Rent + big spender": "I pay my 30k rent by credit card. 25k a month in shops and restaurants, 15k other online "
    "shopping, 5k on Flipkart, about 72k a year on flights and hotels, 4k bills and 5k fuel.",
    "Flipkart + stores": "Flipkart is my main shopping site, about 10k a month. I spend 15k in stores, 4k on fuel "
    "and pay 24k a year in insurance premiums.",
}

EXAMPLE_QUESTIONS = [
    "What is the maximum cashback per statement cycle on the Cashback SBI Card?",
    "How much cashback does the Swiggy BLCK HDFC card give on Swiggy, and is there a cap?",
    "What is the difference in annual fee between the Swiggy Ornge and Swiggy BLCK HDFC cards?",
    "How much do I need to spend to get the annual fee waived on the Flipkart Axis Bank card?",
    "Do rent payments earn cashback on HDFC Millennia?",
    "What cashback does the Amazon Pay ICICI card give on Amazon for Prime and non-Prime members?",
]

TOP_COLOR, OTHER_COLOR = "#2f7d5b", "#9aa5b1"


def inr(x) -> str:
    return "–" if x is None else f"₹{x:,.0f}"


def show_error(e: Exception):
    st.error(str(e), icon="⚠️")


@st.cache_data(ttl=20, show_spinner=False)
def load_cards():
    return api.cards()


def card_names() -> dict:
    try:
        return {c["card_id"]: c["card_name"] for c in load_cards()}
    except api.ApiError:
        return {}


# ------------------------------------------------------------------ charts


def net_value_chart(rows: list[dict], value_key: str = "net_value", title: str = "Net value per year"):
    df = pd.DataFrame([{"Card": r["card_name"], "Net ₹/yr": r[value_key]} for r in rows])
    top = df["Net ₹/yr"].max()
    return (
        alt.Chart(df, title=title)
        .mark_bar(cornerRadiusEnd=3)
        .encode(
            x=alt.X("Net ₹/yr:Q", axis=alt.Axis(format=",.0f")),
            y=alt.Y("Card:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
            color=alt.condition(alt.datum["Net ₹/yr"] == top, alt.value(TOP_COLOR), alt.value(OTHER_COLOR)),
            tooltip=["Card", alt.Tooltip("Net ₹/yr:Q", format=",.0f")],
        )
        .properties(height=36 * len(df) + 40)
    )


def ranking_table(ranking: list[dict]):
    df = pd.DataFrame(
        [
            {
                "#": i,
                "Card": v["card_name"],
                "Rewards ₹/yr": v["gross_rewards"],
                "Fee paid ₹/yr": v["fee_paid"],
                "Fee waived": "yes" if v["fee_waived"] else "",
                "Net ₹/yr": v["net_value"],
                "Caps hit": len(v["caps_hit"]),
            }
            for i, v in enumerate(ranking, 1)
        ]
    )
    money = st.column_config.NumberColumn(format="₹%,.0f")
    st.dataframe(
        df,
        hide_index=True,
        width="stretch",
        column_config={"Rewards ₹/yr": money, "Fee paid ₹/yr": money, "Net ₹/yr": money},
    )


def breakdown_table(card_value: dict):
    df = pd.DataFrame(
        [
            {
                "Category": CATEGORIES.get(ln["category"], ln["category"]),
                "Spend ₹/yr": ln["annual_spend"],
                "Rate %": ln["rate_pct"],
                "Rule applied": ln["rule"],
                "Reward ₹/yr": ln["reward"],
            }
            for ln in card_value["lines"]
        ]
    )
    money = st.column_config.NumberColumn(format="₹%,.0f")
    st.dataframe(df, hide_index=True, width="stretch",
                 column_config={"Spend ₹/yr": money, "Reward ₹/yr": money})


def _usage_caption(res: dict):
    u = res.get("usage")
    if u:
        cost = "" if u.get("est_cost_usd") is None else f" · ~${u['est_cost_usd']:.4f}"
        st.caption(f"{u['requests']} LLM calls · {u['prompt_tokens']:,} in / {u['completion_tokens']:,} out tokens{cost}")


def passages_expander(contexts: list[str], label: str = "Retrieved passages"):
    with st.expander(f"{label} ({len(contexts)})"):
        if not contexts:
            st.caption("No passages.")
        for c in contexts:
            st.markdown(f"<div style='font-size:0.85rem;border-left:3px solid #ccc;padding-left:8px;margin-bottom:10px'>{c}</div>",
                        unsafe_allow_html=True)


# ------------------------------------------------------------------ sidebar


def sidebar():
    with st.sidebar:
        st.markdown("### 💳 Card Fit Advisor")
        st.caption("CrewAI · Pinecone · FastAPI · RAGAS")
        try:
            h = api.health()
            st.success(f"API connected · `{h['llm_model']}`", icon="✅")
            if not (h.get("llm_key_set", h.get("gemini_key_set")) and h["pinecone_key_set"]):
                st.warning("A key is missing in .env")
        except api.ApiError as e:
            st.error(str(e))
            return

        st.markdown("**Cards**")
        try:
            for c in load_cards():
                docs = c["documents"]
                present = sum(d["present"] for d in docs)
                rules = "rules cached" if c["rules_cached"] else "rules not extracted yet"
                st.markdown(f"- **{c['card_name']}**  \n  <small>{present}/{len(docs)} PDFs · {rules}</small>",
                            unsafe_allow_html=True)
        except api.ApiError as e:
            show_error(e)
        if st.button("Refresh status", width="stretch"):
            load_cards.clear()
            st.rerun()


# ------------------------------------------------------------------ tab: recommend


def render_crew(res: dict, compact: bool = False):
    ranking, advice = res["ranking"], res.get("advice") or {}
    top, second = ranking[0], ranking[1] if len(ranking) > 1 else None

    st.success(f"**Best fit: {top['card_name']}** · net {inr(top['net_value'])} a year", icon="🏆")
    c1, c2, c3 = st.columns(3)
    c1.metric("Net value / yr", inr(top["net_value"]))
    c2.metric("Ahead of #2 by", inr(top["net_value"] - second["net_value"]) if second else "–")
    c3.metric("Response time", f"{res.get('seconds', 0):.0f}s")
    _usage_caption(res)

    if advice.get("summary"):
        st.markdown(f"> {advice['summary']}")

    if not compact:
        st.altair_chart(net_value_chart(ranking), width="stretch")
    ranking_table(ranking)

    advice_by_card = {a["card_id"]: a for a in advice.get("cards", [])}
    st.markdown("#### Card by card")
    for i, v in enumerate(ranking, 1):
        a = advice_by_card.get(v["card_id"], {})
        with st.expander(f"{i}. {v['card_name']} · {inr(v['net_value'])}/yr", expanded=(i == 1 and not compact)):
            if a.get("verdict"):
                st.markdown(a["verdict"])
            if a.get("watch_outs"):
                st.markdown("**Watch out for**")
                for w in a["watch_outs"]:
                    st.markdown(f"- {w}")
            breakdown_table(v)
            for cap in v["caps_hit"]:
                st.caption(f"⚠️ {cap}")
            if a.get("citations"):
                st.caption("Sources: " + " · ".join(a["citations"]))

    with st.expander("What the Spend Profiler understood"):
        prof = res.get("spend_profile") or {}
        df = pd.DataFrame(
            [{"Category": CATEGORIES[k], "₹ / month": prof.get(k, 0)} for k in CATEGORIES if prof.get(k)]
        )
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"₹ / month": st.column_config.NumberColumn(format="₹%,.0f")})
        st.caption(f"Amazon Prime: {'yes' if prof.get('amazon_prime_member') else 'no'}")
        for a in prof.get("assumptions", []):
            st.caption(f"• {a}")
        for a in advice.get("assumptions", []):
            st.caption(f"• {a}")

    with st.expander("Pipeline timings"):
        t = res.get("timings", {})
        st.dataframe(pd.DataFrame([{"Step": k, "Seconds": v} for k, v in t.items()]), hide_index=True)
    passages_expander(res.get("contexts", []), "Passages the agents read")


def render_baseline(res: dict, compact: bool = False):
    rec, names = res["recommendation"], card_names()
    rows = [{**c, "card_name": names.get(c["card_id"], c["card_id"])} for c in rec["cards"]]
    rows.sort(key=lambda r: r["net_annual_value_inr"], reverse=True)
    top_name = names.get(rec["top_pick_card_id"], rec["top_pick_card_id"])

    st.info(f"**Baseline pick: {top_name}**", icon="📄")
    c1, c2 = st.columns(2)
    c1.metric("Claimed net value / yr", inr(rows[0]["net_annual_value_inr"]) if rows else "–")
    c2.metric("Response time", f"{res.get('seconds', 0):.0f}s")
    _usage_caption(res)
    if rec.get("summary"):
        st.markdown(f"> {rec['summary']}")
    if rows and not compact:
        st.altair_chart(net_value_chart(rows, "net_annual_value_inr", "Net value per year (LLM estimate)"),
                        width="stretch")
    if rows:
        money = st.column_config.NumberColumn(format="₹%,.0f")
        st.dataframe(
            pd.DataFrame([{"Card": r["card_name"], "Rewards ₹/yr": r["estimated_annual_rewards_inr"],
                           "Fee ₹/yr": r["annual_fee_inr"], "Net ₹/yr": r["net_annual_value_inr"],
                           "Reasoning": r.get("reasoning", "")} for r in rows]),
            hide_index=True, width="stretch",
            column_config={"Rewards ₹/yr": money, "Fee ₹/yr": money, "Net ₹/yr": money},
        )
    passages_expander(res.get("contexts", []), "Passages the baseline read")


def compare_chart(crew: dict, base: dict):
    names = card_names()
    crew_vals = {v["card_id"]: v["net_value"] for v in crew["ranking"]}
    base_vals = {c["card_id"]: c["net_annual_value_inr"] for c in base["recommendation"]["cards"]}
    rows = []
    for cid in crew_vals:
        rows.append({"Card": names.get(cid, cid), "System": "Agents + calculator", "Net ₹/yr": crew_vals[cid]})
        if cid in base_vals:
            rows.append({"Card": names.get(cid, cid), "System": "Plain RAG", "Net ₹/yr": base_vals[cid]})
    df = pd.DataFrame(rows)
    order = [names.get(cid, cid) for cid in sorted(crew_vals, key=crew_vals.get, reverse=True)]
    return (
        alt.Chart(df, title="Same spend, two systems")
        .mark_bar()
        .encode(
            x=alt.X("Net ₹/yr:Q", axis=alt.Axis(format=",.0f")),
            y=alt.Y("Card:N", title=None, sort=order, axis=alt.Axis(labelLimit=260)),
            yOffset="System:N",
            color=alt.Color("System:N", scale=alt.Scale(range=[TOP_COLOR, "#c58b2a"]), legend=alt.Legend(orient="top")),
            tooltip=["Card", "System", alt.Tooltip("Net ₹/yr:Q", format=",.0f")],
        )
        .properties(height=60 * len(crew_vals) + 40)
    )


def tab_recommend():
    st.markdown("Describe your monthly spending in plain English. The agents read the bank T&Cs, "
                "a calculator values each card, and the advisor explains the fine print.")

    if "spend_text" not in st.session_state:
        st.session_state.spend_text = PRESETS["Rent + big spender"]

    def _use_preset():
        if st.session_state.preset:
            st.session_state.spend_text = PRESETS[st.session_state.preset]

    st.pills("Try an example", list(PRESETS), key="preset", on_change=_use_preset)
    st.text_area("Your spending", key="spend_text", height=110)

    c1, c2 = st.columns([3, 1])
    mode = c1.radio("Mode", ["Agents (crew)", "Plain RAG (baseline)", "Compare both"], horizontal=True)
    refresh = c2.checkbox("Re-extract card rules", help="Ignore the rules cache. Slow: re-reads every card's PDFs.")

    if st.button("Find my best card", type="primary"):
        results = {}
        try:
            if mode in ("Plain RAG (baseline)", "Compare both"):
                with st.spinner("Baseline: one search + one LLM call..."):
                    results["baseline"] = api.recommend(st.session_state.spend_text, "baseline")
            if mode in ("Agents (crew)", "Compare both"):
                msg = "Agents at work: profiling spend → reading card rules → calculating → checking fine print..."
                if refresh:
                    msg += " (re-extracting rules, this takes a few minutes)"
                with st.spinner(msg):
                    results["crew"] = api.recommend(st.session_state.spend_text, "crew", refresh)
                load_cards.clear()
        except api.ApiError as e:
            show_error(e)
        st.session_state.rec_results = results

    results = st.session_state.get("rec_results") or {}
    if "crew" in results and "baseline" in results:
        st.altair_chart(compare_chart(results["crew"], results["baseline"]), width="stretch")
        left, right = st.columns(2)
        with left:
            st.markdown("### 🤖 Agents + calculator")
            render_crew(results["crew"], compact=True)
        with right:
            st.markdown("### 📄 Plain RAG baseline")
            render_baseline(results["baseline"], compact=True)
    elif "crew" in results:
        render_crew(results["crew"])
    elif "baseline" in results:
        render_baseline(results["baseline"])


# ------------------------------------------------------------------ tab: what-if


def tab_what_if():
    st.markdown("Change any number and the ranking updates instantly. No LLM calls: this runs the calculator "
                "on the card rules the agents already extracted.")

    last = (st.session_state.get("rec_results") or {}).get("crew", {}).get("spend_profile")
    if last and st.button("Load spend from my last recommendation"):
        for k in CATEGORIES:
            st.session_state[f"wi_{k}"] = int(last.get(k, 0))
        st.session_state.wi_prime = bool(last.get("amazon_prime_member"))

    defaults = {"amazon": 5000, "swiggy": 3000, "offline": 10000, "utilities": 3000, "fuel": 3000}
    cols = st.columns(4)
    profile = {}
    for i, (k, label) in enumerate(CATEGORIES.items()):
        key = f"wi_{k}"
        if key not in st.session_state:
            st.session_state[key] = defaults.get(k, 0)
        profile[k] = cols[i % 4].number_input(f"{label} (₹/month)", min_value=0, step=500, key=key)
    if "wi_prime" not in st.session_state:
        st.session_state.wi_prime = True
    profile["amazon_prime_member"] = cols[3].checkbox("Amazon Prime member", key="wi_prime")

    try:
        ranking = api.calculate(profile)
    except api.ApiError as e:
        show_error(e)
        return

    total = sum(v for k, v in profile.items() if k in CATEGORIES)
    st.caption(f"Total spend: {inr(total)}/month · {inr(total * 12)}/year")
    left, right = st.columns([3, 2])
    with left:
        st.altair_chart(net_value_chart(ranking), width="stretch")
    with right:
        top = ranking[0]
        st.caption("Best card for these spends")
        st.markdown(f"### {top['card_name']}")
        st.markdown(f"**{inr(top['net_value'])}** a year net · rewards {inr(top['gross_rewards'])} − fee {inr(top['fee_paid'])}")
        for cap in top["caps_hit"]:
            st.caption(f"⚠️ {cap}")
        excluded = [CATEGORIES[ln["category"]] for ln in top["lines"] if ln["rule"] == "excluded"]
        if excluded:
            st.caption("Earns nothing on: " + ", ".join(excluded))
    ranking_table(ranking)
    with st.expander(f"Breakdown for {ranking[0]['card_name']}"):
        breakdown_table(ranking[0])


# ------------------------------------------------------------------ tab: ask


def render_answer(res: dict):
    st.markdown(res["answer"])
    st.caption(f"{res.get('seconds', 0):.0f}s · sources: " + (" · ".join(res.get("sources", [])) or "none"))
    _usage_caption(res)
    passages_expander(res.get("contexts", []))


def tab_ask():
    st.markdown("Ask anything about the cards' terms. The agent picks the right card, searches its documents "
                "as many times as it needs and cites pages. The baseline does one search.")

    def _use_example():
        if st.session_state.example_q:
            st.session_state.question = st.session_state.example_q

    st.selectbox("Example questions", [""] + EXAMPLE_QUESTIONS, key="example_q", on_change=_use_example)
    question = st.text_input("Your question", key="question")
    mode = st.radio("Mode", ["Agent", "Baseline", "Both"], horizontal=True, key="ask_mode")

    if st.button("Ask", type="primary", disabled=not question.strip()):
        out = {}
        try:
            if mode in ("Agent", "Both"):
                with st.spinner("Agent searching the documents..."):
                    out["agent"] = api.ask(question, "agent")
            if mode in ("Baseline", "Both"):
                with st.spinner("Baseline: one search + one LLM call..."):
                    out["baseline"] = api.ask(question, "baseline")
        except api.ApiError as e:
            show_error(e)
        st.session_state.ask_results = out

    out = st.session_state.get("ask_results") or {}
    if len(out) == 2:
        left, right = st.columns(2)
        with left:
            st.markdown("### 🤖 Agent")
            render_answer(out["agent"])
        with right:
            st.markdown("### 📄 Baseline")
            render_answer(out["baseline"])
    elif len(out) == 1:
        render_answer(next(iter(out.values())))


# ------------------------------------------------------------------ tab: card rules


def tab_rules():
    st.markdown("What the Rules Researcher extracted from each card's PDFs, with the exact passages it used. "
                "This is the input to the calculator.")
    names = card_names()
    if not names:
        st.info("Start the API to see cards.")
        return
    card_id = st.selectbox("Card", list(names), format_func=lambda c: names[c])

    try:
        cached = api.get_rules(card_id)
    except api.ApiError:
        cached = None

    if st.button("Re-extract rules for this card", help="Runs the Rules Researcher again (1-4 minutes)"):
        try:
            with st.spinner(f"Rules Researcher reading {names[card_id]} documents..."):
                api.refresh_rules(card_id)
            load_cards.clear()
            cached = api.get_rules(card_id)
        except api.ApiError as e:
            show_error(e)

    if not cached:
        st.info("No rules extracted yet for this card. Run a recommendation or click Re-extract.")
        return

    r = cached["rules"]
    st.caption(f"Extracted {cached['extracted_at'][:16].replace('T', ' ')} UTC · {cached['model']} · {cached['seconds']}s")
    c1, c2, c3 = st.columns(3)
    c1.metric("Annual fee (+GST)", inr(r["annual_fee_inr"]))
    c2.metric("Fee waived above", inr(r["fee_waiver_spend_inr"]) if r["fee_waiver_spend_inr"] else "not stated")
    c3.metric(f"Overall cap (per {r['overall_cap_period'].replace('_', ' ')})" if r["overall_cap_inr"] else "Overall cap",
              inr(r["overall_cap_inr"]) if r["overall_cap_inr"] else "none")

    st.markdown("#### Earning rules")
    rows = [
        {
            "Categories": ", ".join(CATEGORIES.get(c, c) for c in rule["categories"]),
            "Rate %": rule["rate_pct"],
            "Cap": f"{inr(rule['cap_inr'])} / {rule['cap_period'].replace('_', ' ')}" if rule["cap_inr"] else "no cap",
            "Prime only": "yes" if rule["requires_prime"] else "",
            "Source": rule["source"],
        }
        for rule in r["reward_rules"]
    ]
    rows.append({
        "Categories": "Everything else (base rate)",
        "Rate %": r["base_rate_pct"],
        "Cap": f"{inr(r['base_cap_inr'])} / {r['base_cap_period'].replace('_', ' ')}" if r["base_cap_inr"] else "no cap",
        "Prime only": "",
        "Source": "",
    })
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    if r["excluded_categories"]:
        st.markdown("**Earns nothing on:** " + ", ".join(CATEGORIES.get(c, c) for c in r["excluded_categories"]))
    if r["fee_waiver_excluded_categories"]:
        st.markdown("**Doesn't count toward fee waiver:** "
                    + ", ".join(CATEGORIES.get(c, c) for c in r["fee_waiver_excluded_categories"]))
    if r["gotchas"]:
        st.markdown("#### Fine print")
        for g in r["gotchas"]:
            st.markdown(f"- {g}")
    st.caption("Sources: " + " · ".join(r["sources"]))
    passages_expander(cached.get("contexts", []), "Passages the Rules Researcher read")
    with st.expander("Raw JSON"):
        st.json(r)


# ------------------------------------------------------------------ tab: evaluation


def tab_eval():
    st.markdown("Results of `python -m eval.run_eval`. Baseline = one search + one LLM call. "
                "Agents = Rules Researcher + calculator. Gold = hand-checked rules from the PDFs.")
    if not LATEST_EVAL.exists():
        st.info("No evaluation results yet. Run, from the project folder:\n\n"
                "```\npython -m eval.run_eval --suite extraction\npython -m eval.run_eval --suite recommend\n"
                "python -m eval.run_eval --suite qa --limit 5\n```")
        return
    report = json.loads(LATEST_EVAL.read_text(encoding="utf-8"))
    st.caption(f"From {LATEST_EVAL.relative_to(ROOT)}")

    if "extraction" in report:
        ex = report["extraction"]
        st.markdown("#### 1 · Rule extraction accuracy")
        st.metric("Field-level accuracy vs gold rules", f"{ex['overall_field_accuracy']:.0%}")
        df = pd.DataFrame([{"Card": cid, "Accuracy": v["accuracy"], "Mismatches": len(v["mismatches"])}
                           for cid, v in ex["per_card"].items()])
        st.dataframe(df, hide_index=True, width="stretch",
                     column_config={"Accuracy": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1)})
        with st.expander("Mismatched fields"):
            for cid, v in ex["per_card"].items():
                for m in v["mismatches"]:
                    st.caption(f"{cid} · {m['field']}: gold {m['gold']} vs extracted {m['pred']}")

    if "recommend" in report:
        st.markdown("#### 2 · Recommendation quality (5 personas)")
        summ = report["recommend"]["summary"]
        df = pd.DataFrame([{"System": s, "Top pick accuracy": m["top_pick_accuracy"],
                            "Mean regret ₹/yr": m.get("mean_regret_inr"), "Mean abs error ₹/yr": m["mean_abs_error_inr"],
                            "Within 10%": m["within_10pct_share"]} for s, m in summ.items()])
        st.dataframe(df, hide_index=True, width="stretch", column_config={
            "Top pick accuracy": st.column_config.NumberColumn(format="percent"),
            "Within 10%": st.column_config.NumberColumn(format="percent"),
            "Mean regret ₹/yr": st.column_config.NumberColumn(format="₹%,.0f"),
            "Mean abs error ₹/yr": st.column_config.NumberColumn(format="₹%,.0f"),
        })
        st.caption("Regret = rupees a year you'd lose by taking the system's pick instead of the truly best card.")

    if "qa" in report:
        st.markdown("#### 3 · Grounded answers (RAGAS)")
        summ = report["qa"]["summary"]
        metrics = ["faithfulness", "context_recall", "context_precision", "factual_correctness"]
        long = pd.DataFrame([{"System": s, "Metric": m.replace("_", " "), "Score": v[m]}
                             for s, v in summ.items() for m in metrics if v.get(m) is not None])
        if not long.empty:
            chart = (
                alt.Chart(long).mark_bar()
                .encode(x=alt.X("Score:Q", scale=alt.Scale(domain=[0, 1])), y=alt.Y("Metric:N", title=None, axis=alt.Axis(labelLimit=200)),
                        yOffset="System:N", color=alt.Color("System:N", legend=alt.Legend(orient="top")),
                        tooltip=["System", "Metric", alt.Tooltip("Score:Q", format=".2f")])
                .properties(height=240)
            )
            st.altair_chart(chart, width="stretch")
        st.dataframe(pd.DataFrame([{"System": s, **v} for s, v in summ.items()]), hide_index=True, width="stretch")


# ------------------------------------------------------------------ layout


sidebar()
st.title("💳 Which credit card actually pays you the most?")
tabs = st.tabs(["🏆 Recommend", "🎛️ What-if", "💬 Ask the T&Cs", "📄 Card rules", "📊 Evaluation"])
with tabs[0]:
    tab_recommend()
with tabs[1]:
    tab_what_if()
with tabs[2]:
    tab_ask()
with tabs[3]:
    tab_rules()
with tabs[4]:
    tab_eval()
