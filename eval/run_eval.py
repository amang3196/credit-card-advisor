"""Evaluation: plain RAG baseline vs the agentic pipeline.

  python -m eval.run_eval --suite extraction   # rule extraction accuracy vs gold rules (no baseline)
  python -m eval.run_eval --suite recommend    # top pick + rupee error per persona, crew vs baseline
  python -m eval.run_eval --suite qa           # RAGAS on rule questions, agent vs baseline
  python -m eval.run_eval --suite all

Options: --limit N (only N questions/personas), --refresh-rules, --with-profiler

Gold rules in eval/gold_rules/ are written by hand from the PDFs. Expected rupee values for each
persona are computed from them with the same calculator, so the recommend suite measures
"did the system get the rules right", not "did it do arithmetic the same way".
"""

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from app import usage
from app.calculator import calculate, effective_category_terms
from app.schemas import CATEGORIES, CardRules, SpendProfile, render_spend_text

EVAL_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVAL_DIR / "results"


def _ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def load_gold() -> dict[str, CardRules]:
    gold, drafts = {}, []
    for path in sorted((EVAL_DIR / "gold_rules").glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if raw.get("_status", "").upper() != "VERIFIED":
            drafts.append(path.stem)
        gold[raw["card_id"]] = CardRules(**{k: v for k, v in raw.items() if not k.startswith("_")})
    if drafts:
        print(f"WARNING: gold rules still marked DRAFT for {', '.join(drafts)}. Verify them against the PDFs.\n")
    return gold


def load_personas(limit: int | None) -> list[dict]:
    personas = json.loads((EVAL_DIR / "personas.json").read_text(encoding="utf-8"))
    return personas[:limit] if limit else personas


def persona_profile(p: dict) -> SpendProfile:
    return SpendProfile(**p["spend"], amazon_prime_member=p.get("amazon_prime_member", False))


def _save(name: str, payload: dict) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    path = RESULTS_DIR / f"{name}_{_ts()}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def _close(a, b, tol: float = 1.0) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


# ------------------------------------------------------------------ extraction


def compare_rules(pred: CardRules, gold: CardRules) -> list[dict]:
    """Field-level comparison on what actually changes the maths, not on how the JSON is shaped."""
    checks = [
        ("annual_fee_inr", gold.annual_fee_inr, pred.annual_fee_inr),
        ("fee_waiver_spend_inr", gold.fee_waiver_spend_inr, pred.fee_waiver_spend_inr),
    ]
    gp, pp = effective_category_terms(gold, True), effective_category_terms(pred, True)
    gn, pn = effective_category_terms(gold, False), effective_category_terms(pred, False)
    for cat in CATEGORIES:
        checks.append((f"{cat}.rate_pct", gp[cat]["rate_pct"], pp[cat]["rate_pct"]))
        checks.append((f"{cat}.annual_cap", gp[cat]["annual_cap"], pp[cat]["annual_cap"]))
    checks.append(("amazon.rate_pct_non_prime", gn["amazon"]["rate_pct"], pn["amazon"]["rate_pct"]))

    def overall(r: CardRules):
        from app.calculator import _annual_cap

        return _annual_cap(r.overall_cap_inr, r.overall_cap_period)

    checks.append(("overall_annual_cap", overall(gold), overall(pred)))
    # Rupee amounts may differ by rounding (±1); percentages must match exactly (±0.01).
    return [
        {"field": f, "gold": g, "pred": p, "ok": _close(g, p, tol=0.01 if f.endswith("rate_pct") or "rate_pct_" in f else 1.0)}
        for f, g, p in checks
    ]


def suite_extraction(refresh: bool) -> dict:
    from app.crew import extract_card_rules

    gold = load_gold()
    per_card, all_ok = {}, []
    for card_id, g in gold.items():
        t = time.time()
        pred, _ = extract_card_rules(card_id, refresh=refresh)
        rows = compare_rules(pred, g)
        acc = sum(r["ok"] for r in rows) / len(rows)
        per_card[card_id] = {"accuracy": round(acc, 3), "seconds": round(time.time() - t, 1),
                             "mismatches": [r for r in rows if not r["ok"]]}
        all_ok += [r["ok"] for r in rows]
        print(f"{card_id:20s} field accuracy {acc:.0%}  ({len(per_card[card_id]['mismatches'])} mismatches)")
    overall = sum(all_ok) / len(all_ok)
    print(f"\nOverall field-level accuracy: {overall:.1%}")
    return {"overall_field_accuracy": round(overall, 3), "per_card": per_card}


# ------------------------------------------------------------------ recommend


def _value_metrics(rows: list[dict]) -> dict:
    """rows: one per persona with gold_top, pred_top, errors (list of abs rupee errors per card), gold_values."""
    errors = [e for r in rows for e in r["abs_errors"]]
    within = [e <= max(250, 0.10 * abs(gv)) for r in rows for e, gv in zip(r["abs_errors"], r["gold_values"])]
    return {
        "top_pick_accuracy": round(sum(r["gold_top"] == r["pred_top"] for r in rows) / len(rows), 3),
        # Regret: Rs/yr lost by taking the system's pick instead of the true best card. Robust to near-ties.
        "mean_regret_inr": round(statistics.mean(r["regret"] for r in rows), 0),
        "mean_abs_error_inr": round(statistics.mean(errors), 0) if errors else None,
        "within_10pct_share": round(sum(within) / len(within), 3) if within else None,
    }


def suite_recommend(limit: int | None, refresh: bool, with_profiler: bool, systems: list[str]) -> dict:
    from app.baseline import baseline_recommend
    from app.crew import extract_card_rules, profile_spend

    gold = load_gold()
    card_ids = list(gold)
    crew_rules = {cid: extract_card_rules(cid, refresh=refresh)[0] for cid in card_ids} if "crew" in systems else {}

    results = {s: [] for s in systems}
    for p in load_personas(limit):
        profile = persona_profile(p)
        gold_vals = {cid: calculate(profile, gold[cid]).net_value for cid in card_ids}
        gold_top = max(gold_vals, key=gold_vals.get)
        print(f"\n{p['id']}: gold top pick = {gold_top} (Rs {gold_vals[gold_top]:,.0f}/yr)")

        if "crew" in systems:
            prof = profile_spend(p["description"]) if with_profiler else profile
            pred = {cid: calculate(prof, crew_rules[cid]).net_value for cid in card_ids}
            results["crew"].append(_row(p["id"], gold_vals, gold_top, pred))
            print(f"  crew     -> {results['crew'][-1]['pred_top']}")

        if "baseline" in systems:
            text = p["description"] if with_profiler else render_spend_text(profile)
            try:
                rec = baseline_recommend(text)["recommendation"]
                pred = {c["card_id"]: c["net_annual_value_inr"] for c in rec["cards"]}
                pred_top = rec["top_pick_card_id"]
            except Exception as e:
                print(f"  baseline failed: {e}")
                pred, pred_top = {}, None
            results["baseline"].append(_row(p["id"], gold_vals, gold_top, pred, pred_top))
            print(f"  baseline -> {results['baseline'][-1]['pred_top']}")

    summary = {s: _value_metrics(rows) for s, rows in results.items() if rows}
    print("\n" + json.dumps(summary, indent=2))
    return {"summary": summary, "rows": results}


def _row(pid: str, gold_vals: dict, gold_top: str, pred: dict, pred_top: str | None = None) -> dict:
    pred_top = pred_top or (max(pred, key=pred.get) if pred else None)
    # A card the system forgot to value counts as a full miss (error = |gold value|).
    abs_errors = [abs(pred.get(cid, 0) - gv) for cid, gv in gold_vals.items()]
    best = gold_vals[gold_top]
    regret = best - gold_vals[pred_top] if pred_top in gold_vals else best
    return {
        "persona": pid,
        "gold_top": gold_top,
        "pred_top": pred_top,
        "regret": round(regret, 2),
        "gold_values": list(gold_vals.values()),
        "pred_values": [pred.get(cid) for cid in gold_vals],
        "abs_errors": abs_errors,
    }


# ------------------------------------------------------------------ qa (RAGAS)


def suite_qa(limit: int | None, systems: list[str], rescore: str | None = None) -> dict:
    from app.baseline import baseline_answer
    from app.crew import answer_question
    from eval.ragas_judge import METRIC_NAMES, score_samples

    questions = json.loads((EVAL_DIR / "rule_questions.json").read_text(encoding="utf-8"))
    questions = questions[:limit] if limit else questions

    samples = {s: [] for s in systems}
    if rescore:  # reuse the answers and contexts from an earlier run; only re-run the RAGAS judge
        old = json.loads(Path(rescore).read_text(encoding="utf-8"))["qa"]["samples"]
        samples = {s: [{k: v for k, v in x.items() if k not in ("scores", "errors")} for x in old[s]]
                   for s in systems if s in old}
        questions = []
        print(f"Re-scoring answers from {rescore}")
    for q in questions:
        print(f"{q['id']}: {q['question']}")
        for s in systems:
            t = time.time()
            try:
                out = baseline_answer(q["question"]) if s == "baseline" else answer_question(q["question"])
            except Exception as e:
                out = {"answer": f"ERROR: {e}", "contexts": []}
            samples[s].append({**q, "answer": out["answer"], "contexts": out["contexts"],
                               "seconds": round(time.time() - t, 1)})

    summary = {}
    for s in systems:
        print(f"\nScoring {s} with RAGAS ({len(samples[s])} samples)...")
        scored = score_samples(samples[s])
        for sample, sc in zip(samples[s], scored):
            sample.update(sc)
        summary[s] = {}
        for m in METRIC_NAMES:
            vals = [x["scores"][m] for x in samples[s] if x["scores"].get(m) is not None]
            summary[s][m] = round(statistics.mean(vals), 3) if vals else None
            summary[s][f"{m}_n"] = len(vals)  # how many samples the score is based on
            if len(vals) < len(samples[s]):
                print(f"  WARNING: {s} {m} scored on {len(vals)}/{len(samples[s])} samples (judge errors) - "
                      f"re-run with --rescore to fill the gaps")
        summary[s]["avg_seconds"] = round(statistics.mean(x["seconds"] for x in samples[s]), 1)
    print("\n" + json.dumps(summary, indent=2))
    return {"summary": summary, "samples": samples}


# ------------------------------------------------------------------ report


def write_markdown(new_report: dict) -> Path:
    """Merge with the latest results of the other suites so summary.md always shows all three."""
    RESULTS_DIR.mkdir(exist_ok=True)
    latest_path = RESULTS_DIR / "latest.json"
    report = json.loads(latest_path.read_text(encoding="utf-8")) if latest_path.exists() else {}
    report.update(new_report)
    latest_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    fmt = lambda x, spec=".2f": "n/a" if x is None else format(x, spec)
    tag = (report.get("_meta") or {}).get("tag")
    lines = [f"# Evaluation results{f' - {tag}' if tag else ''} ({datetime.now():%Y-%m-%d %H:%M})", ""]
    if "extraction" in report:
        ex = report["extraction"]
        lines += ["## Rule extraction (Rules Researcher vs hand-checked gold rules)", "",
                  f"Overall field-level accuracy: **{ex['overall_field_accuracy']:.0%}**", "",
                  "| Card | Field accuracy | Mismatches |", "|---|---|---|"]
        lines += [f"| {cid} | {v['accuracy']:.0%} | {len(v['mismatches'])} |" for cid, v in ex["per_card"].items()]
        lines.append("")
    if "recommend" in report:
        lines += ["## Recommendation quality per persona", "",
                  "| System | Top pick accuracy | Mean regret (Rs/yr) | Mean abs error (Rs/yr per card) | Estimates within 10% |",
                  "|---|---|---|---|---|"]
        for s, m in report["recommend"]["summary"].items():
            lines.append(f"| {s} | {fmt(m['top_pick_accuracy'], '.0%')} | {fmt(m.get('mean_regret_inr'), ',.0f')} | "
                         f"{fmt(m['mean_abs_error_inr'], ',.0f')} | "
                         f"{fmt(m['within_10pct_share'], '.0%')} |")
        lines.append("")
    if "qa" in report:
        lines += ["## Rule questions (RAGAS)", "",
                  "| System | Faithfulness | Context recall | Context precision | Factual correctness | Avg seconds |",
                  "|---|---|---|---|---|---|"]
        for s, m in report["qa"]["summary"].items():
            lines.append(f"| {s} | {fmt(m['faithfulness'])} | {fmt(m['context_recall'])} | "
                         f"{fmt(m['context_precision'])} | {fmt(m['factual_correctness'])} | {m['avg_seconds']} |")
        lines.append("")
    costs = []
    for name in ("extraction", "recommend", "qa"):
        u = (report.get(name) or {}).get("usage")
        if u:
            t = u["total"]
            cost = "n/a" if t.get("est_cost_usd") is None else f"${t['est_cost_usd']:.4f}"
            costs.append(f"| {name} | {t['requests']} | {t['prompt_tokens']:,} | {t['completion_tokens']:,} | {cost} |")
    if costs:
        lines += ["## Cost of this run (estimated)", "",
                  "| Suite | LLM calls | Input tokens | Output tokens | Est. cost (USD) |", "|---|---|---|---|---|", *costs, ""]
    path = RESULTS_DIR / "summary.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=["extraction", "recommend", "qa", "all"], default="all")
    ap.add_argument("--limit", type=int, default=None, help="only the first N personas / questions")
    ap.add_argument("--refresh-rules", action="store_true", help="re-run the Rules Researcher instead of using the cache")
    ap.add_argument("--with-profiler", action="store_true", help="recommend suite: start from free text (tests the Spend Profiler too)")
    ap.add_argument("--systems", default="crew,baseline", help="comma-separated: crew,baseline")
    ap.add_argument("--rescore", default=None,
                    help="qa suite: path to an earlier qa_*.json; re-runs only the RAGAS judge on its answers")
    ap.add_argument("--tag", default=None,
                    help="label for this run, e.g. 'v1_pymupdf_fixed'. Results go to eval/results/<tag>/ so runs can be compared")
    args = ap.parse_args()

    global RESULTS_DIR
    if args.tag:
        RESULTS_DIR = EVAL_DIR / "results" / args.tag
    systems = [s.strip() for s in args.systems.split(",")]

    report = {}
    qa_systems = ["agent" if s == "crew" else s for s in systems]
    suites = {
        "extraction": lambda: suite_extraction(args.refresh_rules),
        "recommend": lambda: suite_recommend(args.limit, args.refresh_rules and args.suite != "all",
                                             args.with_profiler, systems),
        "qa": lambda: suite_qa(args.limit, qa_systems, args.rescore),
    }
    for name, run in suites.items():
        if args.suite not in (name, "all"):
            continue
        with usage.collect() as u:  # tokens + estimated cost of every LLM call in this suite
            report[name] = run()
        report[name]["usage"] = u.summary()
        print(f"\n[usage] {name}: {usage.fmt(report[name]['usage'])}")

    report["_meta"] = _run_meta(args)
    print(f"\nSaved {_save(args.suite, report)}")
    print(f"Summary table: {write_markdown(report)}")
    if args.tag:  # the UI reads eval/results/latest.json, so mirror the tagged results there
        root = EVAL_DIR / "results" / "latest.json"
        root.write_text((RESULTS_DIR / "latest.json").read_text(encoding="utf-8"), encoding="utf-8")


def _index_info() -> dict:
    """What the Pinecone index was built with (written by app.ingest)."""
    from app.config import settings

    path = settings.data_dir / "index_info.json"
    return json.loads(path.read_text()) if path.exists() else {"chunker": "unknown"}


def _run_meta(args) -> dict:
    from app.config import settings

    return {
        "tag": args.tag,
        "suite": args.suite,
        "limit": args.limit,
        "refresh_rules": args.refresh_rules,
        "llm_model": settings.llm_model,
        "eval_llm_model": settings.eval_llm_model,
        "top_k": settings.top_k,
        "researcher_prompt": settings.researcher_prompt,
        "index": _index_info(),
        "run_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    main()
