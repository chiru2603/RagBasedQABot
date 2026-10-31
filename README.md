# Governance Bot — vanilla RAG starter

A small, readable Python baseline for the AI Engineering capstone. No agent, tool routing, hybrid search or reranker yet. The application uses OpenAI embeddings and the Responses API; SQLite persists chunks and vectors locally. Retrieval performs exact cosine search in Python, appropriate for this small corpus rather than a large production index.

## Quick start (macOS/Linux, Python 3.10+)

Extract the archive, open a terminal in `RagBasedQABot`, then run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env` and set your own `OPENAI_API_KEY`. Never commit or share it. Models are configurable; the sample uses `gpt-4.1-mini` and `text-embedding-3-small`. Your account must have access to the configured models. OpenAI requests incur API charges; a ChatGPT subscription is separate.

```bash
python -m governance_bot ingest --docs docs
python -m governance_bot ask "What approval is needed to access Restricted data?"
python -m governance_bot search "How should a dataset be deprecated?"
python -m governance_bot chat
```

`chat` is a terminal Q&A loop; each question is independent. Exit with `/quit`. Ask an ownership question such as “Who owns analytics.orders?” to check that the model explains its lack of live evidence rather than inventing a person.

## Follow the data flow

Ingestion: MD/TXT/PDF → heading/page sections → overlapping character chunks → OpenAI embeddings → SQLite snapshot.

Question: query embedding → cosine scores → top 4 chunks → evidence + question sent to OpenAI → answer JSON → citation ID validation → answer and source excerpts.

`src/governance_bot/` contains the implementation:

- `read_document`: Markdown/text and text-based PDF loading. Scanned PDF requires OCR outside this starter.
- `chunks`: heading-aware windows, 1,800 characters with 200-character overlap. This is character splitting, not a token-aware semantic chunker.
- `OpenAIBackend`: the only API-specific code; injectable for offline testing.
- `RAG.ingest`: full snapshot rebuild. Re-running replaces old chunks, including removed files. Embeddings are regenerated every run. Incremental ingestion is deferred.
- `RAG.retrieve`: exact cosine ranking. `search` exposes sources and scores without answer generation.
- `RAG.ask`: evidence-only prompt, abstention instructions, citation membership checks and token/latency reporting.

`--top-k` accepts 1–10. Optional `--min-score` accepts -1–1; its default disables a relevance cutoff. Similarity is not calibrated confidence: tune a threshold against reviewed cases instead of assuming a universal value. Abstention is principally model-based and must be evaluated.

```bash
python -m governance_bot ask "What does Internal mean?" --top-k 3
python -m governance_bot --db data/alternate.sqlite ingest --docs docs
```

## Add your own documents

Put approved `.md`, `.txt` and text-based `.pdf` files in a directory and run ingestion against that directory. Do not mix real policy documents with the synthetic examples. The capstone design PDF describes architecture; it is not an authoritative governance policy source.

```bash
python -m governance_bot ingest --docs /path/to/approved-policies
```

Ingestion errors leave the prior snapshot intact. An empty corpus is rejected to avoid accidental clearing. Source references are relative filenames and section/page labels, not enterprise URLs. `indexed_at` is index build time, not a policy's last-modified date. Content hashes are recorded for a later incremental-ingestion implementation. Changing the embedding model requires rebuilding the index.

## Offline verification

```bash
python -m unittest discover -s tests -v
```

Tests use a fake backend, no API key and no network. They check ranking/source mapping, missing or invented citations, abstention, replacement of old content, preservation after embedding failure, empty-input preservation, model mismatch and chunk boundaries. They do not establish real-model answer quality or OpenAI integration success. Live ingestion and Q&A were not executed in the generation environment because no API credentials were configured.

## Manual quality checks

Use `evals/cases.json` as a starting checklist. Run each question, compare required facts/behavior, then inspect cited excerpts. Record correctness, groundedness, citation support, abstention, latency and usage. This is a seed set, not the complete 50-question capstone evaluation. Citation ID validation checks that a cited chunk was retrieved; it does not prove the chunk supports the claim.

## Boundaries and next steps

This is a single-user local prototype for approved broadly shared documents. There is no authentication or ACL filtering. Documents and questions are sent to the configured OpenAI service. Retrieved instructions are treated as untrusted by the system prompt; this mitigates but does not eliminate prompt injection. No write tools are exposed. No live ownership, lineage or classification data is available. No server or browser UI is included.

Next: run the baseline against your corpus, annotate evidence and expected answers, then add live catalog lookups. Later compare hybrid retrieval + reranking against this same baseline and build the LangGraph remediation flow separately.

Dependencies have compatible ranges in `pyproject.toml`. Capture a lockfile after installing in your target environment.

Official API references used:
- https://developers.openai.com/api/reference/python/resources/responses/methods/create
- https://developers.openai.com/api/reference/resources/embeddings/methods/create

## Repository layout

```text
src/governance_bot/
  cli.py          # command line entry points
  ingestion.py    # file parsing and heading-aware chunking
  providers.py    # OpenAI adapter and answer instructions
  retrieval.py    # index lifecycle, ranking and answer validation
tests/            # network-free unit tests
docs/             # fictional sample corpus
evals/cases.json  # seed evaluation questions
.github/workflows/ci.yml
pyproject.toml
```

The installed command `governance-bot` is equivalent to `python -m governance_bot`.
