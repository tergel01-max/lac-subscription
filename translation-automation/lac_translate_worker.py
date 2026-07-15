#!/usr/bin/env python3
"""
LAC Book Translation Helper — external worker.

Talks to your ERPNext (Translation Project / Translation Segment DocTypes)
over the REST API and to the OpenAI API. Three commands:

    import   Split an English source + Mongolian draft into sentence-level
             segments and create them in ERPNext (nothing can be dropped:
             one row per English sentence).

    run      Fetch "Pending" segments for a project, send them to OpenAI in
             small COUNT-ENFORCED batches (the model must return exactly one
             improved sentence per input — no merging/splitting/dropping),
             and write the suggestion back as status "Suggested".

    export   Compile a project's segments (final_text > ai_suggestion >
             draft_text) in order into a single text file.

Review/accept happens in the ERPNext UI (Translation Segment list): read the
Original | Draft | AI Suggestion, set Final + status Accepted.

Config via environment variables (see .env.example):
    ERPNEXT_URL, ERPNEXT_API_KEY, ERPNEXT_API_SECRET
    OPENAI_API_KEY
"""

import argparse
import json
import os
import re
import sys

import requests
from openai import OpenAI

# --------------------------------------------------------------------------- #
# Pricing — USD per 1,000,000 tokens. VERIFY against current OpenAI pricing.
# Used only to estimate/track cost; does not affect translation output.
# --------------------------------------------------------------------------- #
PRICES = {
    "gpt-4o-mini": {"in": 0.15, "out": 0.60},
    "gpt-4o": {"in": 2.50, "out": 10.00},
}
DEFAULT_PRICE = {"in": 0.15, "out": 0.60}

BATCH_SIZE = 8  # sentences per OpenAI request (context vs. cost trade-off)

SYSTEM_PROMPT = (
    "You are an expert literary translator and editor specializing in "
    "English-to-Mongolian, with domain expertise in medicine, nutrition and "
    "chemistry. You are given an English SOURCE and an existing Mongolian DRAFT. "
    "Produce the best Mongolian rendering.\n\n"
    "MONGOLIAN LANGUAGE QUALITY:\n"
    "- Write natural, idiomatic literary Mongolian that obeys standard grammar, "
    "orthography, vowel harmony, case/suffix agreement, postpositions and word "
    "order (SOV).\n"
    "- It must read as if written by an educated native Mongolian author, NOT a "
    "word-for-word ('wooden'/calque) rendering. Recast the sentence the way "
    "Mongolian requires; do not mirror English syntax, articles or punctuation.\n"
    "- Fix any grammatical or spelling errors in the draft.\n\n"
    "TERMINOLOGY (STRICT):\n"
    "- Render medical, scientific and chemical terms precisely and CONSISTENTLY. "
    "Use the established Mongolian term when one exists; otherwise use the "
    "internationally accepted term (Latin/chemical name) in its standard Mongolian "
    "transliteration, and keep it identical everywhere.\n"
    "- Never loosely paraphrase a technical term. Preserve proper names, numbers, "
    "dosages, units, dates and abbreviations (e.g. OPC) exactly.\n"
    "- The project glossary below is authoritative and overrides your defaults.\n\n"
    "RULES:\n"
    "- If the draft is already accurate and natural, output it VERBATIM (never a "
    "placeholder like 'UNCHANGED').\n"
    "- Never merge, split, drop, summarize, add or reorder sentences.\n"
    "- Preserve the draft's quotation-mark style.\n"
    "Output only the improved Mongolian."
)


