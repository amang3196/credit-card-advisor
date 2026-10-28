# Engineering Log

A running record of every problem found while building and evaluating this project: what the
issue was, why it happened, what was changed, and what the change measurably did.
Newest experiment results are in the **Experiment tracker**; individual fixes are in the **Log**.

Format of each log entry: **Issue → Cause → Change → Result → Files**.

---

## Experiment tracker

All runs use the same gold rules (`eval/gold_rules/`, verified 2026-10-27), the same 5 personas, the
same 13 rule questions (QA suite limited to the first 8) and `TOP_K=6`. Within a run, the agents, the
baseline and the RAGAS judge use the same model. Results for each run live in `eval/results/<tag>/`.

| Tag | Model | What changed | Extraction accuracy | Crew top pick / regret | Baseline top pick / regret | RAGAS (agent) | Est. cost |
|---|---|---|---|---|---|---|---|
| `v1_pymupdf_fixed` | gemini-3.5-flash-lite | Starting setup: PyMuPDF text, 180-word page-bounded chunks, 40-word overlap | **85.9%** | 40% / ₹2,854 | 20% / ₹456 | incomplete (LOG-13) | not tracked (free tier) |
| `v1_pymupdf_fixed_gpt54mini` | gpt-5.4-mini | Same setup, new model (LOG-14) | **74.4%** | 40% / ₹708 | 20% / ₹3,584 | faithfulness 0.99, recall 0.83, precision 0.83, factual 0.52 | $0.50 |
| `v2_pymupdf4llm_sections` | gpt-5.4-mini | pymupdf4llm Markdown, section-aware chunks, tables kept whole, context line per chunk, sections scoped to one card (LOG-19) | 69.2% | 60% / ₹4 (see LOG-20: not a real gain) | 20% / ₹2,908 | faithfulness 0.97, recall 0.96, precision 0.85, factual 0.65 | $0.58 |
| `v2_1_merged_cells` | gpt-5.4-mini | v2 + vertically merged table cells restored (LOG-21) | 66% (Ornge 54%→77%; see LOG-22) | 60% / ₹4 | 20% / ₹3,820 | faithfulness 0.96, recall 0.96, precision 0.85, factual 0.68 | $0.58 |
| `v3_prompt_rules` | gpt-5.4-mini | v2.1 index + Rules Researcher prompt rules for category coverage, base rate, exclusions, fee rows, verification round (LOG-23) | **77%** | **80% / ₹288** (abs error ₹1,172) | 20% / ₹3,724 | faithfulness 0.97, recall 0.96, precision 0.84, factual 0.59 | $0.62 |

Cost per run comes from `eval/results/<tag>/summary.md` ("Cost of this run"), see LOG-16.

The two v1 rows form a **model comparison** on an identical pipeline. v2 and v3 are compared against
the GPT-5.4 mini v1 row, so each change is measured on its own.

**Predictions written before v2 is run** (so the result can confirm or refute them):
- v2 should fix Swiggy Ornge (Amazon/Flipkart 5% row split across a page break) and may fix the SBI
  fee waiver (it sits in an MITC table).
- v2 should **not** fix Flipkart Axis or HDFC Millennia over-generalisation; that is a reasoning
  problem, targeted by v3.
- Added after the GPT-5.4 mini v1 run: v2 should fix the SBI fee (₹499 read from the neighbouring table
  row, LOG-17). It should not fix GPT-5.4 mini's over-exclusion or over-inclusion errors (LOG-18).
