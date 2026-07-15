# Mongolian translation improver (OpenAI)

Automates what you do by hand: instead of pasting one sentence at a time into
OpenAI to get a better Mongolian translation, this reads your **English** and
**Mongolian** Google Docs and produces an improved Mongolian version of the
whole document in one run — using the OpenAI API you like for Mongolian.

## How it works

1. Exports both Google Docs from Drive as plain text.
2. Sends the English source + current Mongolian translation to OpenAI with a
   prompt that tells it to align them and rewrite the Mongolian to be accurate
   and natural (small docs go in one request; large ones are chunked).
3. Saves the improved Mongolian text locally, and can upload it back to Drive
   as a new Google Doc (`--upload`).

## One-time setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. OpenAI API key
Get a key from https://platform.openai.com/api-keys, then:
```bash
export OPENAI_API_KEY=sk-...
```
(See `.env.example`.)

### 3. Google Drive access
The script reads your Google Docs via the Drive API using OAuth:

1. Go to https://console.cloud.google.com/ → create/select a project.
2. Enable the **Google Drive API**.
3. **APIs & Services → Credentials → Create Credentials → OAuth client ID →
   Application type: Desktop app**.
4. Download the JSON and save it as `credentials.json` in this folder.

The first run opens a browser to grant access and caches `token.json` so you
won't be asked again.

## Run it

Use the Doc IDs or full URLs (the long id in `docs.google.com/document/d/<ID>/edit`):

```bash
python translate_improve.py \
  --english   "https://docs.google.com/document/d/AAA.../edit" \
  --mongolian "https://docs.google.com/document/d/BBB.../edit" \
  --out improved_mn.txt \
  --upload
```

Flags:
- `--out` — local output file (default `improved_mn.txt`)
- `--upload` — also create a new Google Doc in your Drive with the result
- `--model` — OpenAI model (default `gpt-4o`; or set `OPENAI_MODEL`)

## Tuning the quality

The prompt that controls the improvement is `SYSTEM_PROMPT` in
`translate_improve.py`. If you have a preferred style, glossary, or terminology,
add it there — that is the single biggest lever on output quality.

## Notes

- `credentials.json` and `token.json` are secrets — do not commit them.
- The script preserves paragraph structure. Complex formatting (tables,
  styling) is flattened to text on export; the output is clean text you paste
  back or upload as a new Doc.
