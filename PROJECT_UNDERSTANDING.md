# Credit Card Fit Advisor: Project Understanding (KT document)

This document explains the whole project end to end: what it does, how data flows, every design
decision and why it was made, and what to say when someone asks "why did you do it this way?".
It matches the code in this repository.

---

## 1. One-paragraph summary

A user describes their monthly spending in plain English ("12k on Amazon, I have Prime, 25k rent
by card, 4k petrol..."). The system tells them which Indian credit card gives them the most money
back per year, shows the rupee calculation per spend category, and warns them about fine print that
affects them specifically (for example "your rent earns nothing on this card"). It works from the
**official bank T&C and MITC PDFs**, not from blog posts. It is built with **CrewAI** (agents and
Flow), **Pinecone** (vector search with hosted embeddings), **FastAPI** (API) and **OpenAI GPT-5.4 mini** (LLM; Gemini also supported),
and evaluated with **RAGAS** plus custom metrics, against a plain-RAG baseline.

**Scope:** 6 cards from 4 banks, 9 PDFs, 397 chunks (v2/v2.1 chunker; v1 made 650).

| card_id | Card | Bank |
|---|---|---|
| sbi_cashback | Cashback SBI Card | SBI Card |
| hdfc_millennia | HDFC Bank Millennia | HDFC Bank |
| swiggy_hdfc_ornge | Swiggy Ornge HDFC | HDFC Bank |
| swiggy_hdfc_blck | Swiggy BLCK HDFC | HDFC Bank |
| flipkart_axis | Flipkart Axis Bank | Axis Bank |
| amazon_pay_icici | Amazon Pay ICICI | ICICI Bank |

---

## 2. The problem and why it is hard

Card marketing says "5% cashback". The real rules are spread across several documents and are full
of conditions:

- **Caps:** 5% online, but at most ₹2,000 per statement cycle, plus a ₹4,000 overall cap.
- **Exclusions:** rent, fuel, utilities, insurance, wallet loads, railways, EMI, government payments
  and so on earn nothing. The list differs per card.
- **Revisions:** banks change terms with separate "revision" notices. For example, SBI's April 2026
  notice adds new exclusions (gaming, tolls, government). The newer document overrides the older one.
- **One PDF, two cards:** HDFC publishes Swiggy Ornge and Swiggy BLCK in one PDF with different
  numbers (5% vs 10% on Swiggy, ₹500 vs ₹1,000 fee).
- **Fees:** an annual fee + 18% GST, sometimes waived above an annual spend threshold, and some spends
  (like rent) don't count toward that threshold.
- **Conditional rates:** Amazon Pay ICICI gives 5% to Prime members and 3% to everyone else.

Picking the right card means **retrieving the right rules, resolving conflicts between documents,
then doing arithmetic with caps**. Plain RAG is decent at the first step and unreliable at the last
two. That gap is the reason for this project.

---

## 3. End-to-end flow (in points)

### 3a. Offline: ingestion (run once, or when PDFs change)
1. The bank PDFs are placed in `data/pdfs/`.
2. `data/cards.yaml` (the manifest) lists the cards, and for every PDF says which card(s) it covers,
   whether it is a `base` document or a `revision`, and its `effective_date`.
3. `python -m app.ingest --reset` reads every PDF listed in the manifest (missing files are skipped).
4. `pymupdf4llm` turns each PDF into Markdown page by page: tables become Markdown rows, headings
   become `#` lines (v2 chunker, the default; see section 4 for v1).
5. The Markdown is cleaned and cut into section-aware chunks: one chunk per heading-led section (it may
   cross a page), tables kept whole or split between rows, and a context line (card + section) on top.
6. Each chunk becomes a Pinecone record carrying its text plus metadata: `card_ids`, `card_names`,
   `doc_type`, `effective_date`, `source_file`, `page`, `page_end`, `section` and `chunker`.
7. The Pinecone index is created the first time it's needed, with **integrated embedding**: Pinecone
   embeds the text itself using its hosted `llama-text-embed-v2` model.
8. Records are upserted in batches of 90, with retry and backoff.
9. Side outputs for debugging: `data/extracted/<file>.md` (what the parser actually saw; `.txt` with
   v1), `data/chunks.jsonl` (every chunk with its metadata) and `data/index_info.json` (which chunker
   built the index; the eval copies it into each run's `_meta`).

### 3b. Online: `POST /recommend` in crew mode (the main product)
1. FastAPI receives `spend_description` (free text).
2. A **CrewAI Flow** (`RecommendFlow`) starts and runs four steps in order, sharing a typed state object.
3. **Step 1 – profile:** the *Spend Profiler* agent turns the free text into a `SpendProfile`
   (monthly ₹ in 11 fixed categories, plus Prime yes/no, plus assumptions it made).
4. **Step 2 – research:** for each of the 6 cards, the code first looks in `data/rules_cache/<card_id>.json`.
   - **Cache hit:** the stored rules are used with no LLM call.
   - **Cache miss:** the *Rules Researcher* agent runs. It calls the `search_card_terms` tool
     several times, locked to that one card, reads the passages, resolves revisions, and outputs a
     structured `CardRules` object with citations. The result and the exact passages it saw are
     saved to the cache.
5. **Step 3 – calculate:** plain Python (`calculator.py`) values every card for this spend
   profile: rates, per-rule caps, overall cap, exclusions, fee, waiver and GST. It then ranks the
   cards by net annual value. **No LLM involved.**
6. **Step 4 – explain:** the *Fine-print Advisor* agent receives the ranking and the numbers. It
   writes a summary and a verdict per card, plus watch-outs specific to this user's spends. It
   checks fine print through the same search tool and cites it. It **cannot change the ranking**:
   the top pick is enforced in code to be the calculator's #1.
7. The API returns the spend profile, the ranking with per-category breakdown and caps hit, the
   advice, the rules used, every retrieved passage (the contexts) and per-step timings.

### 3c. Online: `POST /recommend` in baseline mode (what we compare against)
1. One Pinecone search, using the user's text plus the words "cashback rate cap exclusions annual fee
   waiver", with top_k = 12 and **no card filter**.
2. One LLM call: "here are the passages, estimate rewards, fee and net value for each card, and
   pick the best". The output is a structured `BaselineRecommendation`.
3. The LLM does the retrieval interpretation, conflict resolution **and** the arithmetic in one shot.

### 3d. Online: `POST /ask` (question answering about card terms)
- **Agent mode:** a Rules Researcher agent with access to all 6 cards works out which card the
  question is about, passes that `card_id` to the tool, can search several times, and answers
  in 1–4 cited sentences.
- **Baseline mode:** one search (top_k = 6, no filter), then one LLM call.
- Both return the answer **and the contexts used**, which is what RAGAS needs.

### 3e. Evaluation (`python -m eval.run_eval`)
1. **extraction suite:** compares the Rules Researcher's extracted rules with hand-checked gold rules,
   field by field.
2. **recommend suite:** for 5 personas, compares the crew and the baseline against the gold answer
   (top pick, regret in ₹, rupee error).
3. **qa suite:** 13 questions with reference answers, answered by the agent and the baseline, then
   scored by RAGAS.
4. Results are written to `eval/results/*.json`, plus a combined `summary.md` table.

---

## 4. Chunking strategy

Three chunkers exist; `CHUNKER` in `.env` picks one at ingestion time. **v2.1 is the default** (v2 plus
merged table cells, 4b). v2 and v1 are kept so earlier benchmarks can be reproduced
(`CHUNKER=v1 python -m app.ingest --reset`).

### 4a. v2: Markdown sections (`app/chunking_v2.py`, LOG-19)

**What we do**
1. **Parse to Markdown** with `pymupdf4llm.to_markdown(page_chunks=True, header=False, footer=False,
   use_ocr=False)`. Tables come out as rows, for example
   `|CASHBACK SBI Card|999|999 (Waived off on annual spends of Rs.2 Lakh ...)|`, and headings as `#` lines.
   Running headers/footers are dropped; OCR is off because the PDFs are digital.
2. **Clean:** remove emphasis markup (`**`, `_`, `<u>`), turn `<br>` inside cells into spaces, and drop
   short lines that repeat on 3+ pages ("Classification - Internal" was rendered as a heading and would
   have split a section). Captions such as "Table 1.1" stay as text, not headings.
3. **Blocks:** each page becomes a list of heading, table and paragraph blocks, each tagged with its page.
4. **Glue page-crossing tables:** if a page starts with a table that has the same number of columns as
   the table that ended the previous page, and it didn't repeat the header, it is the same table
   (Swiggy Ornge Table 2.1 lists Amazon/Flipkart on the next page). Tables that repeat their header on
   each page (the SBI fee schedule) stay one block per page, so each piece keeps its header and an exact page.
5. **Section-aware packing:** a heading starts a new chunk. A section under 300 characters merges into
   the next one. Target ~1,800 characters per chunk; long prose splits on sentence boundaries.
6. **Tables:** a table up to 2,400 characters stays in one chunk. A bigger table splits **between rows**
   and every piece repeats the header row, so no row is ever cut.
7. **Card scoping:** in a multi-card PDF, a heading that names exactly one of its cards ("Terms and
   Conditions - Swiggy BLCK HDFC Bank Credit Card") scopes the following chunks to that card. A chunk
   never straddles two cards.
8. **Context line:** chunk text = `<card names> | Section: <heading>` + the body. This line is embedded
   too, so the vector knows which card and section the passage belongs to.
9. **IDs and pages:** `<file-stem>-s<NNN>` (deterministic); `page` is where the chunk starts and
   `page_end` where it ends, so citations read `file.pdf p.2-3` when needed.
- **Result:** 397 chunks from 9 PDFs (median ~1,480 characters incl. the context line). Parsing takes
  ~70 s for all PDFs, once per ingestion.

**Why**
- The v1 errors were structural: a fee read from the neighbouring table row (LOG-17), a table cut at a
  page break (LOG-12), and both Swiggy cards' passages tagged with both cards. Each step above targets one of those.
- **Headings are the document's own boundaries.** A T&C section usually holds one rule with its caps
  and exclusions, so chunking by section keeps a rate and its cap together without needing overlap.
- **Rows are the unit of meaning in a fee table.** Keeping rows intact and repeating the header means
  a retrieved piece is readable on its own ("Annual Fee | Renewal Fee" columns stay labelled).
- **Card scoping** makes the existing Pinecone card filter do the separation, instead of hoping the LLM
  notices which card a passage is about.
- **Context line:** a passage like "5% cashback on select online spends" is ambiguous on its own; with
  "Swiggy BLCK ... | Section: 2.3 ..." in front, both the embedding and the LLM know what it is.
- **Size:** ~1,800 characters is ~450 tokens, well under `llama-text-embed-v2`'s 2,048-token input limit.

**Trade-offs:** chunks are ~1.4× bigger than v1, so `TOP_K=6` sends more text per search. Parsing is
slower (~70 s vs ~2 s). Heading detection depends on the PDF's fonts, so some sections are long
(ICICI MITC "Billing" spans 12 chunks). `pymupdf` 2.0 already bundles `pymupdf4llm` and its layout
model (ONNX), so there's no extra install.

### 4b. v2.1: merged table cells restored (`fill_merged_cells`, LOG-21)

**Problem:** a PDF table cell can span several rows. Swiggy Ornge's "Rs.1500 per billing cycle" cap is
one cell spanning the Swiggy, online-merchant, Cleartrip and Nykaa rows: one cap shared by all four.
Markdown has no merged cells, so pymupdf4llm put the cap on the Swiggy row only. The agent then read
the 5% online rule as uncapped (LOG-20).

**What we do**
1. Run PyMuPDF `page.find_tables()` on pages that have a Markdown table. It marks a position covered by
   a merged cell as `None` (an empty cell is `""`) and gives each cell's bounding box.
2. For each covered position, find the cell in the same column whose box covers it: a vertical merge
   group. Horizontal merges are ignored.
3. Match each Markdown table to the PyMuPDF table with the same rows × columns on that page, in order;
   skip tables that don't match.
4. Write the group's text into every covered row, tagged `[one merged cell across N rows]`, so the
   model can tell a shared cap from a repeated one.

**Why this way:** pymupdf4llm gives clean text but loses the structure; `find_tables()` keeps the
structure, but its cell text loses spaces ("TypeofSpends"). Using `find_tables()` only for *where* the
merges are and pymupdf4llm for *what* the text is gets both. The tag matters: "Rs.1500 per cycle" on
four rows could otherwise be read as four separate caps.

**Result:** same 397 chunks and IDs; 5 chunks changed. Ingestion ~105 s.

### 4c. v1: page-bounded word windows (baseline for the benchmark)

- **Parser:** PyMuPDF `page.get_text("text")`, one page at a time; whitespace collapsed.
- **Window:** 180 words with a 40-word overlap; chunks never cross a page.
- **ID:** `<file-stem>-p<page>-c<index>`. **Result:** 650 chunks.
- **Why it was the first choice:** simple and predictable for a 3-day MVP, with exact page citations.
- **What went wrong:** tables flattened into text, so columns and rows ran together (the SBI fee row,
  LOG-17); a page break cut tables in two (Ornge, LOG-12). This led to v2.

---

## 5. Retrieval strategy

**Index**
- Pinecone serverless index `card-advisor`, AWS `us-east-1` (the only region on the free Starter plan).
- Namespace `cards`.
- **Integrated embedding:** the index is created with `create_index_for_model`, using the hosted
  model **`llama-text-embed-v2`** (dense, 1024 dimensions). We send raw text both when upserting and
  when searching, and Pinecone embeds it server-side. There is no separate embedding API key and no
  risk of using a different embedding model for queries than for documents.

**Search** (`retriever.search`)
- **Dense semantic search**, with the query text embedded by Pinecone.
- **Metadata filter** `{"card_ids": {"$in": [...]}}`. `card_ids` is a list field. With v1, every chunk of
  the combined Swiggy PDF carried both cards; with v2 each section carries only the card it describes,
  while MITC chunks that apply to all cards keep the full list.
- top_k: 6 by default (`TOP_K` in `.env`), 6 inside the agent tool, 12 for the baseline recommender.
- The search returns these fields: text, card_ids, card_names, doc_type, effective_date,
  source_file, page, page_end.

**How results are shown to the LLM** (`format_chunks`)

Each passage gets a header like this:
```
[sbi_cashback_revision_2026-04.pdf p.1] cards: Cashback SBI Card | doc_type: revision | effective: 2026-04-01
<chunk text>
```
The header gives the model everything it needs to: (a) cite the passage, (b) see which card the
passage belongs to, and (c) decide which rule wins when a base document and a revision disagree.

**Agentic retrieval vs baseline retrieval**

| | Baseline | Agent (Rules Researcher / Q&A) |
|---|---|---|
| Number of searches | 1 | as many as the agent decides (bounded by `max_iter = 10`) |
| Query | user's raw text | focused queries the agent writes ("fee waiver threshold", "excluded MCC") |
| Card filter | none | locked to one card (research) or chosen by the agent (Q&A) |
| Conflict handling | hope the LLM notices | instructed: newer revision / effective date wins |

This difference is what the RAGAS context precision and recall scores are designed to show.

**Not done yet (next steps):** hybrid dense + sparse search (useful for exact tokens like MCC codes
"6513"), reranking (Pinecone hosted reranker), and query-time date filtering.

---

## 6. Tools and technologies

| Tool | Version | Role |
|---|---|---|
| **CrewAI** | 1.15.30 (`crewai[google-genai]`) | Agents, Tasks, a custom Tool, and a **Flow** to orchestrate the pipeline |
| **Pinecone** | Python SDK 10.x, serverless, free Starter plan | Vector store with integrated (hosted) embeddings and metadata filtering |
| **OpenAI GPT-5.4 mini** | via CrewAI's native OpenAI provider | LLM for every agent, the baseline and the RAGAS judge |
| **Gemini** (optional) | via CrewAI's native Gemini provider (google-genai SDK) | Alternative LLM (used for the first v1 run) and alternative judge |
| **FastAPI** + Uvicorn | 0.115+ | REST API, with Swagger UI at `/docs` |
| **RAGAS** | 0.4.3 | Faithfulness, context recall, context precision, factual correctness |
| **OpenAI Python SDK** | 1.40+ | RAGAS judge client: OpenAI directly, or Gemini's OpenAI-compatible endpoint |
| **PyMuPDF** | 1.24+ | PDF text extraction |
| **Pydantic v2** | | Every data contract (spend profile, rules, values, advice) |
| **pytest** | | Unit tests for the calculator |
| PyYAML, python-dotenv | | Manifest, config |

**Dependency note:** `langchain-community` is pinned to `0.4.1`, because RAGAS 0.4.3 fails to import
with `0.4.2` (it imports a module that was removed). This was found while setting up the project.

**The custom tool: `search_card_terms`** (`app/tools.py`)
- Arguments: `query` (required) and `card_id` (optional).
- Each tool instance has an `allowed_card_ids` list. The Rules Researcher gets exactly one card, so it
  physically cannot read another card's numbers. The Q&A agent and the Advisor get all the cards
  they need.
- Every returned passage is **logged** (de-duplicated by chunk id) in `tool.retrieved`. This is how
  we know exactly what the agent saw, which is required for RAGAS faithfulness and context metrics.
  It's also stored in the rules cache as an audit trail.

---

## 7. Models

| Purpose | Model | Where it's set |
|---|---|---|
| Agents + baseline | **`openai/gpt-5.4-mini`** (since 2026-10-27; before that `gemini/gemini-3.5-flash-lite`) | `LLM_MODEL` in `.env` |
| RAGAS judge | **`gpt-5.4-mini`** via OpenAI (Gemini still supported) | `EVAL_PROVIDER` + `EVAL_LLM_MODEL` |
| Embeddings | `llama-text-embed-v2` (Pinecone-hosted) | `PINECONE_EMBED_MODEL` |

- **Provider-aware LLM factory** (`app/llm.py`): the `LLM_MODEL` prefix (`openai/` or `gemini/`) picks the
  provider. Both stay supported, which is what makes the model comparison in the engineering log possible.
- **GPT-5.4 mini settings:** CrewAI calls OpenAI through Chat Completions, where GPT-5.x function tools only
  work with reasoning effort `none` (the model's default), so no reasoning effort is sent. No custom
  temperature is sent either, because not every GPT-5.x model accepts one. 60 s timeout per attempt,
  6 retries with backoff (OpenAI SDK).
- **Gemini settings (when used):** temperature 0.1; retries on 429/500/502/503/504 (6 attempts,
  backoff 2 s → 30 s cap) and a 60 s timeout per attempt, added after a `503 high demand` error and a
  660-second response on `gemini-3.8-flash`.
- **Same model for crew and baseline**, so the comparison measures the architecture, not the model.
- **Why GPT-5.4 mini:** the Gemini free tier allows ~15 requests/minute, which broke RAGAS scoring
  (see LOG-13), and unused OpenAI credits were available. GPT-5.4 mini is current (not deprecated),
  supports function calling and structured outputs, and is positioned for sub-agent workloads.
  Price: $0.75 / 1M input tokens, $4.50 / 1M output tokens.
- **Judge independence (planned):** right now GPT-5.4 mini both answers and grades. Setting
  `EVAL_PROVIDER=gemini` gives an independent judge from another model family; to be revisited.

---

## 8. Agents (and the one step that is deliberately not an agent)

All agents use: the LLM from `get_llm()` (GPT-5.4 mini), `max_iter = 10`, `max_rpm = MAX_RPM` (default 10),
`allow_delegation = False`, and Pydantic structured output (`output_pydantic`).

### 8.1 Spend Profiler
- **Input:** the user's free text.
- **Output:** `SpendProfile`, with monthly ₹ for 11 categories (amazon, flipkart, swiggy, zomato,
  other_online, offline, travel, fuel, utilities, insurance, rent), plus `amazon_prime_member` and
  `assumptions`.
- **Instructions:** convert yearly or weekly amounts to monthly; "20k" = 20,000 and "1 lakh" =
  1,00,000; put anything unmappable in other_online or offline and record that as an assumption;
  Prime is true only if the user says so; never invent spends.
- **Tools:** none.
- **Why it's an LLM:** free text in, structure out is exactly what LLMs are good at.

### 8.2 Card Rules Researcher (the core agent)
- **Input:** one card (id + name).
- **Tool:** `search_card_terms`, locked to that card.
- **Instructions:** must search at least for (1) rates by merchant or category, (2) caps per
  cycle/month/quarter, (3) exclusions, (4) fee, waiver threshold and spends excluded from the waiver,
  (5) revisions.
- **Output:** `CardRules`:
  - `annual_fee_inr` (before GST), `fee_waiver_spend_inr`, `fee_waiver_excluded_categories`
  - `reward_rules[]`: each rule has categories, `rate_pct`, `cap_inr`, `cap_period`
    (statement_cycle/month/quarter/year), `requires_prime`, description and a source citation
  - `base_rate_pct` / `base_cap_inr`: for "all other spends"
  - `excluded_categories`
  - `overall_cap_inr` / `overall_cap_period`
  - `gotchas[]`: fine print, cited
  - `sources[]`
- **Key rules in its prompt:**
  - Categories that earn nothing go in `excluded_categories`, not in a 0% rule.
  - Categories that share one cap go in the same rule.
  - Merchant-level rules (e.g. Myntra only) are mapped to a category only if those merchants are
    most of that category; otherwise the lower rate is kept and a gotcha is added.
  - Prime-only rates get `requires_prime`, plus a separate non-Prime rule.
  - In a PDF covering several cards, it uses only the numbers for this card.
  - A newer revision or effective date wins.
  - Never guess: use null/0 and say so in gotchas.
- **v3 prompt additions** (`V3_RULES` in `app/crew.py`, `RESEARCHER_PROMPT=v3`, LOG-23). Each targets an
  error class seen in the benchmarks; none contains a card-specific number:
  - "Other spends" rates are the **base rate** (`base_rate_pct`), not a rule for one category.
  - Exclude a category only if the documents name it; a revision notice that is silent about a category
    doesn't remove its rewards.
  - Merchants listed in a rule's table map to their categories (Amazon → amazon, Cleartrip → travel).
  - Online-only rules never cover offline; one merchant's special rate doesn't apply to its whole category.
  - Fee tables: use only the row naming this exact card; search again by exact name if it's missing.
  - A second, verification round of searches, and an 11-category checklist before answering.
- **Post-processing:** `card_id` and `card_name` are overwritten with the manifest values ("never
  trust the LLM with identifiers").
- **Caching:** the result is saved to `data/rules_cache/<card_id>.json` with the timestamp, model,
  duration, rules and the passages used.
- The same agent persona is also reused for **Q&A** (`answer_question`), with all 6 cards allowed.

### 8.3 Calculator (deliberately plain Python, not an agent)
`app/calculator.py`, with a deterministic algorithm:
1. For each category with spend: if excluded, the reward is 0. Otherwise pick the **highest-rate rule
   that covers the category and applies to the user** (Prime-only rules are skipped for non-Prime
   users). If no rule matches, use the base rate.
2. **Annualise:** spend × 12, and each cap × periods per year (statement_cycle/month = 12,
   quarter = 4, year = 1). This assumes spending is even across the year.
3. **Per-rule caps:** the sum of uncapped rewards in a rule group is capped. If the cap is hit, the
   capped amount is split back across that group's categories in proportion to spend (so the
   per-category breakdown still adds up), and a "caps hit" message is recorded.
4. **Overall cap:** if total rewards exceed it, everything is scaled down proportionally.
5. **Fee:** the waiver applies if annual spend in non-excluded-from-waiver categories ≥ the
   threshold. Otherwise fee paid = annual fee × 1.18 (GST).
6. **Net value** = rewards − fee paid. Cards are ranked by net value.

It is covered by 9 pytest unit tests (rates, GST, rule caps, proportional split, overall cap,
exclusions vs waiver, waiver exclusions, Prime fallback, quarterly caps, ranking, cap 0 = uncapped).
The v2/v2.1 chunker has 6 more (15 in total).

### 8.4 Fine-print Advisor
- **Input:** the spend profile, plus the calculator's full ranking as text (net, rewards, fee, waiver,
  caps hit, spends earning nothing, known gotchas per card).
- **Tool:** `search_card_terms` across the ranked cards, used to confirm fine print before stating it.
- **Output:** `Advice`:
  - `top_pick_card_id`
  - `summary`: 2–3 plain sentences, including the ₹ gap between #1 and #2
  - for each card, a `verdict`, `watch_outs` and `citations`
  - `assumptions`: even spending, GST included, joining-year offers ignored
- **Guardrail:** it is told the numbers are exact and must not be recomputed, and the code overwrites
  `top_pick_card_id` with the calculator's #1 anyway.

### 8.5 Orchestration: CrewAI Flow
`RecommendFlow(Flow[RecommendState])` with `@start() profile` → `@listen(profile) research` →
`@listen(research) calculate` → `@listen(calculate) explain`.

The state is a Pydantic model: `spend_text`, `spend_profile`, `card_ids`, `refresh_rules`, `rules`,
`ranking`, `advice`, `contexts` and `timings`. A Flow was chosen over one big multi-agent Crew so
the **control flow is deterministic code**, while the agents are used only where judgement is needed.

---

## 9. API (FastAPI)

| Method | Path | What it does | LLM calls |
|---|---|---|---|
| GET | `/health` | config check (model, index, keys set) | 0 |
| GET | `/cards` | cards, which PDFs are present, rules cached or not | 0 |
| POST | `/ingest` | same as the CLI ingestion | 0 |
| POST | `/ask` | question about terms, `mode = agent / baseline` | 1 (baseline) / several (agent) |
| POST | `/recommend` | `mode = crew / baseline`, `refresh_rules` flag | 1 (baseline) / 2+ with warm cache (crew) |
| GET | `/rules/{card_id}` | cached rules + the passages they came from | 0 |
| POST | `/rules/{card_id}/refresh` | re-run the Rules Researcher for one card | several |
| POST | `/calculate` | structured spend profile → ranking from cached rules | 0 |

- The endpoints are sync `def`, so FastAPI runs them in a threadpool. CrewAI's Flow is synchronous,
  and this avoids event-loop clashes.
- Errors: a missing key returns 400 with a readable message; anything else returns 500 with the
  exception type and message.
- Swagger UI (`/docs`) exposes every endpoint for testing.

### Streamlit UI (`ui/app.py`)
A thin front end that only calls the API (`ui/api_client.py`), so the UI and the backend stay decoupled. Five tabs:
- **Recommend:** free-text spend (with 4 example profiles) → best card, net ₹/yr chart, ranking table, per-card category breakdown, caps hit, cited watch-outs. Modes: agents, baseline, or **compare both** side by side on one chart.
- **What-if:** edit any monthly spend and the ranking updates instantly through `/calculate` (no LLM calls, just cached rules plus the calculator).
- **Ask the T&Cs:** agent vs baseline answers side by side, with sources and passages.
- **Card rules:** what the Rules Researcher extracted per card, the passages it used, and a re-extract button.
- **Evaluation:** extraction accuracy, regret and RAGAS charts read from `eval/results/latest.json`.

Run: `uvicorn app.main:app --reload`, then `streamlit run ui/app.py` (http://localhost:8501).

---

## 10. Evaluation design

**Principle:** measure each stage separately, and always against a plain-RAG baseline that uses the
same model.

### 10.1 Gold data
- `eval/gold_rules/<card_id>.json`: rules written by hand from the PDFs, in the same `CardRules`
  format. Each starts as `"_status": "DRAFT"` with notes on what is confirmed, and must be verified
  and switched to `VERIFIED` (the eval warns until then).
- `eval/personas.json`: 5 spend profiles (Amazon Prime heavy, food delivery, big spender with rent,
  Flipkart + offline, light online), each with a natural-language description and structured spends.
- `eval/rule_questions.json`: 13 questions with reference answers, covering caps, exclusions,
  revisions, fee waivers, the Ornge vs BLCK distinction, and Prime vs non-Prime.

### 10.2 Suite 1: extraction (is the Rules Researcher reading correctly?)
- For each card it compares extracted rules with gold rules on **26 fields**: annual fee, waiver
  threshold, effective rate and effective annual cap for each of the 11 categories, the Amazon
  non-Prime rate, and the overall annual cap.
- It compares *effective terms per category* (what the calculator would actually apply) rather than
  JSON structure, so two differently shaped but equivalent rule sets both score correctly.
- Tolerance: ±0.01 for rates and ±₹1 for money (LOG-08). Reported per card and overall: **field-level accuracy**.

### 10.3 Suite 2: recommend (does the user get the right card?)
- Expected ₹ values are computed by running **the same calculator on the gold rules**. So this
  isolates "did the system understand the rules", not "did it do arithmetic the same way".
- **Crew:** cached or extracted rules plus the calculator. The optional `--with-profiler` flag starts
  from free text to include the Spend Profiler.
- **Baseline:** `baseline_recommend` on the same spend, rendered as text.
- **Metrics:**
  - **Top-pick accuracy**
  - **Regret:** ₹/year lost by taking the system's pick instead of the truly best card. This is
    robust to near-ties, where a strict top-pick match would be noisy.
  - **Mean absolute error:** ₹ per card per year. A card the system forgot counts as a full miss.
  - **Share of estimates within 10%** (or ₹250).

### 10.4 Suite 3: qa (RAGAS: are answers grounded?)
- Each question is answered by the agent (`/ask` agent mode) and the baseline. Both return the
  answer and the exact contexts.
- RAGAS 0.4 metrics, with GPT-5.4 mini as the judge (`EVAL_PROVIDER=gemini` switches to Gemini through its
  OpenAI-compatible endpoint). Judge calls are rate-limited (`EVAL_RPM`) and retried, the summary reports how
  many samples each score is based on (`<metric>_n`), and `--rescore` re-judges saved answers:
  - **Faithfulness:** is every claim in the answer supported by the retrieved contexts?
  - **Context recall:** do the contexts contain what the reference answer needs?
  - **Context precision (with reference):** are the relevant contexts ranked high?
  - **Factual correctness (F1):** does the answer match the reference claims?
- Scoring runs async with concurrency 2. One failed judge call records `None` for that metric
  instead of killing the run.
- Average latency per system is also reported.

### 10.5 Cost tracking
Every LLM call (agents, baseline, judge) is recorded with its step name, tokens (input, cached input,
output) and estimated cost (`app/usage.py`). Each suite's report carries its own usage, `summary.md`
ends with a "Cost of this run" table, and API responses include a `usage` block, so every experiment
version can be compared on cost as well as accuracy.

### 10.6 Output
- `eval/results/<suite>_<timestamp>.json`: full per-sample detail.
- `eval/results/latest.json` + `summary.md`: the latest result of each suite merged into one table
  for the README.

---

## 11. Key design decisions (and the reasoning to give in an interview)

1. **"LLMs extract, code calculates."** LLMs are unreliable at multi-step arithmetic with caps and
   proportional limits. The agent's job ends at producing structured, cited rules, and a tested
   calculator does the money. *Trade-off:* the calculator only knows what the schema can express.
2. **The advisor explains, it doesn't decide.** The ranking comes from the calculator. The LLM
   writes the explanation, and the top pick is enforced in code. This prevents a confident LLM from
   "overruling" the maths.
3. **Rules are cached per card.** A card's rules don't depend on the user, so extraction (the most
   expensive step) runs once per card, not once per request. A warm-cache `/recommend` is about 2
   LLM calls plus a few searches. *Trade-off:* when a bank revises terms, you must re-ingest and
   refresh, which `/rules/{card_id}/refresh` exists for.
4. **A CrewAI Flow instead of one autonomous crew.** Deterministic orchestration with typed state,
   with agents only where judgement is needed. It's easier to debug, time and test (the Flow was
   tested with mocked steps).
5. **The tool is locked to one card during research.** A filter in code, not a request in the
   prompt, so the agent can't mix up Ornge and BLCK numbers by searching the wrong card.
6. **Revision-aware metadata.** `doc_type` and `effective_date` are stored on every chunk and shown
   in every passage header, so the agent can apply "newer wins".
7. **Multi-card documents.** `card_ids` is a list, so one chunk can belong to two cards and the
   filter still works.
8. **Every retrieved passage is logged.** Needed for honest RAGAS scoring (you score against what
   the agent actually saw), and stored in the cache as an audit trail.
9. **Integrated embeddings in Pinecone.** No separate embedding service or key, and documents and
   queries are always embedded with the same model.
10. **Fixed spend taxonomy (11 categories).** It keeps extraction and calculation consistent.
    *Trade-off:* merchant-level rules have to be approximated, which is flagged as a gotcha.
11. **Pydantic everywhere.** Agent outputs are validated objects, not strings to parse, and the same
    models are reused by the API, the calculator and the evaluation.
12. **Same model for baseline and crew.** The comparison isolates architecture from model quality.
13. **The eval compares effective terms, not JSON shape.** It's fair to equivalent but differently
    structured extractions.
14. **Regret as the headline recommendation metric.** It's what a user actually loses, and it's
    robust to near-ties.
15. **Resilience.** Retries with backoff and per-attempt timeouts on every LLM call, a rate limiter on the judge, retries on Pinecone
    upserts, `MAX_RPM` throttling for the free tier, and missing PDFs skipped instead of crashing.
16. **Small safeguards.** Card ids and names are always taken from the manifest; curly braces are
    stripped from dynamic task text (CrewAI treats `{x}` as template variables); CrewAI tracing and
    telemetry are disabled.

---

## 12. Data contracts (quick reference)

- **SpendProfile:** monthly ₹ for each of the 11 categories, `amazon_prime_member`, `assumptions`.
- **RewardRule:** `categories[]`, `rate_pct`, `cap_inr`, `cap_period`, `requires_prime`,
  `description`, `source`.
- **CardRules:** fee, waiver threshold, waiver exclusions, `reward_rules[]`, base rate and cap,
  `excluded_categories[]`, overall cap, `gotchas[]`, `sources[]`.
- **CardValue:** annual spend, gross rewards, fee paid, fee waived, net value, `lines[]` per category
  (spend, rate, rule applied, reward) and `caps_hit[]`.
- **Advice:** top pick, summary, per-card verdict, watch-outs and citations, assumptions.
- **BaselineRecommendation:** top pick, per card: estimated rewards, fee, net, reasoning.

---

## 13. Repository map

```
app/config.py      settings from .env (keys, models, index, top_k, MAX_RPM, CREW_VERBOSE)
app/schemas.py     all Pydantic models + the 11-category taxonomy
app/manifest.py    reads data/cards.yaml
app/ingest.py      PDF -> chunks -> Pinecone
app/chunking_v2.py PDF -> Markdown -> section-aware chunks, merged table cells (v2 / v2.1)
app/retriever.py   Pinecone search + passage formatting
app/tools.py       search_card_terms CrewAI tool (logs passages)
app/llm.py         provider-aware LLM factory (OpenAI / Gemini, retry + timeout), plain and structured calls
app/usage.py       token + cost tracking for every LLM call (agents, baseline, judge)
app/calculator.py  deterministic valuation + ranking
app/baseline.py    plain RAG baseline (ask + recommend)
app/crew.py        agents, steps, rules cache, RecommendFlow
app/main.py        FastAPI endpoints
data/cards.yaml    cards and which PDF covers which card (doc_type, effective_date)
data/rules_cache/  extracted rules per card (+ passages used)
eval/              gold rules, personas, questions, run_eval.py, ragas_judge.py, results/
tests/             calculator + chunker unit tests
```

---

## 14. Questions you are likely to be asked (with answers)

**Q: Why CrewAI and not LangGraph?**
I wanted role-based agents with tools and structured outputs, plus deterministic orchestration.
CrewAI's Flow gives me the deterministic part (typed state, `@start`/`@listen` steps) and its agents
give me the tool-using part. LangGraph would give finer control over state transitions and cycles.
I've used LangGraph before, and for a linear four-step pipeline with one tool, both work; here I
wanted to show CrewAI.

**Q: Why not let one agent do everything?**
Because the failure modes differ. Extraction needs careful reading and citations, the maths needs
exactness, and the explanation needs clarity. Splitting them lets each step be cached, tested and
measured separately. The extraction suite and calculator tests exist because of this split.

**Q: How do you stop the LLM hallucinating numbers?**
At four levels: (1) retrieval is locked to the right card; (2) the prompt says "never guess, use
null and say so" and requires a citation per rule; (3) the arithmetic is done in code, not by the
LLM; (4) the extraction eval compares every number against hand-checked gold rules, and RAGAS
faithfulness checks that answers are supported by the retrieved text.

**Q: How do you handle conflicting documents?**
Every chunk carries `doc_type` (base/revision) and `effective_date`, which appear in the passage
header the agent reads, and the agent is told the newer rule wins and must record the change as a
gotcha. Example: SBI's April 2026 revision adds gaming, tolls and government payments to the exclusions.

**Q: How did you choose your chunking?**
I started with a deliberately simple baseline: 180-word windows with 40-word overlap, per page. The
benchmark showed its failures were structural: a fee read from the neighbouring table row, a table cut
at a page break, and two cards' passages mixed in one PDF. So v2 parses to Markdown with pymupdf4llm,
chunks by the document's headings, never cuts a table row (big tables split between rows with the
header repeated), scopes sections to the card they describe, and puts a "card | section" line on each
chunk. I kept v1 behind a setting and benchmarked both with the same model, prompts and gold rules,
so the difference is attributable to chunking.

**Q: Did the better chunking improve the results?**
Retrieval improved clearly: context recall went from 0.83 to 0.96 for the agent and 0.71 to 1.00 for the
baseline, and the table-row errors (SBI fee) disappeared. Extraction accuracy actually dropped 5 points,
and I traced why: Markdown can't represent merged table cells, so a cap shared across rows was lost for
one card. The ranking metrics looked much better too, but I checked per-card errors and found the
over-valued card had just moved from one card to another, so I didn't claim that gain. Each run's
errors are logged by cause, which tells me whether the next fix belongs in chunking or in the prompt.

**Q: How do you know a change helped and isn't noise?**
I write a prediction before each run and check which fields changed. In v2.1 the fix targeted one table;
exactly those fields were fixed, but other cards whose chunks hadn't changed got worse. That told me the
extraction has large run-to-run variance, so I don't read a few points of difference as a real effect.
I only claim a gain when several metrics move together (as in v3, where accuracy, per-card error and
within-10% all improved). Repeat runs for proper error bars are the next step; I skipped them to stay
within the time budget.

**Q: What did you learn from the experiments, in one minute?**
Four things. (1) The agentic pipeline with a deterministic calculator beat plain RAG in every run; in
the final run its per-card valuation error was ₹1,172 vs ₹6,545. (2) Better chunking fixed retrieval
(context recall 0.83 → 0.96) and table errors, but not reasoning. (3) Most remaining errors were the
agent mis-mapping categories on passages it already had, which six generic prompt rules fixed: extraction
66% → 77%, valuation error halved. (4) Metrics on small samples are noisy, so I wrote predictions before
each run, diffed results field by field, and didn't claim gains that a second metric didn't confirm.

**Q: Why dense search only? What about exact terms?**
Dense search is enough for natural-language rules. Exact tokens like MCC codes ("6513") are where
dense search is weak, so the next step is Pinecone hybrid search (dense + sparse) and a reranker.

**Q: How would this scale to 100 cards?**
Ingestion and the index scale fine. Rule extraction is per card and cached, so it's a one-time
batch job (parallelisable) that only re-runs when a card's documents change. The per-request cost
doesn't grow with the number of PDFs, only the calculator loop does, and that's microseconds.

**Q: What does a request cost and how long does it take?**
Baseline: 1 search + 1 LLM call. Crew with a warm cache: profiler + advisor (plus the advisor's
searches). Crew with a cold cache: plus about 6 extraction runs with several tool calls each. In
testing, an overloaded `gemini-3.8-flash` took 660s for one call, which led to adding retry and
timeouts and switching to `gemini-3.5-flash-lite`, and later to GPT-5.4 mini. Estimated cost of the full
benchmark plan (3 cycles) on GPT-5.4 mini: about $5–6. Planned speed-ups: parallel extraction and a
different model per step.

**Q: Why did you switch models in the middle of the project?**
The Gemini free tier's 15 requests/minute broke RAGAS scoring. I re-ran the "before" benchmark on the new
model before changing anything else, so model effects and chunking effects are measured separately, and
the Gemini run became a model comparison on an identical pipeline.

**Q: How do you know the eval is right?**
The gold rules are written by hand from the PDFs and marked VERIFIED only after checking. Expected
recommendation values come from the same tested calculator, so the recommend suite measures rule
understanding only. RAGAS uses reference answers written from the documents.

**Q: Why RAGAS plus custom metrics?**
RAGAS answers "is the answer grounded and relevant", but it can't tell you whether the user got the
right card or how many rupees a wrong rule costs. Regret and field-level accuracy answer that.

**Q: What if the bank updates its terms?**
Add the new PDF to `cards.yaml` as a `revision` with its `effective_date`, re-run ingestion, call
`/rules/{card_id}/refresh`, and re-run the extraction eval for that card.

**Q: What would you do next?**
Repeat runs for error bars, hybrid search + reranking, parallel extraction, a per-step model choice,
merchant-level rules (finer categories), joining-year and milestone benefits,
deployment, and tracing (e.g. Langfuse) for per-step cost and latency.

---

## 15. Assumptions and limitations

- Spend is assumed even across the year (monthly caps are annualised).
- Joining-year offers, welcome benefits and milestone vouchers are ignored. The fee is treated as a
  recurring renewal fee.
- Minimum transaction sizes (e.g. ₹249 for Swiggy cashback) are not modelled. They're mentioned as fine print.
- Reward points are valued at the rupee rate stated in the documents. All 6 cards here are cashback cards.
- Category mapping is coarse (11 categories), and merchant-level rules are approximated.
- Tables are parsed as Markdown with merged cells restored; tables whose structure PyMuPDF and
  pymupdf4llm disagree on (38 of 100) are left as pymupdf4llm produced them.
- It runs locally, with no authentication, no rate limiting on the API and no deployment yet.

---

## 16. Current status and open items

Every fix and experiment since the first build is recorded in `ENGINEERING_LOG.md` (issue, cause, change, result).

- Ingestion: 9 PDFs (renamed `bank_card_doctype_date.pdf`). v1 chunker: 650 chunks; v2 / v2.1 chunker (LOG-19, LOG-21; v2.1 default): 397 chunks.
- Gold rules: verified against the PDFs with page references (2026-10-27).
- Streamlit UI built and working.
- Benchmark `v1_pymupdf_fixed` (Gemini flash-lite): extraction 85.9%; crew top pick 40% vs baseline 20%;
  crew per-card error ₹1,794 vs ₹3,979; RAGAS incomplete because of free-tier rate limits.
- Switched agents, baseline and judge to OpenAI GPT-5.4 mini (LOG-14), with token and cost tracking (LOG-16).
- Benchmark `v1_pymupdf_fixed_gpt54mini`: extraction 74.4%; crew regret ₹708 vs baseline ₹3,584; crew
  per-card error ₹2,040 vs ₹5,704; RAGAS context recall 0.83 vs 0.71 and precision 0.83 vs 0.75
  (agent vs baseline); run cost $0.50. Model comparison and error patterns in LOG-18.
- v2 chunker built (pymupdf4llm Markdown, section-aware, tables whole, card-scoped, context line; LOG-19).
- Benchmark `v2_pymupdf4llm_sections` (LOG-20): retrieval clearly better (RAGAS context recall agent
  0.83→0.96, baseline 0.71→1.00; factual correctness 0.52→0.65); SBI fee/waiver and the BLCK/ICICI
  leaks fixed. Extraction 74.4%→69.2%: a new merged-table-cell failure on Ornge (shared cap lost in
  Markdown) and category-mapping slips. The crew's 60% / ₹4 regret is luck (the over-valued card moved
  from BLCK to Ornge); per-card error unchanged. Run cost $0.58.
- v2.1 chunker built (merged table cells restored; LOG-21). Default chunker.
- Benchmark `v2_1_merged_cells` (LOG-22): the targeted fix worked (Ornge 54%→77%, all predicted fields),
  but overall extraction fell to 66% because of run-to-run category-mapping slips on unchanged chunks
  (BLCK, ICICI, SBI fee). Found a retrieval weakness: the SBI fee table's six row-chunks share one context
  line, so the right row isn't reliably retrieved.
- v3 prompt built (LOG-23): category coverage, base rate, exclusions, merchant lists, fee rows, verification round.
- Benchmark `v3_prompt_rules` (LOG-24): extraction **77%** (best GPT-5.4 mini run; 22 fields fixed, 5
  broken); crew per-card error **₹1,172** vs baseline ₹6,545; within 10% 57%; top pick 80% vs 20%. Run cost $0.62.
- **Status: concluded.** Summary table and lessons in `ENGINEERING_LOG.md` → "Conclusion". Not pursued, by
  choice: repeat runs for error bars, hybrid search, corpus gaps, an independent judge, held-out cards.
- Known gaps: Millennia fee not in the ingested PDFs; Flipkart Axis base rate only implied; the eval
  doesn't check shared vs separate caps.
- Remaining: demo video. Optional later: the latency improvements in section 14, an independent (Gemini) judge, deployment.