- Added when v2 was built (LOG-19): the Swiggy PDF's sections are now tagged with the one card they
  describe, so v2 may also reduce BLCK/Ornge mix-ups (e.g. BLCK's 10% leaking into Ornge). The SBI fee
  waiver (₹2,00,000) should now be read correctly, since its row stays intact.

### v1 analysis (`v1_pymupdf_fixed`)

Extraction (field-level accuracy vs gold, 26 fields per card):

| Card | Accuracy | Main errors |
|---|---|---|
| SBI Cashback | 92% | Missed ₹2 lakh fee waiver (MITC table); travel at 1% instead of 5% |
| HDFC Millennia | 88% | Applied 5% merchant list to all online + travel; waiver not in corpus |
| Swiggy BLCK | 92% | Excluded utilities (they earn 1%) |
| Swiggy Ornge | 65% | Amazon/Flipkart at 1% instead of 5%; excluded utilities + insurance |
| Flipkart Axis | 85% | Myntra 7.5% applied to all other_online; Cleartrip 5% to all travel |
| Amazon Pay ICICI | 92% | Excluded insurance (it earns 1%) |

Recommendation (5 personas):

| | Crew | Baseline |
|---|---|---|
| Top-pick accuracy | 40% | 20% |
| Mean abs error per card | ₹1,794 | ₹3,979 |
| Estimates within 10% | 53% | 7% |
| Mean regret | ₹2,854 | ₹456 |

Reading: the crew's per-card numbers are far more accurate, but one extraction error dominates its
ranking. Flipkart Axis's Myntra rate applied to all online spend made the crew value it at ₹23,100/yr
for the "rent + big spender" persona (true value ₹8,520). Counterfactual check: with only Flipkart
Axis extracted correctly, crew regret drops from **₹2,854 to ₹520**. The baseline's low regret comes
from picking SBI Cashback (a broad, near-best card) in 4 of 5 personas, not from accurate numbers.

---

## Conclusion (2026-10-28)

The project set out to show that an agentic pipeline (CrewAI agents + Pinecone retrieval + deterministic
calculator) recommends credit cards from real bank T&Cs better than plain RAG, and to improve it in
measured steps. Runs on GPT-5.4 mini, same gold rules and personas throughout:

| Run | What changed | Extraction | Crew abs error / card | Crew within 10% | Crew top pick / regret | RAGAS agent recall | Cost |
|---|---|---|---|---|---|---|---|
| v1 | baseline pipeline | 74.4% | ₹2,040 | 40% | 40% / ₹708 | 0.83 | $0.50 |
| v2 | Markdown, section-aware chunks | 69.2% | ₹2,102 | 33% | 60% / ₹4 * | 0.96 | $0.58 |
| v2.1 | + merged table cells | 66% | ₹2,189 | 33% | 60% / ₹4 * | 0.96 | $0.58 |
| v3 | + prompt rules | **77%** | **₹1,172** | **57%** | **80% / ₹288** | 0.96 | $0.62 |
| plain RAG baseline (v3 run) | — | — | ₹6,545 | 7% | 20% / ₹3,724 | 0.96 | — |

\* luck, see LOG-20: the over-valued card changed, the per-card error did not.

What the experiments showed:
1. **Agents + calculator beat plain RAG in every run.** In v3 the crew's per-card error is 5.6× lower
   than the baseline's and its top pick is right 80% vs 20% of the time. Letting code do the arithmetic and
   the agent do targeted, card-filtered retrieval is what makes the difference.
2. **Chunking fixed retrieval, not reasoning.** Markdown parsing with section-aware, table-preserving,
   card-scoped chunks raised context recall from 0.83 to 0.96 (agent) and 0.71 to 0.96–1.00 (baseline),
   and removed the table-row errors. It also introduced one new failure (merged cells), found by reading
   the errors and fixed in v2.1.
3. **Most remaining errors were reasoning slips on passages the agent already had**: which categories a
   rule covers, what "other spends" means, what is really excluded. Six generic prompt rules (v3) fixed 22
   fields and broke 5, and halved the crew's valuation error: the first improvement visible in every
   recommendation metric at once.
4. **Measuring is the hard part.** Top pick and regret moved by luck (v2); one extraction per card swung
   individual cards by 20 points on unchanged chunks (v2.1); RAGAS factual correctness on 8 questions moved
   the baseline from 0.70 to 0.51 in v3 although nothing in the baseline changed. Writing predictions
   before each run and diffing field by field is what kept the conclusions honest.

What is left (not pursued, by choice; the project's goal was the method, not maximum accuracy):
- Repeat runs per configuration to put error bars on extraction accuracy (LOG-22).
- Hybrid (dense + keyword) search, so exact names like "CASHBACK SBI Card" find their fee-table row.
- Corpus gaps: HDFC Millennia's fee page and Flipkart Axis's full T&C (its 1% base rate is only implied).
- An independent judge (Gemini) instead of GPT-5.4 mini grading itself; a held-out set of cards to check
  the v3 prompt rules generalise; a Gemini v3 run (Gemini scored 85.9% on v1 extraction).

---

## Log

### LOG-01 · RAGAS fails to import (2026-10-26)
- **Issue:** `import ragas` raised `ModuleNotFoundError: langchain_community.chat_models.vertexai`.
- **Cause:** ragas 0.4.3 imports a module that `langchain-community` 0.4.2 removed.
- **Change:** pinned `langchain-community==0.4.1` in `requirements.txt` (0.4.1, 0.4 and 0.3.31 all work).
- **Result:** clean install from `requirements.txt` verified in a fresh virtualenv.
- **Files:** `requirements.txt`

### LOG-02 · Ingestion skipped every PDF (2026-10-26)
- **Issue:** `python -m app.ingest` printed `SKIP` for all 10 documents; 0 chunks.
- **Cause:** downloaded PDFs kept the banks' file names; the manifest expected different names.
- **Change:** identified each PDF by reading its first page, then mapped `data/cards.yaml` to the real
  file names, with effective dates taken from the documents.
- **Result:** 9 PDFs ingested, 650 chunks in Pinecone. (Superseded by the rename in LOG-06.)
- **Files:** `data/cards.yaml`

### LOG-03 · Gemini 503 errors and a 660-second response (2026-10-26)
- **Issue:** the first baseline `/ask` failed with `503 UNAVAILABLE: model experiencing high demand`;
  after a retry it answered correctly but took 660 s.
- **Cause:** `gemini-3.8-flash` was overloaded; calls hung or failed with no retry and no timeout.
- **Change:** Gemini client now retries 429/500/502/503/504 (6 attempts, exponential backoff 2→30 s)
  with a 60 s timeout per attempt. Model switched to `gemini-3.5-flash-lite` via `.env`.
- **Result:** answers return normally; the same baseline answer was correct and cited both the SBI
  T&C and the 2026 revision.
- **Files:** `app/llm.py`, `.env`

### LOG-04 · PyMuPDF deprecation warning (2026-10-26)
- **Issue:** `FutureWarning: The fitz API is deprecated`.
- **Change:** `import fitz` → `import pymupdf as fitz`.
- **Files:** `app/ingest.py`

### LOG-05 · No user interface (2026-10-27)
- **Issue:** the MVP was usable only through Swagger, which is hard to demo.
- **Change:** added a Streamlit UI that talks only to the FastAPI backend: Recommend (agents vs
  baseline vs side-by-side), What-if calculator (no LLM calls), Ask the T&Cs, Card rules, Evaluation.
  Tested with a mocked API on real cached rules (no exceptions, screenshots checked).
- **Result:** working UI; `streamlit run ui/app.py`.
- **Files:** `ui/app.py`, `ui/api_client.py`, `.streamlit/config.toml`, `requirements.txt`
- **Note:** `pip install streamlit` timed out on the local network (PyPI read timeout); installed
  offline from wheels instead.

### LOG-06 · Unclear citations (2026-10-27)
- **Issue:** citations such as `mitc-cc-gk-t.pdf p.3` don't say which bank or card they refer to.
- **Change:** renamed all PDFs to `bank_card_doctype_date.pdf`, e.g. `sbi_cashback_revision_2026-04.pdf`.
  Updated the manifest, the cached rule citations (so no re-extraction was needed) and the docs;
  re-ingested with `--reset` (chunk IDs are built from file names).
- **Result:** 650 records after reset (no duplicates); citations are self-explanatory.
- **Files:** `data/pdfs/*`, `data/cards.yaml`, `data/rules_cache/*.json`, `README.md`

### LOG-07 · Gold rules were drafts (2026-10-27)
- **Issue:** the evaluation's answer key was written from web sources before the PDFs were available.
- **Change:** re-derived every gold value from the ingested PDFs with page references (`_sources`),
  then spot-checked by hand and marked `VERIFIED`. Main corrections:
  - Swiggy Ornge: Swiggy and online spends share **one** ₹1,500 cap (sample statement, p.7); travel
    sites earn 5%.
  - Swiggy BLCK: travel earns 5%; no renewal-fee waiver exists.
  - Flipkart Axis: updated to the June 2025 revision; removed a 4% Swiggy rule not stated in any
    ingested PDF; Myntra 7.5% and Cleartrip 5% kept at base rate (single merchants inside a category).
  - Amazon Pay ICICI: utilities excluded since 11 Oct 2025; insurance and travel earn 1%.
- **Known gap:** the Millennia fee and waiver are not in any ingested PDF (taken from Paisabazaar).
- **Files:** `eval/gold_rules/*.json`

### LOG-08 · Eval scoring treated 1% and 0% as equal (2026-10-27)
- **Issue:** utilities showed a cap mismatch but no rate mismatch, even though the rate was wrong.
- **Cause:** every field was compared with a ±1 tolerance, which is right for rupees but makes
  1% vs 0% a "match".
- **Change:** rates now compared with ±0.01; rupee amounts keep ±1.
- **Result:** a preview on the old cached rules went from 89.7% to 84.6%: the earlier number was inflated.
- **Files:** `eval/run_eval.py`

### LOG-09 · "Overall cap = ₹0" zeroed out a card (2026-10-27)
- **Issue:** for Swiggy Ornge the agent wrote `overall_cap_inr: 0` to mean "no overall cap". The
  calculator applied it literally, so every Ornge reward was capped to ₹0.
- **Cause:** the schema allowed 0; a real cap of 0 never happens (non-earning categories go in
  `excluded_categories`).
- **Change:** the calculator treats a cap ≤ 0 as uncapped; the extraction eval uses the same rule; unit
  test added. The agent's field descriptions were deliberately **not** changed, so the agent's
  instructions stay identical between v1 and v2.
- **Result:** v1 extraction re-scored from the same cached rules (no LLM calls): 85.3% → **85.9%**
  (Ornge 62% → 65%); Ornge no longer worth ₹0 in recommendations.
- **Files:** `app/calculator.py`, `eval/run_eval.py`, `tests/test_calculator.py`

### LOG-10 · Benchmark runs could overwrite each other (2026-10-27)
- **Issue:** every run wrote to `eval/results/latest.json`, so "before" numbers would be lost.
- **Change:** `--tag <name>` writes to `eval/results/<tag>/`; each report stores `_meta` (tag, models,
  `TOP_K`, limit, timestamp). The UI's Evaluation tab shows the latest tagged run.
- **Files:** `eval/run_eval.py`

### LOG-11 · Flipkart Axis over-generalisation drives most ranking errors (2026-10-27)
- **Finding:** see "v1 analysis" above. A single wrong rule (Myntra 7.5% → all online) accounts for
  ~80% of crew regret (₹2,854 → ₹520 if only that card were correct).
- **Planned change:** v3 prompt experiment. Kept separate from v2 so each change is measured on its own.

### LOG-12 · Ornge table split across a page break (2026-10-27)
- **Finding:** Table 2.1 in the Swiggy PDF lists "Amazon, Flipkart, Nykaa… all online transactions"
  on the page after its header. Chunks never cross pages, so the row and its meaning (5% cashback)
  are retrieved separately; the agent gave Amazon and Flipkart 1%.
- **Planned change:** v2 (section-aware, table-preserving chunks).

### LOG-13 · RAGAS judge hit the free-tier rate limit (2026-10-27)
- **Issue:** the QA suite produced `null` for all baseline grounding metrics; the agent's scores were
  averaged over whichever judge calls succeeded.
- **Cause:** 31 judge calls failed with `429 RESOURCE_EXHAUSTED`. The Gemini free tier allows 15
  requests/minute for `gemini-3.5-flash-lite`, and RAGAS ran calls concurrently with 1 retry.
- **Change:**
  - Judge calls go through a rate limiter (`EVAL_RPM`, default 12/min), are retried up to 8× with
    backoff, and scoring runs 1 sample at a time. Verified with a mock server: calls arrive evenly
    spaced at the configured rate.
  - The summary now reports `<metric>_n` (how many samples each score is based on) and warns when a
    metric is incomplete.
  - `--rescore <qa_file.json>` re-runs only the judge on saved answers, so the v1 answers are kept and
    no agent/baseline calls are repeated.
- **Result:** the fix works in a mocked test (calls evenly spaced at the configured rate). The Gemini v1
  QA re-score was not run before switching models (LOG-14), so the Gemini row's RAGAS numbers stay
  marked incomplete. They can still be filled later with `--rescore` and `EVAL_PROVIDER=gemini`.
- **Files:** `eval/ragas_judge.py`, `eval/run_eval.py`

### LOG-14 · Switched agents, baseline and judge to OpenAI GPT-5.4 mini (2026-10-27)
- **Issue:** the Gemini free tier (15 requests/minute) made the RAGAS suite unreliable (LOG-13) and
  slowed every run. Unused OpenAI credits were available.
- **Decision:** `gpt-5.4-mini` for everything (agents, baseline, RAGAS judge). Reasons: current model
  (GPT-5 and GPT-5 mini are deprecated), function calling + structured outputs, positioned for
  sub-agent workloads, $0.75 / $4.50 per 1M input/output tokens. Estimated cost for the full plan
  (v1 re-run, v2, v3, with buffer): about $5–6. An independent Gemini judge is deferred.
- **Change:**
  - `app/llm.py` is provider-aware (the `LLM_MODEL` prefix picks OpenAI or Gemini). For GPT-5.4 mini no
    reasoning effort and no temperature are sent: Chat Completions only allows function tools with
    reasoning effort `none` (the model's default), and not every GPT-5.x model accepts a custom
    temperature. 60 s timeout per attempt and 6 retries.
  - `app/config.py`: `OPENAI_API_KEY`, `EVAL_PROVIDER`, `LLM_TIMEOUT`; the missing-key check follows the
    provider in use.
  - `eval/ragas_judge.py`: the judge talks to OpenAI directly or to Gemini's OpenAI-compatible endpoint;
    the default `EVAL_RPM` is 60 for OpenAI and 12 for Gemini.
  - `/health` and the UI sidebar report the active provider and key status. `.env.example` rewritten.
- **Experiment hygiene:** the agents' prompts, chunks, gold rules and calculator are unchanged. v1 is
  re-run on GPT-5.4 mini before any chunking change, so the model effect and the chunking effect are
  measured separately.
- **Verified:** with a mocked OpenAI server, a real CrewAI agent ran a tool call and returned a valid
  `CardRules`; requests carried `model=gpt-5.4-mini`, no temperature and no reasoning effort. The
  baseline and the judge also worked, Gemini still builds correctly, and the unit tests pass.
- **Result:** pending (v1 re-run on GPT-5.4 mini).
- **Files:** `app/llm.py`, `app/config.py`, `app/main.py`, `ui/app.py`, `eval/ragas_judge.py`, `.env.example`

### LOG-15 · RAGAS sent unsupported parameters to GPT-5.4 mini (2026-10-27)
- **Issue:** in the mocked test, judge requests carried `max_tokens` and `temperature`. OpenAI's GPT-5
  family rejects `max_tokens` (it needs `max_completion_tokens`), so every judge call would have failed.
- **Cause:** a bug in RAGAS 0.4.3. It always adds `temperature=0.01`, `top_p=0.1`, `max_tokens=1024`, and
  is meant to remap them for reasoning models, but its check reads the version with `int()`:
  `gpt-5.4-mini` gives `"5.4"`, which fails to parse, so the model is treated as non-reasoning.
- **Change:** for OpenAI GPT-5+ / o-series judges, `temperature`, `top_p` and `max_tokens` are removed after
  the judge LLM is built and `max_completion_tokens=4096` is set.
- **Result:** mocked requests now carry only `max_completion_tokens=4096`; Gemini judge requests are
  unchanged.
- **Files:** `eval/ragas_judge.py`

### LOG-16 · Token and cost tracking for every LLM call (2026-10-27)
- **Issue:** after the one-card calibration run on GPT-5.4 mini, the OpenAI usage dashboard still showed
  $0, because it updates with a delay. There was no way to see what a run cost, or to compare cost
  between experiment versions.
- **Change:** new `app/usage.py`. Every LLM call is recorded with its step name:
  - agents: CrewAI's per-instance token counters (each agent has its own LLM instance, so the counts are
    exactly that task's usage, including CrewAI's structured-output conversion call);
  - baseline: the same, per call;
  - RAGAS judge: the `usage` field of every OpenAI response, read in the existing rate-limit wrapper.
  - `usage.collect()` scopes the counts to a block, using context variables so CrewAI Flow worker threads
    are included. Cost = uncached input × $0.75 + cached input × $0.075 + output × $4.50 per 1M tokens
    (GPT-5.4 mini), configurable.
  - Surfaced in: the rules cache (`usage` per card) with a `[usage]` line printed after each extraction;
    every eval suite (`report[suite]["usage"]` plus a "Cost of this run" table in `summary.md`); the
    `/ask`, `/recommend` and `/rules/{id}/refresh` responses; captions in the UI.
- **Verified (mocked OpenAI server):** recorded calls matched HTTP calls exactly (agent 2/2, judge 16/16);
  the per-call cost matched a hand calculation ($0.000138); usage from Flow steps on worker threads
  reached the collector (9/9 calls).
- **Files:** `app/usage.py` (new), `app/crew.py`, `app/llm.py`, `app/baseline.py`, `app/main.py`,
  `eval/ragas_judge.py`, `eval/run_eval.py`, `ui/app.py`

### LOG-17 · Calibration run: GPT-5.4 mini read the wrong fee row (2026-10-27)
- **Finding:** one-card extraction of SBI Cashback on GPT-5.4 mini (45 s, 25 passages): rates, caps and
  exclusions were all correct, but the annual fee came out as **₹499** instead of ₹999.
- **Cause:** in the SBI MITC fee table, the row below "CASHBACK SBI Card 999" is "Tata Neu Plus SBI
  Credit Card 499". In the flattened PDF text the rows run together, so the wrong number sits next to the
  card name. Same failure class as LOG-12 (tables lose their structure in plain-text extraction).
- **Planned change:** v2 (table-preserving parsing). Recorded here as a prediction to check.

### LOG-18 · v1 on GPT-5.4 mini: results and model comparison (2026-10-27)
- **Run:** `v1_pymupdf_fixed_gpt54mini`, same pipeline as the Gemini v1. All RAGAS scores are based on
  8/8 samples (the LOG-13 fix worked). Cost $0.50: extraction $0.09, recommend $0.04, QA $0.37, of which
  the judge was $0.33.

| v1 metric | Gemini 3.5 flash-lite | GPT-5.4 mini |
|---|---|---|
| Extraction field accuracy | **85.9%** | 74.4% |
| Crew top-pick accuracy | 40% | 40% |
| Crew mean regret | ₹2,854 | **₹708** |
| Crew mean abs error / card | **₹1,794** | ₹2,040 |
| Crew estimates within 10% | **53%** | 40% |
| Baseline mean regret | ₹456 | ₹3,584 |
| Baseline mean abs error / card | ₹3,979 | ₹5,704 |
| RAGAS agent (faith / recall / precision / factual) | incomplete | 0.99 / 0.83 / 0.83 / 0.52 |
| RAGAS baseline | incomplete | 1.00 / 0.71 / 0.75 / 0.55 |
| Extraction time per card | 45–240 s | 15–25 s |

- **Agents vs baseline (GPT-5.4 mini):** the crew's regret is 5× lower (₹708 vs ₹3,584) and its per-card
  error is 2.8× lower. Agentic retrieval is better on context recall (0.83 vs 0.71) and context precision
  (0.83 vs 0.75); faithfulness is ~1.0 for both. Factual correctness is similar (0.52 vs 0.55). The agent
  writes longer answers with extra true details that the short reference answers don't contain, and
  claim-level F1 penalises that; treat this metric with care.
- **Gemini vs GPT-5.4 mini:** GPT-5.4 mini is 3–10× faster but extracts less accurately (74% vs 86%).
  Its errors happened to hurt the ranking less: Flipkart Axis is now *under*-valued instead of
  over-valued, so it no longer wins wrong recommendations.
- **Behaviour difference found:** GPT-5.4 mini made exactly **2 LLM calls per card**. It requested all of
  its searches at once (parallel tool calls, 9–25 passages), then answered, with no second round of
  searching to check what it found. Gemini searched iteratively.
- **GPT-5.4 mini error patterns:**
  - *Over-exclusion:* treats categories that a revision notice doesn't mention as earning nothing
    (Flipkart Axis: Amazon, Swiggy, offline and travel marked excluded, base rate 0). Partly a corpus gap:
    the 1% base rate is only implied in the ingested PDFs (see Open issues).
  - *Over-inclusion:* Swiggy BLCK given 5% on Zomato and offline; Amazon Pay ICICI's 2% partner rate
    applied to Swiggy, Zomato and other online.
  - *Table errors:* SBI fee ₹499 (LOG-17) and fee waiver ₹20,000 instead of ₹2,00,000.
  - Utilities and insurance excluded on 4 cards, where they earn 1% (same as Gemini).
- **Decision:** v2 and v3 are compared against this GPT-5.4 mini row. A candidate later experiment:
  require a second, verification round of searches in the Rules Researcher prompt.
- **Files:** `eval/results/v1_pymupdf_fixed_gpt54mini/`

### LOG-19 · v2 parsing and chunking: Markdown sections instead of page windows (2026-10-28)
- **Issue:** v1 errors traced back to the chunker. Tables lost their columns (SBI fee ₹499 and waiver
  ₹20,000 read from the wrong row, LOG-17), a table running onto the next page was cut in two (Ornge
  Table 2.1, LOG-12), and the Swiggy PDF's chunks were tagged with *both* cards, so a BLCK passage was
  also returned when researching Ornge.
- **Cause:** v1 used plain page text (`page.get_text()`), cut into 180-word windows per page. Rows,
  headings and page-crossing tables had no meaning to the chunker.
- **Change** (`CHUNKER=v2`, now the default; `CHUNKER=v1` still rebuilds the old index):
  1. **Parsing:** `pymupdf4llm.to_markdown(page_chunks=True, header=False, footer=False, use_ocr=False)`.
     Tables come out as Markdown rows (`|CASHBACK SBI Card|999|999 (Waived off on annual spends of Rs.2
     Lakh ...)|`), headings as `#` lines. OCR is off: the PDFs are digital and OCR would need Tesseract.
     Already installed: `pymupdf` 2.0 bundles `pymupdf4llm` and its layout model.
  2. **Clean-up:** emphasis markup (`**`, `_`, `<u>`) removed to save tokens; short lines repeated on 3+
     pages dropped ("Classification - Internal" was rendered as a heading and split sections in two).
     Captions such as "Table 1.1" stay as text, not headings.
  3. **Section-aware chunks:** a new chunk starts at each heading and a section may cross a page break.
     Target ~1,800 characters per chunk; sections under 300 characters merge into the next one; long
     prose splits on sentence boundaries.
  4. **Tables:** kept in one chunk up to 2,400 characters. Bigger tables split between rows with the
     header row repeated on every piece; a row is never cut. A table that continues on the next page
     without repeating its header is glued back together (Ornge Table 2.1 is now one chunk, p.2-3).
  5. **Card scoping:** in a multi-card PDF, a heading that names exactly one card (e.g. "Terms and
     Conditions - Swiggy BLCK HDFC Bank Credit Card") scopes the following chunks to that card's
     `card_ids`. The Pinecone card filter then keeps Ornge and BLCK passages apart.
  6. **Context line:** each chunk's text starts with `<card names> | Section: <heading>`, and it is
     embedded with the body, so "BLCK online 5%" finds the BLCK table.
  7. **Citations:** records now carry `page` (start) and `page_end`; a citation reads `file p.2-3` when a
     chunk spans pages. Records also store `section` and `chunker`. Ingestion writes
     `data/index_info.json`, and eval `_meta.index` records which chunker the index was built with.
- **Result (offline, before the eval run):** 397 chunks instead of 650 (median ~1,480 characters incl. the context line, vs
  ~1,040). Spot checks: the SBI fee row is intact in one chunk; Ornge Table 2.1 including the
  Amazon/Flipkart rows is one chunk; Ornge chunks carry only `swiggy_hdfc_ornge`, BLCK only
  `swiggy_hdfc_blck`. Parsing all 9 PDFs takes ~70 s (v1: ~2 s); this is a one-off ingestion cost.
  4 new unit tests (`tests/test_chunking_v2.py`); 13/13 tests pass. The eval result goes in the tracker.
- **Confounds to keep in mind when reading v2:** chunks are larger, so `TOP_K=6` now returns more text
  per search (~1.4× v1 by mean chunk size); and card scoping is a metadata change, not only a chunking one. Both are part of
  "how the corpus is chunked" and were kept in one experiment to stay within the time budget.
- **Files:** `app/chunking_v2.py` (new), `app/ingest.py`, `app/retriever.py`, `app/config.py`,
  `eval/run_eval.py`, `tests/test_chunking_v2.py` (new), `requirements.txt`, `.env.example`, `.gitignore`

### LOG-20 · v2 results: retrieval clearly better, extraction mixed (2026-10-28)
- **Run:** `v2_pymupdf4llm_sections` (index: chunker v2, 397 chunks). Same model, prompts, TOP_K and gold
  rules as `v1_pymupdf_fixed_gpt54mini`. All RAGAS scores on 8/8 samples. Cost $0.58 (extraction $0.11,
  recommend $0.04, QA $0.43).

| Metric | v1 (GPT-5.4 mini) | v2 | Change |
|---|---|---|---|
| Extraction field accuracy | 74.4% | 69.2% | −5.2 pts (6 fields fixed, 14 broken) |
| RAGAS agent: context recall | 0.83 | **0.96** | +0.13 |
| RAGAS agent: context precision | 0.83 | 0.85 | +0.02 |
| RAGAS agent: factual correctness | 0.52 | **0.65** | +0.13 |
| RAGAS agent: faithfulness | 0.99 | 0.97 | −0.02 |
| RAGAS baseline: recall / precision / factual | 0.71 / 0.75 / 0.55 | **1.00 / 0.86 / 0.61** | all up |
| Crew top pick / regret | 40% / ₹708 | 60% / ₹4 | looks better, but see below |
| Crew mean abs error per card | ₹2,040 | ₹2,102 | unchanged |
| Baseline mean abs error per card | ₹5,704 | ₹7,090 | worse |

- **Retrieval improved.** Context recall rose for both systems (agent 0.83→0.96, baseline 0.71→1.00) and
  factual correctness rose for both. These metrics don't depend on rule extraction, so they are the
  cleanest measure of the chunking change.
- **Predictions confirmed:**
  - SBI fee ₹999 and waiver ₹2,00,000 are now correct (LOG-17 fixed). The fee row stayed intact.
  - Card scoping worked: every Ornge passage the agent saw was tagged Ornge only, and every BLCK passage
    BLCK only. BLCK no longer gets 5% on offline spends.
  - Amazon Pay ICICI's 2% partner rate no longer leaks onto Swiggy and Zomato.
  - Flipkart Axis is unchanged at 50%, as predicted (a reasoning and corpus problem, not chunking).
- **Prediction refuted: Swiggy Ornge got worse (77% → 54%).** New failure mode: **merged table cells.**
  In Ornge Table 1.1, the "Capping: Rs.1500 per billing cycle" cell is merged across the Swiggy,
  online-merchant, Cleartrip and Nykaa rows (that is the shared 5% cap). Markdown has no merged cells,
  so pymupdf4llm writes the cap on the Swiggy row only and leaves the others blank. The agent then read
  the 5% online rule as **uncapped** (5 cap fields wrong). It also put offline spends at 5%, misreading
  the category list (Apparels, Electronics…) despite "only online transactions". v1's flattened text
  happened to put "Rs.1500" next to both rows. Checked with `page.find_tables()`: PyMuPDF marks those
  positions as `None` (covered by a merged cell) instead of `""` (empty), so they can be detected.
  BLCK's table gives the online row its own cap, which is why BLCK's caps are right.
- **New errors not caused by chunking (8 fields):**
  - Travel left out of every rule on SBI, Millennia and BLCK (6 fields): a category-mapping slip. The
    text describing those rules is the same as in v1.
  - Millennia fee: ₹0 with a note that the fee isn't in the documents. That's true (Open issues); v1's
    ₹1,000 wasn't supported by any retrieved passage either.
  - BLCK: the first-year joining-fee waiver (₹75,000 in 90 days) was put in the renewal-waiver field.
- **Why the crew's ranking "improved" is not real:** the over-valued card simply moved. In v1, BLCK was
  over-valued (5% leaking onto offline and Zomato) by +₹3,480 / +₹4,320 / +₹11,520 / +₹6,960 / +₹2,400
  across the 5 personas. In v2, BLCK is close to correct, and **Ornge** is over-valued by almost the same
  amounts (+₹3,480 / +₹2,520 / +₹11,520 / +₹6,960 / +₹2,400). Ornge happens to be the gold top pick (or
  tied) for 4 of the 5 personas, so the regret dropped by luck. The per-card error (₹2,040 → ₹2,102)
  shows the valuation accuracy did not change.
- **Caveat:** one extraction per card per run. The travel omissions in particular may be run-to-run variance.
- **Decision:** keep v2 as the default chunker (better retrieval, and its errors are now specific and
  fixable). Candidates for the next step:
  1. v2.1: mark merged table cells (detected via `find_tables()`), a chunking fix;
  2. v3: prompt fixes for category mapping ("assign every one of the 11 categories"; online-only rules;
     merchant-specific rates);
  3. a repeat extraction run to measure variance.
- **Files:** `eval/results/v2_pymupdf4llm_sections/`

### LOG-21 · v2.1: restore merged table cells (2026-10-28)
- **Issue:** LOG-20. Markdown tables can't merge cells, so a value printed once for several rows ends
  up on one row only. Swiggy Ornge's shared "Rs.1500 per billing cycle" cap appeared only on the
  Swiggy row, and the agent read the 5% online rule as uncapped.
- **Cause:** pymupdf4llm writes a merged cell's text into one row and leaves the rows it covers blank,
  which looks exactly like a genuinely empty cell.
- **Change** (`CHUNKER=v2.1`, now the default; `v2` and `v1` still selectable):
  1. For each page with a Markdown table, run PyMuPDF `page.find_tables()`. It reports a position
     covered by a merged cell as `None` (an empty cell is `""`), with cell bounding boxes.
  2. For each `None`, find the cell in the same column whose box covers it: a **vertical** merge group.
     Horizontal merges (a note spanning a whole row) are left alone, since repeating them across
     columns adds nothing.
  3. Match each Markdown table to the PyMuPDF table with the same shape (rows × columns) on that page,
     in order. Tables that don't match are left unchanged (62 of 100 tables match; 11 have merged cells).
  4. Write the group's text into every row it covers, tagged
     `[one merged cell across N rows]`, so the model can tell a **shared** value from a repeated one.
     If the text sits in a covered row (the MCC table prints "Fuel" mid-cell), it is still found.
- **Result (offline):** still 397 chunks with the same IDs; only **5 chunks changed** (Ornge Table 1.1,
  Ornge excluded-MCC table, BLCK Table 1.1, BLCK excluded-MCC table, one ICICI MITC fee table), so v2.1
  vs v2 isolates this one fix. Ornge Table 1.1 now shows the Rs.1500 cap on all 4 rows that share it;
  BLCK's shows its 5% online cap shared by 3 rows, separate from its Swiggy cap, matching the gold
  rules. 2 new unit tests; 15/15 pass. Ingestion now takes ~105 s (was ~70 s).
- **Predictions for the v2.1 run:** Ornge's 5 cap fields should be fixed. Ornge offline at 5%, the travel
  omissions, the Millennia fee and Flipkart Axis should not change (not table-structure problems),
  apart from run-to-run variance.
- **Files:** `app/chunking_v2.py`, `app/ingest.py`, `app/config.py`, `.env.example`,
  `tests/test_chunking_v2.py`, `README.md`

### LOG-22 · v2.1 results: the targeted fix worked; run-to-run variance is now the bigger signal (2026-10-28)
- **Run:** `v2_1_merged_cells` (index: chunker v2.1, 397 chunks, 5 changed vs v2). Cost $0.58.

| Metric | v2 | v2.1 |
|---|---|---|
| Extraction field accuracy | 69.2% | 66% |
| Swiggy Ornge | 54% | **77%** |
| Swiggy BLCK / Amazon Pay ICICI | 65% / 88% | 46% / 65% |
| SBI / Millennia / Flipkart Axis | 92% / 65% / 50% | 92% / 65% / 50% |
| Crew top pick / regret / abs error per card | 60% / ₹4 / ₹2,102 | 60% / ₹4 / ₹2,189 |
| RAGAS agent (faith / recall / precision / factual) | 0.97 / 0.96 / 0.85 / 0.65 | 0.96 / 0.96 / 0.85 / 0.68 |
| RAGAS baseline (faith / recall / precision / factual) | 0.96 / 1.00 / 0.86 / 0.61 | 0.96 / 0.96 / 0.80 / 0.70 |

- **Prediction confirmed:** Ornge's 5 cap fields are all correct again (the shared Rs.1500 cap is read for
  Amazon, Flipkart, other online and travel), and its offline rate went back to 1%. Ornge is back to 77%.
- **But overall accuracy fell (69% → 66%), and the errors came from chunks that did not change:**
  - *BLCK (65% → 46%):* the agent saw the corrected Table 1.1 and still mapped the 5% online rule to
    `other_online` only, dropping Amazon and Flipkart (and its base rate went to 0 for offline).
  - *Amazon Pay ICICI (88% → 65%):* the 1% "non-Amazon spends" rule was mapped to `other_online` only;
    Flipkart, Swiggy, Zomato, offline and travel fell to 0, and travel was marked excluded.
  - *SBI:* fee ₹499 and no waiver again, although the SBI chunks are identical to v2 (see below).
  - *Millennia:* fee correct this time, offline dropped; *SBI* travel correct this time.
  These are **category-mapping slips that change from run to run**. Same chunks, same model, different
  answers: one extraction per card is too noisy to judge a change of a few points.
- **New retrieval finding: the SBI fee table.** The SBI MITC fee schedule is split into ~6 row-chunks
  across 12 pages. Every piece carries the same context line ("Cashback SBI Card | Section: A. Annual
  Fees & Renewal Fees"), because the manifest maps the common MITC to this one card. So to the embedding
  all six look equally relevant. In v2 the agent happened to retrieve the piece with the
  `CASHBACK SBI Card|999|...2 Lakh` row; in v2.1 it got four other pieces and took ₹499 from another
  card's row. Two fixes to consider: keyword (hybrid) search, which would match the exact card name in
  the row; and not labelling chunks of a common, all-cards document with one card's name.
- **The crew's ranking is unchanged** (same picks as v2). Ornge is now valued closer to correct; the crew's
  per-card error is about the same. The RAGAS changes are within noise for 8 questions.
- **Decision:** before any further change, measure the noise: run the extraction suite 2 more times on
  the same v2.1 index (~$0.12 each) and report the mean and range per card. Then do v3, aimed at the
  largest error class: category mapping (every one of the 11 categories must be assigned to a rule, the
  base rate, or an exclusion).
- **Files:** `eval/results/v2_1_merged_cells/`

### LOG-23 · v3: Rules Researcher prompt rules for the recurring error classes (2026-10-28)
- **Issue:** across v1, v2 and v2.1, most extraction errors are not retrieval or chunking problems but the
  same few reasoning slips, on passages the agent had in front of it (LOG-18, LOG-20, LOG-22):
  1. "1% on other spends" mapped to one category, with `base_rate_pct` 0 (ICICI, BLCK, Ornge, Millennia);
  2. categories excluded that the documents never exclude: utilities/insurance on 4 cards, and on
     Flipkart Axis everything a revision notice doesn't mention;
  3. listed merchants not mapped (BLCK's Amazon/Flipkart; travel left out on SBI, Millennia, BLCK);
  4. online rules applied to offline (Ornge v2), merchant rates applied to a whole category (Flipkart Axis
     Myntra 7.5%, ICICI partner 2%);
  5. the fee taken from another card's row in a multi-card fee table (SBI ₹499);
  6. one parallel round of searches, never a check (LOG-18).
- **Change:** `V3_RULES` appended to the Rules Researcher task (`app/crew.py`), switchable with
  `RESEARCHER_PROMPT=v3|v2`, recorded in eval `_meta.researcher_prompt`. One rule per error class above,
  plus a verification search round and an 11-category checklist before answering. Model, chunker v2.1,
  TOP_K, schema and gold rules unchanged, so v3 vs v2.1 isolates the prompt.
- **Guarding against teaching to the test:** the rules describe document conventions (what "other spends"
  means, how fee tables work), not answers; no card-specific numbers or category decisions are in the
  prompt. Limitation: the rules were written after seeing these 6 cards' errors and are measured on the
  same 6 cards; there is no held-out card set.
- **Predictions:** base-rate and exclusion fixes should raise ICICI, BLCK, Ornge and Millennia (utilities,
  insurance, travel, base rate). Flipkart Axis should improve on the base-rate fields, though its 1% base
  is only implied by the corpus. The SBI fee should be right more often. The Millennia fee (not in the
  corpus) can't be fixed by a prompt. Extraction will be slower and dearer (more searches per card).
- **Files:** `app/crew.py`, `app/config.py`, `eval/run_eval.py`, `.env.example`

### LOG-24 · v3 results: prompt rules fix the reasoning slips (2026-10-28)
- **Run:** `v3_prompt_rules` (`_meta.researcher_prompt` = v3, index chunker v2.1). Cost $0.62 (extraction
  $0.15, up from $0.12: 16 LLM calls for 6 cards instead of 12, i.e. the verification round ran on some
  cards; ~3 s more per card).

| Card | v2.1 | v3 | Fixed | Broken |
|---|---|---|---|---|
| Amazon Pay ICICI | 65% | **88%** | 6 (base 1% on Flipkart, Swiggy, Zomato, offline, travel) | 0 |
| Swiggy BLCK | 46% | **73%** | 7 (5% on Amazon/Flipkart, offline 1%) | 0 |
| Swiggy Ornge | 77% | **85%** | 2 (Zomato 1%) | 0 |
| HDFC Millennia | 65% | **73%** | 4 (offline, travel 1%) | 2 (fee 0; other online 5%) |
| SBI Cashback | 92% | 92% | 2 (fee ₹999, waiver ₹2,00,000) | 2 (travel left out) |
| Flipkart Axis | 50% | 50% | 1 | 1 (quarterly cap read as monthly) |
| **Overall** | 66% | **77%** | **22** | **5** |

- **Predictions:** confirmed for ICICI, BLCK, Ornge, Millennia (base rate, travel, offline) and the SBI fee.
  Flipkart Axis did **not** improve: the agent still zeroes every category outside Flipkart/Myntra/Cleartrip,
  because the corpus never states its 1% base rate (Open issues). Millennia's fee is wrong again, as predicted.
- **Not fixed by the exclusion rule:** utilities and insurance are still excluded on the HDFC cards and
  insurance on ICICI, in every run so far. Either the agent reads the utility/insurance fee and MCC lines as
  exclusions, or the gold rule is a judgement call worth a second look.
- **Recommendation:** the crew's per-card error fell from ₹2,189 to **₹1,172**, estimates within 10% rose
  from 33% to **57%**, and the top pick is right for 4 of 5 personas (the miss costs ₹1,440/yr). Unlike v2,
  every recommendation metric moved together, so this is a real gain in valuation.
- **QA (RAGAS):** the v3 prompt is only used for rule extraction, not for answering questions. Still, the
  baseline's factual correctness moved 0.70 → 0.51 and the agent's 0.68 → 0.59 between v2.1 and v3, with
  nothing changed on that path. With 8 questions, a ±0.2 swing in factual correctness is noise; recall
  (0.96 for both) and faithfulness (0.97–1.00) are stable.
- **Files:** `eval/results/v3_prompt_rules/`

---

## Open issues

- HDFC Millennia fee/waiver isn't in the ingested PDFs; add HDFC's fees & charges page as a document.
- Flipkart Axis base rate (1%) is only implied by the revision notice; the full card T&C would confirm it.
- The extraction eval compares effective per-category rates and caps; it doesn't check whether
  categories **share** a cap (e.g. Ornge's single 5% bucket). The recommend suite still catches the
  effect on rupee values.
- LLM extraction varies a lot between runs (LOG-22: BLCK 65%→46% and ICICI 88%→65% on unchanged
  chunks). Measure the spread with repeat runs before reading small differences.
- SBI fee table: six row-chunks share one context line, so the right row isn't reliably retrieved
  (LOG-22). Candidates: hybrid (keyword) search; no single-card label on common MITC chunks.
- Markdown tables lose merged cells (Ornge shared cap, LOG-20); addressed by v2.1 (LOG-21). Only
  tables whose shape PyMuPDF and pymupdf4llm agree on are repaired (62 of 100).
- Top-pick accuracy and regret can move by luck when an over-valued card happens to be near-best; read
  them together with mean abs error per card.
- GPT-5.4 mini currently both answers and judges (self-grading risk); an independent Gemini judge is planned.
