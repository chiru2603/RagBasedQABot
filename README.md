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

## Google Drive source connector

The read-only Drive connector previews direct children of one configured folder.
It does not recurse into subfolders or follow shortcuts. It supports Google Docs,
UTF-8 text/Markdown, and text-based PDFs; other file types are excluded.
These commands do not call OpenAI, change SQLite, or schedule ingestion.

```bash
python -m pip install -e ".[drive]"
```

For the initial service-account setup:

1. Enable Google Drive API in your Google Cloud project.
2. Create a dedicated service account and share the approved source folder with
   its email address as a Viewer. Do not enable domain-wide delegation.
3. Store its credential JSON outside the repository. Add these settings to `.env`:

```dotenv
GOOGLE_DRIVE_FOLDER_ID=16Efg8h0A7777FiF5ylKtDGGL16TsLjt1
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/outside/repo/google-drive-reader.json
```

The connector uses Application Default Credentials with the `drive.readonly`
scope. Externally provisioned OAuth ADC credentials with that scope can also be
used; this repo does not implement an interactive OAuth login flow. The chat's
connected Drive plugin does not provision credentials for this standalone app.

```bash
python -m governance_bot sources list
python -m governance_bot sources fetch DRIVE_FILE_ID
python -m governance_bot sources list --folder-id ANOTHER_FOLDER_ID
```

Listing returns source/document IDs, titles, MIME types, original URLs, source
modification times, versions, and checksums when Drive provides them. `complete`
is emitted only after all pages succeed; errors and incomplete searches exit
nonzero without publishing a partial listing. An empty accessible folder is a
valid complete listing. Discovery is not a transactional snapshot of Drive;
incremental sync checks a second complete listing before committing changes.

Fetching verifies folder membership and download permission, downloads stored
files or exports Google Docs as plain text, normalizes Unicode and line endings,
and returns extracted text with its SHA-256 hash. Google Docs exports lose heading
formatting in this initial version. Google limits exports to 10 MB. PDFs require
extractable text; OCR is not provided. A metadata check after fetching rejects
files changed or moved during extraction. Fetch output contains document text;
do not redirect it into shared operational logs.

Daily scheduling is a subsequent step. The existing `ingest --docs` command
continues to rebuild the local-file index.

API references: [files.list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list),
[download and export](https://developers.google.com/workspace/drive/api/guides/manage-downloads).

## Incremental Drive indexing

After configuring the connector, sync into a dedicated index:

```bash
python -m governance_bot --db data/drive.sqlite sync
python -m governance_bot --db data/drive.sqlite sync
python -m governance_bot --db data/drive.sqlite search "How long does temporary access last?"
python -m governance_bot --db data/drive.sqlite ask "How long does temporary access last?"
```

The first sync embeds the approved folder's documents. Subsequent syncs skip
documents with unchanged metadata and a source version, timestamp, or checksum.
Documents without those markers are fetched and hashed on every run. Changed
documents are chunked again, but exact matching chunk text reuses stored vectors.
Metadata-only edits update citations without new embeddings. Output reports
added, updated, unchanged, removed, fetched, embedded/reused chunks, embedding
calls, current document/chunk totals, and elapsed time. Updated counts include
metadata-only edits; embedded chunk counts represent unique texts sent to the API.

The `documents` table tracks `(source_id, document_id)`, source metadata, the full
text hash, and document indexing time. Each chunk carries the same IDs, source
URL, title, source modification date, section, and its own indexing time. Source
update dates and indexing times are separate. Existing `ask` and `search`
commands read this representation without a separate retrieval implementation.

A sync takes a SQLite write reservation before discovery, prepares all changes,
validates embeddings, and checks that a second complete source listing agrees
before committing. Failures roll back the whole run, including planned removals.
Concurrent writers fail with a database-locked error; retry after the active run.
Readers can continue using the previous committed snapshot during preparation.
Missing documents are removed only for the current source; an accessible empty
folder confirmed twice removes that source's indexed documents. Drive listings
are not transactional snapshots: stable omissions caused by access changes may
also remove documents from the index. They are never deleted from Drive.

Local snapshot ingestion and incremental sync intentionally cannot share an
index. Keep `data/index.sqlite` for the existing local baseline and explicitly
use `--db data/drive.sqlite` for Drive sync and Q&A. To change embedding model or
chunking configuration, build a new database; incompatible vectors are rejected.
No daily schedule is installed by this command. Failed runs are reported to
stderr with a nonzero exit; persistent run history and notifications are deferred.

Manual verification: sync twice and expect zero embeddings on the second run;
add a disposable Markdown file, sync, edit one section, and sync again. Inspect
SQLite and retrieved source excerpts for the changed text. Move the fixture into
an excluded subfolder, sync, and verify only its indexed rows disappear. Keep
the approved policy documents unchanged during this exercise.

## Offline verification

```bash
python -m unittest discover -s tests -v
```

Tests use fake backends, no API key and no network. They cover ranking, citations,
abstention, chunk boundaries, connector pagination/failures, incremental updates,
vector reuse, source-scoped removals, rollback, model mismatch, and writer locking.
They do not establish real-model answer quality; run live verification separately.

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
  sources.py      # read-only Google Drive connector
  sync.py         # transactional incremental source indexing
tests/            # network-free unit tests
docs/             # fictional sample corpus
evals/cases.json  # seed evaluation questions
.github/workflows/ci.yml
pyproject.toml
```

The installed command `governance-bot` is equivalent to `python -m governance_bot`.
