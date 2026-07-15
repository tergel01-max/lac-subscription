# LAC Book Translation Helper

An AI-assisted book/translation post-editing system built on your ERPNext.

**Flow:** Original (English) → existing Mongolian draft → **AI-improved suggestion** → human **Accept / Edit** → export the finished Mongolian book.

It automates what you do by hand in ChatGPT (paste a sentence, get a better
Mongolian version), but across a whole book — **without dropping any
sentence** — and keeps every original, draft, and suggestion in ERPNext so you
(or a team) can review and accept them.

## Architecture (Approach B: DocTypes + external worker)

- **ERPNext holds the data + review UI.** Two custom DocTypes (already created
  in your instance, module *LAC Custom*):
  - **Translation Project** — one per book: languages, model, glossary/style
    guide, progress, token/cost totals.
  - **Translation Segment** — one row per sentence: `source_text` (EN),
    `draft_text` (MN), `ai_suggestion` (improved MN), `final_text` (accepted),
    `status`, and per-segment token/cost tracking.
- **The worker (`lac_translate_worker.py`) does the AI.** It runs on your
  machine, reads Pending segments over the ERPNext REST API, calls the OpenAI
  API in small **count-enforced batches**, and writes suggestions back. No
  server-side code is deployed to your bench.

### Why batches (not one sentence at a time)
Sending whole chapters makes the model summarize and drop sentences. Sending
one sentence per request is expensive and loses context. So the worker:
- stores **one row per English sentence** (nothing can be lost), and
- sends them in numbered batches (default 8) where the model **must return
  exactly one improved sentence per input id**. If any come back missing, it
  automatically retries them individually. → no dropped sentences, with context.

## Setup

1. `pip install -r requirements.txt`
2. In ERPNext: your user → **API Access → Generate Keys** (API key + secret).
3. Fill in `.env` from `.env.example` (`ERPNEXT_URL`, `ERPNEXT_API_KEY`,
   `ERPNEXT_API_SECRET`) and export them.

**OpenAI key:** you do **not** need to set one separately. If `OPENAI_API_KEY`
is unset, the worker reuses the key already configured in ERPNext
(*Raven Settings → OpenAI API Key*, incl. org/project IDs). Set
`OPENAI_API_KEY` only to override.

## Testing

`python test_worker.py` runs an offline test (no network/key/ERPNext needed)
that stubs OpenAI + the ERP client and verifies the pipeline — including the
guarantee that a dropped sentence is detected and retried, never lost.

## Use it

**1. Create a project** in ERPNext (Translation Project list → New): set the
title, model (e.g. `gpt-4o-mini` to start cheap), and a glossary/style guide.
Note its name, e.g. `TRP-00001`.

**2. Import the book** (segments the source into sentences and creates rows):
```bash
python lac_translate_worker.py import \
  --project TRP-00001 \
  --english book_en.txt \
  --mongolian book_mn_draft.txt   # optional; omit to translate from scratch
```

**3. Generate AI suggestions** for all Pending segments:
```bash
python lac_translate_worker.py run --project TRP-00001
```
Prints tokens used and an estimated cost when done.

**4. Review + accept** in ERPNext: open the Translation Segment list, filter by
project + status = *Suggested*. For each row you see Original / Draft / AI
Suggestion. Put the text you want into **Final** and set status **Accepted**
(edit it first if you like — set **Edited**).

**5. Export** the finished book (uses Final, else AI suggestion, else draft):
```bash
python lac_translate_worker.py export --project TRP-00001 --out final_mn.txt
```

## Cost awareness

- Per-segment and per-project token counts + an estimated USD cost are stored
  automatically. **Update the `PRICES` table** at the top of
  `lac_translate_worker.py` to match current OpenAI pricing.
- Start on a cheap model (`gpt-4o-mini`) to validate quality/cost, then switch
  the project's model to a premium one only where it's worth it.
- Re-running `run` only touches **Pending** segments — accepted/suggested rows
  are never re-billed. Re-runs are cheap.

## Notes / roadmap toward a sellable product

This is the validation version. Natural next steps to make it a product:
- A "Translate" button and background job **inside** ERPNext (native app) so no
  external worker is needed (requires a one-time app install + server internet).
- A proper review workspace (side-by-side diff, bulk accept, keyboard flow).
- Translation memory (reuse accepted sentences across books).
- Do not commit `.env` — it holds API secrets.

---

### Optional: Google Docs one-shot script
`translate_improve.py` is a standalone alternative that improves a Mongolian
Google Doc against an English Google Doc in one pass (no ERPNext). See comments
in that file; it needs Google Drive OAuth (`credentials.json`).
