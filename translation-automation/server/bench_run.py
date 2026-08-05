"""
Run the AI translation-improvement step ON THE ERP SERVER via bench console.

Why this exists: the OpenAI call needs internet + the API key. The ERP server
has both (Raven already uses OpenAI). This script runs inside Frappe, so it:
  - reads the OpenAI key straight from Raven Settings (no REST, no exported key),
  - uses frappe.integrations.utils.make_post_request (no `openai`/`requests` deps),
  - keeps the same anti-dropping guarantee (exactly one improved sentence per
    input; missing ones are retried individually),
  - writes ai_suggestion/status/tokens/cost back and commits.

HOW TO RUN (on the server):
    cd ~/frappe-bench
    bench --site site1.local console
    # then run (the {} gives exec one namespace so functions see module vars):
    #   exec(open('/path/to/bench_run.py').read(), {})

Change PROJECT below to target a different Translation Project.
"""

import json
import frappe
import frappe.utils.password  # ensure submodule is loaded in a fresh console
from frappe.integrations.utils import make_post_request
from frappe.utils.password import get_decrypted_password

# ---- config ---------------------------------------------------------------
PROJECT = "TRP-00002"
BATCH = 8
# USD per 1M tokens — VERIFY against current OpenAI pricing.
PRICES = {"gpt-4o-mini": {"in": 0.15, "out": 0.60}, "gpt-4o": {"in": 2.50, "out": 10.00}}

SYSTEM = (
    "You are an expert literary translator and editor preparing an OFFICIAL, "
    "PUBLISHED Mongolian edition of an English medical/nutrition book. You are "
    "given an English SOURCE and an existing Mongolian DRAFT. Act as a "
    "professional book editor.\n\n"
    "MONGOLIAN LANGUAGE QUALITY:\n"
    "- Write polished, formal, literary Mongolian in the register of a published "
    "book. Obey standard grammar, orthography, vowel harmony, case/suffix "
    "agreement, postpositions and word order (SOV).\n"
    "- It must read as native professional prose, NOT a word-for-word "
    "('wooden'/calque) rendering. Recast structure the way Mongolian requires; do "
    "not mirror English syntax or punctuation.\n"
    "- Replace colloquial/spoken wording with formal equivalents (e.g. "
    "'тааралддаг' → 'агуулагддаг'). Avoid over-literal calques (e.g. 'empty "
    "products' → 'шим тэжээл багатай хүнс', not 'хоосон хүнс').\n"
    "- Fix typos and wrong word choices in the draft.\n\n"
    "MEANING, IDIOM & TONE (be a bold editor, not a timid one):\n"
    "- Prefer a confident, natural rewrite over a minimal one; restructure freely "
    "so the sentence reads as native book prose.\n"
    "- Translate idioms/metaphors by MEANING. Figurative statements about a "
    "person's ambition, fame or status are NOT literal: 'lift himself to Nobel "
    "Prize heights' means promote his own reputation/seek fame — render THAT "
    "meaning (e.g. 'өөрийн нэр хүндийг Нобелийн шагналын өндөрлөгт хүргэх'), NOT "
    "'reach Nobel level'.\n"
    "- Render verbs by sense, not word-for-word ('follows the new needs' = 'шинэ "
    "хэрэгцээнд нийцүүлэн', not 'хангахыг зорих').\n"
    "- Preserve the author's tone; do not overstate ('disturbs' = disrupts, not "
    "'damages').\n"
    "- NEVER transliterate a descriptive foreign word a reader won't understand; "
    "translate its meaning (e.g. 'magistral' work = 'өдөр тутмын мэргэжлийн ажил', "
    "NOT 'магистрал'). Keep only established chemical names, proper nouns and "
    "glossary terms in their given form.\n\n"
    "TERMINOLOGY (STRICT):\n"
    "- Render medical/scientific/chemical terms precisely and CONSISTENTLY per the "
    "project glossary (authoritative). Keep proper names, numbers, dates, dosages, "
    "units and abbreviations (e.g. OPC) exact.\n\n"
    "RULES:\n"
    "- If the draft is already accurate and natural, keep it (never a placeholder "
    "like 'UNCHANGED').\n"
    "- Never merge, split, drop, summarize, add or reorder sentences.\n"
    "- Preserve the draft's quotation-mark style."
)

# ---- setup ----------------------------------------------------------------
proj = frappe.get_doc("Translation Project", PROJECT)
model = proj.model or "gpt-4o-mini"
glossary = proj.glossary or ""
price = PRICES.get(model, {"in": 0.15, "out": 0.60})

