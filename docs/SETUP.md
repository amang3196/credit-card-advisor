# Setup, run and evaluate

How to run the Credit Card Fit Advisor locally and reproduce the benchmarks. For what the project is and
what it shows, see the [README](../README.md).

## Setup

**1. Python env** (3.11 or 3.12)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**2. Keys:** `cp .env.example .env` and fill in:
- `PINECONE_API_KEY`: [app.pinecone.io](https://app.pinecone.io) → API Keys. The free Starter plan is enough. The index is created for you in AWS `us-east-1` with Pinecone's hosted `llama-text-embed-v2` model, so no embedding API key is needed.
- `OPENAI_API_KEY`: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)
- `LLM_MODEL` defaults to `openai/gpt-5.4-mini` (agents and baseline); the RAGAS judge uses `EVAL_PROVIDER` +
  `EVAL_LLM_MODEL` (default `openai` / `gpt-5.4-mini`).
- Gemini also works: set `LLM_MODEL=gemini/<model>` and/or `EVAL_PROVIDER=gemini`, and add `GEMINI_API_KEY`
  ([aistudio.google.com/apikey](https://aistudio.google.com/apikey)).

**3. PDFs:** save these into `data/pdfs/` with **exactly** these names (`bank_card_doctype_date`; missing ones are skipped):

| Saved as | Card(s) | Source |
|---|---|---|
| `sbi_cashback_tnc.pdf` | Cashback SBI | [Cashback SBI T&C e-kit](https://www.sbicard.com/sbi-card-en/assets/docs/pdf/ekit-tncs/cashback-tnc-ekit.pdf) |
| `sbi_cashback_revision_2026-04.pdf` | Cashback SBI | [Revision notice, effective 1 Apr 2026](https://www.sbicard.com/sbi-card-en/assets/docs/pdf/cashback-revised.pdf) |
| `sbi_common_mitc.pdf` | Cashback SBI | [SBI Card MITC](https://www.sbicard.com/en/most-important-terms-and-conditions.page) |
| `hdfc_millennia_tnc_2026-07.pdf` | HDFC Millennia | Millennia T&C (published 20 Jul 2026), from the Millennia product page on hdfc.bank.in |
| `hdfc_swiggy_ornge_blck_tnc_2026-03.pdf` | Swiggy Ornge **and** BLCK | [Combined T&Cs](https://www.hdfc.bank.in/content/dam/hdfcbankpws/in/en/pdfs/Combined_TnCs_Ornge_and_BLCK.pdf) |
| `axis_flipkart_revision_2025-06.pdf` | Flipkart Axis | Revision effective 20 Jun 2025, from the Flipkart Axis product page on axisbank.com |
| `axis_common_mitc.pdf` | Flipkart Axis | [Axis Bank credit cards MITC](https://www.axisbank.com/docs/default-source/default-document-library/mitc-credit-cards.pdf) |
| `icici_amazon_pay_tnc_2026-10.pdf` | Amazon Pay ICICI | [T&C (updated 12 Oct 2026)](https://www.icici.bank.in/content/dam/icicibank/india/managed-assets/revamp-page-images/docs/pdf/tc-for-amazon-pay-credit-card.pdf) |
| `icici_amazon_pay_mitc_2026-07.pdf` | Amazon Pay ICICI | [MITC (updated 10 Jul 2026)](https://www.icici.bank.in/terms-condition/tnc-for-amazon-pay) |

Bank URLs move (several are migrating to `.bank.in`). If a link is dead, open the card's product
page and use its "T&C" / "MITC" link. If you find a newer revision, add it to `data/cards.yaml`
with `doc_type: revision` and its `effective_date`.

## Run

```bash
python -m app.ingest --reset          # PDFs -> Pinecone (~1-2 min). Writes data/extracted/*.md so you can see what the parser saw
uvicorn app.main:app --reload         # then open http://127.0.0.1:8000/docs
pytest                                # calculator + chunker unit tests, no keys needed
```

**Chunking:** `CHUNKER=v2.1` (default) parses PDFs to Markdown with `pymupdf4llm`, chunks by section,
keeps tables whole, tags each section with the card it describes, and restores merged table cells
(found with PyMuPDF's `find_tables()`). `CHUNKER=v2` is the same without the merged-cell step, and
`CHUNKER=v1` rebuilds the original 180-word page windows. Always use `--reset` after switching. Details in
`PROJECT_UNDERSTANDING.md` section 4 and `ENGINEERING_LOG.md` LOG-19 to LOG-21.

**UI:** in a second terminal (keep the API running):

```bash
streamlit run ui/app.py               # opens http://localhost:8501
```

| Tab | What it shows |
|---|---|
| 🏆 Recommend | Free-text spend → best card, net ₹/yr chart, per-card breakdown, caps hit, cited watch-outs. Modes: agents, baseline, or **compare both** side by side |
| 🎛️ What-if | Change any monthly spend and the ranking updates instantly (calculator only, no LLM calls) |
| 💬 Ask the T&Cs | Questions about the cards, agent vs baseline side by side, with sources and passages |
| 📄 Card rules | What the Rules Researcher extracted per card, with passages used; re-extract button |
| 📊 Evaluation | Extraction accuracy, regret, RAGAS charts from `eval/results/latest.json` |

Endpoints (all testable from Swagger at `/docs`):

| Endpoint | What it does |
|---|---|
| `GET /cards` | Cards, which PDFs are present, whether rules are cached |
| `POST /recommend` | `mode=crew` (agents + calculator) or `mode=baseline` (plain RAG) |
| `POST /ask` | Question about card terms, `mode=agent` or `mode=baseline` |
| `POST /rules/{card_id}/refresh` | Re-run the Rules Researcher for one card |
| `GET /rules/{card_id}` | Cached rules plus the exact passages they came from |
| `POST /calculate` | Value a structured spend profile against cached rules (no LLM) |
| `POST /ingest` | Same as the CLI |

The **first** `/recommend` in crew mode extracts rules for all 6 cards (a few minutes). After that
it's cached in `data/rules_cache/` and a request takes ~2 LLM calls plus the advisor's searches.

## Running the evaluation

1. **Gold rules.** The files in `eval/gold_rules/` are hand-checked against the PDFs (with page references)
   and marked `"_status": "VERIFIED"`. If you add a card, write its gold file in the same format; the eval
   prints a warning while any file is still a `DRAFT`.
2. Run the suites:
   ```bash
   python -m eval.run_eval --suite all --refresh-rules --limit 8 --tag my_run   # the full benchmark (~$0.60)
   python -m eval.run_eval --suite extraction --refresh-rules                   # one suite only
   ```
3. Results go to `eval/results/` (one JSON per run, plus `summary.md` with the latest table for each suite).

**Rate limits and cost:** judge calls are spaced by `EVAL_RPM` (default 60 for OpenAI, 12 for the Gemini
free tier) and retried on 429s. Each summary reports how many samples every score is based on
(`<metric>_n`). If some judge calls still failed, `--rescore <qa_file.json>` re-runs only the judge on the
saved answers. Tag every run with `--tag <name>` so results go to `eval/results/<name>/`. Every run records the tokens and
estimated cost of each LLM call; see "Cost of this run" in `summary.md`. A full benchmark run on
GPT-5.4 mini costs about $0.60.

## Demo script (~3 min)

1. **Problem (20s):** show the SBI revision PDF: caps and new exclusions buried in a separate notice.
2. **Architecture (30s):** the diagram in the README. Say "LLMs extract, code calculates".
3. **Baseline (30s):** `POST /recommend` with `mode=baseline` and a spend that includes rent and fuel. Point at a confident number that's wrong.
4. **Crew (60s):** same request with `mode=crew`. Show the terminal (agent searches with card filters), then the response: per-category breakdown, caps hit, and cited watch-outs.
5. **Proof (30s):** `eval/results/summary.md`, extraction accuracy plus baseline vs crew regret and RAGAS scores.
6. **Trade-off (10s):** crew mode is slower and costs more LLM calls on first run; caching rules per card makes repeat requests cheap.

## Project structure

```
app/
  config.py      settings from .env
  schemas.py     SpendProfile, CardRules, CardValue, Advice (Pydantic)
  manifest.py    reads data/cards.yaml (which PDF covers which card)
  ingest.py      PDF -> chunks -> Pinecone, with card_ids / doc_type / effective_date / page metadata
  chunking_v2.py PDF -> Markdown -> section-aware chunks (tables kept whole, card-scoped sections)
  retriever.py   Pinecone search + citation formatting
  tools.py       CrewAI tool that logs every retrieved passage (for RAGAS)
  calculator.py  deterministic valuation
  baseline.py    plain RAG baseline
  crew.py        agents + CrewAI Flow
  main.py        FastAPI
ui/
  app.py         Streamlit UI (calls the API only)
  api_client.py  HTTP client for the API
eval/
  gold_rules/    hand-checked rules per card
  personas.json  5 spend profiles
  rule_questions.json  13 questions with reference answers
  run_eval.py    the three suites
  ragas_judge.py RAGAS metrics with an LLM judge (OpenAI or Gemini), rate-limited
tests/           calculator + chunker unit tests
```
