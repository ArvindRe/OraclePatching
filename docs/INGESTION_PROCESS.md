# Ingestion Process

> How content and release awareness actually get into this system, end to
> end — what's real and running today vs. what's still manual. Companion to
> [DB_PATCHING_SCOPE.md](../Patching_Agentic/DB_PATCHING_SCOPE.md)'s
> Knowledge Base design and [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md).

There are **two separate pipelines** here, easy to conflate but answering
different questions, with different cadences and different trust models:

1. **Knowledge-content ingestion** — "what does the RAG store know about
   Oracle patching mechanics?" Curated, reviewed, low-frequency (monthly).
2. **Patch-release monitoring** — "has a new quarterly Database RU shipped?"
   Automatic, public, can run far more often than it needs to (the answer
   only actually changes ~4 times a year).

Neither pipeline ever downloads an actual Oracle patch. Both stop at
"here's what a human should look at" — the patch download itself stays
manual and MOS-gated, same as [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md#3-how-do-we-download-patches-for-testing-without-a-commercial-account) already covers.

---

## 1. Knowledge-content ingestion

```
┌─────────────────────┐        ┌──────────────────────┐        ┌─────────────────┐
│  knowledge_staging/  │        │ dmz_transfer.py       │        │ knowledge_inbox/ │
│  (curated, reviewed, │──────► │ one-directional copy  │──────► │ (gitignored —     │
│  tracked in git)     │        │ + sha256 manifest      │        │ derived, never    │
│                      │        │ + append-only log      │        │ hand-edited)      │
└─────────────────────┘        └──────────────────────┘        └────────┬────────┘
                                                                          │
                                                                          ▼
                                                                 ┌─────────────────┐
                                                                 │   ingest.py      │
                                                                 │ embeds + upserts │
                                                                 └────────┬────────┘
                                                                          │
                                                                          ▼
                                                                 ┌─────────────────┐
                                                                 │  Qdrant           │
                                                                 │  oracle_procedures│
                                                                 └─────────────────┘
```

### Step 1 — Curate content into `Patching_Agentic/rag/knowledge_staging/`

This is the only step that involves judgment, not just running a script.
Each file is a `KnowledgeObject` YAML (`rag/ingestion/schema.py`):
`id`, `title`, `category`, `applicable_min_version`/`applicable_max_version`,
`preconditions`/`steps`/`validation`/`rollback`, `source`.

**What determines whether something gets added** — the full criteria live
in `DB_PATCHING_SCOPE.md`'s Knowledge Base section, summarized:

- In-scope domain (19c / RAC / ASM / Data Guard / RMAN / OPatch) and version.
- Tied to something this codebase actually does or has a flagged gap for —
  not generic background reading.
- Real, citable source — official `docs.oracle.com` preferred, 2+
  independent practitioner sources cross-checked where official docs are
  thin, never uncited.
- Decomposes honestly into preconditions/steps/validation/rollback.
- Never a verbatim copy of licensed Oracle content (patch READMEs, MOS note
  text) — summarize and cite.

6 objects exist today, covering OPatch conflict analysis, Data Guard patch
ordering, RMAN backup validation, datapatch CDB/PDB behavior, RAC rolling
patching, and Grid Infrastructure/ASM patching — each traceable to a real
source and, in most cases, to a gap this project had already flagged before
the knowledge object did.

### Step 2 — Transfer: `python -m rag.ingestion.dmz_transfer`

Simulates `DB_PATCHING_SCOPE.md`'s "one-directional DMZ transfer from a
connected staging machine" — on a real air-gapped deployment, this step is
a genuine network boundary crossing; here, on one dev machine, it's a
directory copy, but the *boundary* is real: `ingest.py` never reads
`knowledge_staging/` directly, only `knowledge_inbox/`.

What it does, exactly:
- Mirrors every `*.yml` in `knowledge_staging/` into `knowledge_inbox/`
  (files removed from staging are removed from inbox too — a transfer is a
  fresh copy each time, not an accumulating sync).
- Writes `knowledge_inbox/MANIFEST.json`: transfer timestamp, and per-file
  name/sha256/size — the provenance record for "what's actually in the
  knowledge base right now."
- Appends one line to `rag/dmz_transfer_log.jsonl` (tracked in git, unlike
  `knowledge_inbox/` itself) — the durable history of every transfer ever
  run.

A fresh clone of this repo has an **empty** `knowledge_inbox/` — same state
as a fresh air-gapped box before its first monthly transfer. Nothing is
ingestible until this step runs at least once.

### Step 3 — Ingest: `ingest.py`

```python
from rag.ingestion.ingest import load_knowledge_objects, ingest
from rag.ingestion.embeddings import SentenceTransformerEmbedder
from qdrant_client import QdrantClient

objects = load_knowledge_objects(Path("rag/knowledge_inbox"))
ingest(QdrantClient(host="localhost", port=6333), "oracle_procedures",
       SentenceTransformerEmbedder(), objects)
```

Embeds each object (title + category + preconditions + steps + validation +
rollback, flattened — see `KnowledgeObject.embedding_text()`) and upserts
into Qdrant, keyed by a deterministic UUID5 of the object's `id` (Qdrant
requires uint/UUID point IDs, not arbitrary strings — re-running ingest on
the same `id` updates in place, doesn't duplicate).

**Two embedders exist, deliberately different in purpose:**
- `SentenceTransformerEmbedder` — the real one, real semantic search.
  Requires the model already cached (air-gapped box has no runtime internet
  access to fetch it on demand).
- `DeterministicTestEmbedder` — proves the Qdrant plumbing works (collection
  creation, upsert, filtering) without a model download. Used by default in
  `demo.py` for exactly that reason; pass `--real-embeddings` to use the
  real one there instead.

### Cadence

**Monthly**, per `DB_PATCHING_SCOPE.md`. This is about how often *curated
content* gets refreshed, not how often the mechanical steps 2–3 could run
(they're cheap and idempotent — running `dmz_transfer` + `ingest` daily
would be harmless, just pointless if nothing in `knowledge_staging/` has
changed). The real bottleneck is step 1 — a human (or an AI session,
reviewed by a human) actually curating new content — not the pipeline.

### Run it yourself

```bash
cd Patching_Agentic
source .venv/bin/activate
python -m rag.ingestion.dmz_transfer
python3 -c "
from pathlib import Path
from qdrant_client import QdrantClient
from config.settings import load_settings
from rag.ingestion.embeddings import SentenceTransformerEmbedder
from rag.ingestion.ingest import load_knowledge_objects, ingest

settings = load_settings()
objects = load_knowledge_objects(Path('rag/knowledge_inbox'))
ingest(QdrantClient(host=settings.qdrant.host, port=settings.qdrant.port),
       settings.qdrant.collection, SentenceTransformerEmbedder(), objects)
print(f'Ingested {len(objects)} objects into {settings.qdrant.collection}')
"
```

Or just run `demo.py` — its Step 2/3 do exactly this (with the placeholder
embedder by default).

---

## 2. Patch-release monitoring

Answers a completely different question: not "what do we know," but "does
a human need to go get something new." Doesn't touch `knowledge_staging/`,
doesn't touch Qdrant, never downloads a patch.

### The source

[oracle.com/security-alerts](https://www.oracle.com/security-alerts/) is
Oracle's own public Critical Patch Update advisory index — **no MOS login
needed**. The page itself links a real RSS feed (found by inspecting its
page source, then verified directly, not assumed):

```
https://www.oracle.com/ocom/groups/public/@otn/documents/webcontent/rss-otn-sec.xml
```

Confirmed live: real RSS 2.0, reachable with both `curl` and Python's
default `urllib` (no special headers needed — an earlier 403 from the
`WebFetch` tool specifically turned out to be unrelated to this feed, not a
general block).

**The feed mixes two advisory types with easily-confused names** — this
distinction is the one thing worth understanding before trusting this
mechanism:

| Title pattern | What it is | Carries a new DB RU? |
|---|---|---|
| "Oracle Critical Patch Update Advisory - *Month Year*" | The quarterly one, 3rd Tuesday of Jan/Apr/Jul/Oct | **Yes** — this is what this project cares about |
| "Oracle Critical Security Patch Update Advisory - *Month Year*" | A newer (started 2026-05-28), narrower **monthly** advisory for targeted CVE fixes | No — different, smaller thing |

`rag/ingestion/patch_release_monitor.py`'s regex
(`^Oracle Critical Patch Update Advisory\b`) matches only the first,
verified against a real fetched feed containing both kinds mixed together
(the live feed run below shows both `CPUJul2026` and `CSPUSep2026` — only
the former is treated as relevant).

### What it does

```bash
cd Patching_Agentic
python -m rag.ingestion.patch_release_monitor
```

1. Fetches the RSS feed.
2. Filters to quarterly CPU entries only (excludes monthly CSPU and one-off
   CVE alerts).
3. Compares the newest entry's GUID against `rag/patch_release_monitor_state.json`
   (gitignored — a cursor, not a log; regenerated on next check).
4. Prints whether there's a new one since last check, and updates the
   cursor either way.

That's the entire contract. It never writes to `knowledge_staging/`, never
triggers a DMZ transfer, never touches MOS or downloads anything — a "yes,
new one shipped" signal is purely informational. Verified live:

```
56 quarterly CPU advisory entries in the feed. Most recent 3:
  - Oracle Critical Patch Update Advisory - July 2026 (Tue, 21 Jul 2026 ...)
    https://www.oracle.com/security-alerts/cpujul2026.html
  ...
NEW since last check: Oracle Critical Patch Update Advisory - July 2026
```

Second run with the same feed correctly reports "No new quarterly CPU
advisory since the last check."

### Cadence

Unlike knowledge-content ingestion, **there's no real cost to checking
often** — it's one small HTTP GET. The answer only actually changes ~4
times a year (3rd Tuesday of Jan/Apr/Jul/Oct), so daily is already
over-frequent relative to the real event rate, but harmless. Running it as
part of the same monthly cycle as knowledge-content ingestion is reasonable
and keeps both pipelines on one predictable schedule — there's no strong
reason to run it more often than that in practice, even though nothing
stops you.

### What a human does with a "new release" signal

Nothing automated. `cowork_prompt/download_cpu_patch.md` already documents
the manual path: MOS login (support-contract-gated), find the patch number
via MOS Note 1454618.1, download, hand to this project's
`ansible/vars/patches/<patch_id>.yml`. This monitor just removes the need
to manually check whether it's time to do that.

---

## What's NOT automated (by design, not oversight)

- **Curating new knowledge objects.** Steps 2–3 of pipeline 1 are
  mechanical; step 1 (deciding what's worth adding, sourcing it correctly)
  isn't, and shouldn't be — see the criteria in `DB_PATCHING_SCOPE.md`.
- **The actual DMZ network crossing.** This repo simulates the boundary
  (staging → inbox, one directory to another) on a single dev machine — a
  real air-gapped deployment's actual transfer mechanism (write-once media,
  a real DMZ hop) doesn't exist here and hasn't been designed — see
  `OPEN_QUESTIONS.md` question 4 for the same open question as it applies
  to patch zips specifically.
- **Downloading the patch itself.** Always MOS-gated, always manual, by
  design — see `OPEN_QUESTIONS.md` question 3.
- **Acting on a "new release" signal.** The monitor only informs; nothing
  auto-triggers a download, a knowledge-base update, or a patch run.

---

## Changelog

- 2026-09-22T21:07:55+05:30 — Initial version — both ingestion pipelines documented end to end, including the new patch_release_monitor.py built and verified live against Oracle's real public RSS feed — Arvind Regukumar