key = get_decrypted_password("Raven Settings", "Raven Settings", "openai_api_key")
rs = frappe.get_doc("Raven Settings")
HEADERS = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
if rs.openai_organisation_id:
    HEADERS["OpenAI-Organization"] = rs.openai_organisation_id
if rs.openai_project_id:
    HEADERS["OpenAI-Project"] = rs.openai_project_id


def improve(batch):
    """batch: list of {id, source_text, draft_text} -> ({id: mn}, usage)."""
    lines = []
    for it in batch:
        lines.append("[%d] EN: %s" % (it["id"], it["source_text"]))
        lines.append("     MN draft: %s" % (it["draft_text"] or "(none - translate from English)"))
    instr = (
        "For EACH numbered item, act as the book editor. Return a JSON object: "
        '{"items":[{"id":<int>,'
        '"mn":"<the polished, publication-ready book translation — prefer the bold, natural rewrite>",'
        '"alt":"<REQUIRED: a second, more literal/faithful rendering that stays closer to the source; must differ from mn unless the draft is already perfect>",'
        '"notes":"<one short line in Mongolian naming the key fixes; empty if unchanged>"}]}. '
        "Return EXACTLY one element per input id, same ids. "
        "Do not merge, split, drop, reorder, or add items."
    )
    if glossary:
        instr += "\n\nGlossary / style guide:\n" + glossary
    payload = {
        "model": model,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": instr + "\n\nItems:\n" + "\n".join(lines)},
        ],
    }
    resp = make_post_request("https://api.openai.com/v1/chat/completions",
                             headers=HEADERS, data=json.dumps(payload))
    data = json.loads(resp["choices"][0]["message"]["content"])
    result = {int(x["id"]): {"mn": x.get("mn", ""), "alt": x.get("alt", ""),
                             "notes": x.get("notes", "")} for x in data.get("items", [])}
    return result, resp.get("usage", {})


# ---- run ------------------------------------------------------------------
segments = frappe.get_all(
    "Translation Segment",
    filters={"project": PROJECT, "status": "Pending"},
    fields=["name", "seq", "source_text", "draft_text"],
    order_by="seq asc",
)
print("Project %s | model %s | %d pending segments" % (PROJECT, model, len(segments)))

total_p = total_c = 0
for start in range(0, len(segments), BATCH):
    chunk = segments[start:start + BATCH]
    batch = [{"id": i + 1, "source_text": s["source_text"], "draft_text": s.get("draft_text") or ""}
             for i, s in enumerate(chunk)]
    id_to_name = {i + 1: s["name"] for i, s in enumerate(chunk)}

    results, usage = improve(batch)
    pt = usage.get("prompt_tokens", 0)
    ct = usage.get("completion_tokens", 0)

    missing = [i for i in id_to_name if i not in results]
    if missing:
        print("  ! %d/%d returned; retrying missing individually" % (len(results), len(batch)))
        for i in missing:
            s = next(s for s in chunk if id_to_name[i] == s["name"])
            single, u = improve([{"id": i, "source_text": s["source_text"], "draft_text": s.get("draft_text") or ""}])
            results.update(single)
            pt += u.get("prompt_tokens", 0)
            ct += u.get("completion_tokens", 0)

    per_p = pt / max(len(batch), 1)
    per_c = ct / max(len(batch), 1)
    for i, name in id_to_name.items():
        if i not in results:
            frappe.db.set_value("Translation Segment", name, "status", "Rejected")
            continue
        cost = per_p / 1e6 * price["in"] + per_c / 1e6 * price["out"]
        r = results[i]
        frappe.db.set_value("Translation Segment", name, {
            "ai_suggestion": r["mn"], "ai_alternative": r["alt"], "ai_rationale": r["notes"],
            "status": "Suggested", "model": model,
            "prompt_tokens": int(per_p), "completion_tokens": int(per_c), "cost": round(cost, 6),
        })
        print("  seq %s -> %s" % (next(s["seq"] for s in chunk if s["name"] == name), r["mn"][:80]))
    total_p += pt
    total_c += ct

total_cost = total_p / 1e6 * price["in"] + total_c / 1e6 * price["out"]
frappe.db.set_value("Translation Project", PROJECT, {
    "status": "Review",
    "prompt_tokens": (proj.prompt_tokens or 0) + total_p,
    "completion_tokens": (proj.completion_tokens or 0) + total_c,
    "cost": round((proj.cost or 0) + total_cost, 4),
})
frappe.db.commit()
print("\nDONE. tokens in/out: %d/%d | est cost $%.4f (%s)" % (total_p, total_c, total_cost, model))