# --------------------------------------------------------------------------- #
# ERPNext REST client
# --------------------------------------------------------------------------- #
class ERP:
    def __init__(self):
        self.base = os.environ["ERPNEXT_URL"].rstrip("/")
        key = os.environ["ERPNEXT_API_KEY"]
        secret = os.environ["ERPNEXT_API_SECRET"]
        self.s = requests.Session()
        self.s.headers.update(
            {
                "Authorization": f"token {key}:{secret}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    def _url(self, doctype, name=None):
        u = f"{self.base}/api/resource/{requests.utils.quote(doctype)}"
        return f"{u}/{requests.utils.quote(str(name))}" if name else u

    def get_list(self, doctype, filters, fields, order_by="seq asc"):
        r = self.s.get(
            self._url(doctype),
            params={
                "filters": json.dumps(filters),
                "fields": json.dumps(fields),
                "order_by": order_by,
                "limit_page_length": 0,  # all rows
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["data"]

    def create(self, doctype, doc):
        r = self.s.post(self._url(doctype), data=json.dumps(doc), timeout=60)
        r.raise_for_status()
        return r.json()["data"]

    def update(self, doctype, name, doc):
        r = self.s.put(self._url(doctype, name), data=json.dumps(doc), timeout=60)
        r.raise_for_status()
        return r.json()["data"]

    def get(self, doctype, name):
        r = self.s.get(self._url(doctype, name), timeout=60)
        r.raise_for_status()
        return r.json()["data"]

    def get_password(self, doctype, name, fieldname):
        """Read a Password field (e.g. the stored OpenAI key) via the
        whitelisted frappe.client.get_password method."""
        r = self.s.get(
            f"{self.base}/api/method/frappe.client.get_password",
            params={"doctype": doctype, "name": name, "fieldname": fieldname},
            timeout=60,
        )
        r.raise_for_status()
        return r.json().get("message")


def build_openai_client(erp):
    """Use OPENAI_API_KEY if set; otherwise reuse the OpenAI key already
    configured in ERPNext (Raven Settings), so there's no second key to manage.
    Returns (client, source)."""
    if os.getenv("OPENAI_API_KEY"):
        return OpenAI(), "env:OPENAI_API_KEY"
    key = erp.get_password("Raven Settings", "Raven Settings", "openai_api_key")
    if not key:
        sys.exit(
            "No OpenAI key found. Set OPENAI_API_KEY, or configure "
            "Raven Settings > OpenAI API Key in ERPNext."
        )
    settings = erp.get("Raven Settings", "Raven Settings")
    kwargs = {"api_key": key}
    if settings.get("openai_organisation_id"):
        kwargs["organization"] = settings["openai_organisation_id"]
    if settings.get("openai_project_id"):
        kwargs["project"] = settings["openai_project_id"]
    return OpenAI(**kwargs), "ERPNext:Raven Settings"


# --------------------------------------------------------------------------- #
# Sentence segmentation
# --------------------------------------------------------------------------- #
# Abbreviations whose trailing period must NOT end a sentence.
_ABBR = {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "inc",
         "ltd", "no", "fig", "al", "e.g", "i.e", "vol", "pp"}


def split_sentences(text: str):
    """Lightweight sentence splitter that keeps terminal punctuation.
    Works for Latin and Cyrillic; splits on . ! ? … followed by whitespace.
    Handles real-world noise: protects abbreviations ('Dr.') and initials
    ('W.G.C.', 'Э.С.'), and repairs a missing space after a period glued to a
    number (e.g. 'юм.1980' -> 'юм. 1980')."""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    # repair 'letter.<digit>' (missing space after sentence end); leaves 3.14 alone
    text = re.sub(r"([^\W\d_])([.!?…])(\d)", r"\1\2 \3", text)
    # protect known abbreviations
    text = re.sub(
        r"\b([A-Za-z]{1,4})\.",
        lambda m: m.group(1) + "<DOT>" if m.group(1).lower() in _ABBR else m.group(0),
        text,
    )
    # protect single-letter initials in sequences like W.G.C. / Э.С.
    text = re.sub(r"\b([A-ZА-ЯӨҮ])\.(?=[\sA-ZА-ЯӨҮ])", r"\1<DOT>", text)
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return [p.strip().replace("<DOT>", ".") for p in parts if p.strip()]


def align_draft(src_sentences, draft_sentences):
    """Best-effort positional alignment of the Mongolian draft to the English
    sentences. If counts match, 1:1. Otherwise map proportionally so each
    English sentence gets a nearby draft as a hint (the AI refines it)."""
    n, m = len(src_sentences), len(draft_sentences)
    if m == 0:
        return ["" for _ in src_sentences]
    if n == m:
        return draft_sentences
    out = []
    for i in range(n):
        j = min(int(i * m / n), m - 1)
        out.append(draft_sentences[j])
    return out


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_import(erp, args):
    with open(args.english, encoding="utf-8") as f:
        src = split_sentences(f.read())
    draft_raw = ""
    if args.mongolian:
        with open(args.mongolian, encoding="utf-8") as f:
            draft_raw = f.read()
    draft = align_draft(src, split_sentences(draft_raw))

    print(f"Source sentences: {len(src)}. Creating segments under {args.project}...")
    for i, (en, mn) in enumerate(zip(src, draft), 1):
        erp.create(
            "Translation Segment",
            {
                "project": args.project,
                "seq": i,
                "status": "Pending",
                "source_text": en,
                "draft_text": mn,
            },
        )
        if i % 25 == 0:
            print(f"  {i}/{len(src)}")
    erp.update("Translation Project", args.project, {"total_segments": len(src)})
    print(f"Done. Created {len(src)} segments.")


def _improve_batch(client, model, glossary, batch):
    """Send one batch; return {local_id: improved_mn} + usage. Enforces that
    the model returns exactly one item per input id."""
    lines = []
    for item in batch:
        lines.append(f"[{item['id']}] EN: {item['source_text']}")
        lines.append(f"     MN draft: {item['draft_text'] or '(none — translate from English)'}")
    instructions = (
        "Improve the Mongolian for EACH numbered item below. "
        'Return a JSON object: {"items":[{"id":<int>,"mn":"<improved Mongolian>"}, ...]}. '
        "Return EXACTLY one element per input id, using the same ids. "
        "Do not merge, split, drop, reorder, or add items."
    )
    if glossary:
        instructions += f"\n\nGlossary / style guide:\n{glossary}"
    user = instructions + "\n\nItems:\n" + "\n".join(lines)

    resp = client.chat.completions.create(
        model=model,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    result = {int(it["id"]): it["mn"] for it in data.get("items", [])}
    return result, resp.usage


def cmd_run(erp, args):
    client, key_source = build_openai_client(erp)
    print(f"OpenAI key source: {key_source}")
    project = erp.get("Translation Project", args.project)
    model = args.model or project.get("model") or "gpt-4o-mini"
    glossary = project.get("glossary") or ""
    price = PRICES.get(model, DEFAULT_PRICE)

    segments = erp.get_list(
        "Translation Segment",
        {"project": args.project, "status": "Pending"},
        ["name", "seq", "source_text", "draft_text"],
    )
    if not segments:
        print("No Pending segments. Nothing to do.")
        return
    print(f"{len(segments)} Pending segments. Model: {model}. Batch size: {BATCH_SIZE}.")

    total_p = total_c = 0
    processed = 0
    for start in range(0, len(segments), BATCH_SIZE):
        chunk = segments[start : start + BATCH_SIZE]
        # local ids map back to segment names
        batch = [
            {"id": i + 1, "source_text": s["source_text"], "draft_text": s.get("draft_text") or ""}
            for i, s in enumerate(chunk)
        ]
        id_to_name = {i + 1: s["name"] for i, s in enumerate(chunk)}

        results, usage = _improve_batch(client, model, glossary, batch)

        # Anti-dropping guarantee: every input id must come back.
        missing = [i for i in id_to_name if i not in results]
        if missing:
            print(f"  ! batch returned {len(results)}/{len(batch)} items; retrying missing one-by-one")
            for i in missing:
                s = next(s for s in chunk if id_to_name[i] == s["name"])
                single, u = _improve_batch(
                    client, model, glossary,
                    [{"id": i, "source_text": s["source_text"], "draft_text": s.get("draft_text") or ""}],
                )
                results.update(single)
                usage.prompt_tokens += u.prompt_tokens
                usage.completion_tokens += u.completion_tokens

        # split usage evenly across the batch for per-segment tracking
        per_p = usage.prompt_tokens / len(batch)
        per_c = usage.completion_tokens / len(batch)
        for i, name in id_to_name.items():
            if i not in results:
                erp.update("Translation Segment", name, {"status": "Rejected"})
                continue
            cost = per_p / 1e6 * price["in"] + per_c / 1e6 * price["out"]
            erp.update(
                "Translation Segment",
                name,
                {
                    "ai_suggestion": results[i],
                    "status": "Suggested",
                    "model": model,
                    "prompt_tokens": int(per_p),
                    "completion_tokens": int(per_c),
                    "cost": round(cost, 6),
                },
            )
        total_p += usage.prompt_tokens
        total_c += usage.completion_tokens
        processed += len(chunk)
        print(f"  {processed}/{len(segments)} done")

    total_cost = total_p / 1e6 * price["in"] + total_c / 1e6 * price["out"]
    erp.update(
        "Translation Project",
        args.project,
        {
            "status": "Review",
            "prompt_tokens": (project.get("prompt_tokens") or 0) + total_p,
            "completion_tokens": (project.get("completion_tokens") or 0) + total_c,
            "cost": round((project.get("cost") or 0) + total_cost, 4),
        },
    )
    print(
        f"\nDone. Tokens in/out: {total_p}/{total_c}. "
        f"Estimated cost: ${total_cost:.4f} (model {model})."
    )


def cmd_export(erp, args):
    segments = erp.get_list(
        "Translation Segment",
        {"project": args.project},
        ["seq", "final_text", "ai_suggestion", "draft_text"],
    )
    lines = []
    for s in segments:
        lines.append((s.get("final_text") or s.get("ai_suggestion") or s.get("draft_text") or "").strip())
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Exported {len(segments)} segments -> {args.out}")


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="LAC book translation helper worker.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_imp = sub.add_parser("import", help="Segment source+draft into ERPNext")
    p_imp.add_argument("--project", required=True, help="Translation Project name, e.g. TRP-00001")
    p_imp.add_argument("--english", required=True, help="English source .txt")
    p_imp.add_argument("--mongolian", help="Existing Mongolian draft .txt (optional)")

    p_run = sub.add_parser("run", help="Generate AI suggestions for Pending segments")
    p_run.add_argument("--project", required=True)
    p_run.add_argument("--model", help="Override the project's model")

    p_exp = sub.add_parser("export", help="Compile the project into a text file")
    p_exp.add_argument("--project", required=True)
    p_exp.add_argument("--out", default="final_mn.txt")

    args = ap.parse_args()
    erp = ERP()
    {"import": cmd_import, "run": cmd_run, "export": cmd_export}[args.cmd](erp, args)


if __name__ == "__main__":
    main()
