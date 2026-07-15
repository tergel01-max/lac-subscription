"""
Compare OpenAI models on a Translation Project's segments — READ-ONLY.

Runs each segment through several models with the SAME (tuned) prompt and prints
Source / Draft / each model's output so you can judge which model is worth
paying for. Writes nothing back to ERPNext.

Run on the ERP server:
    cd ~/frappe-bench
    sudo -u frappe bench --site site1.local console
    exec(open('/tmp/lac-trans/translation-automation/server/bench_compare.py').read(), {})
"""

import json
import frappe
import frappe.utils.password  # noqa: F401
from frappe.integrations.utils import make_post_request
from frappe.utils.password import get_decrypted_password

PROJECT = "TRP-00002"
MODELS = ["gpt-4o-mini", "gpt-4o"]
PRICES = {"gpt-4o-mini": {"in": 0.15, "out": 0.60}, "gpt-4o": {"in": 2.50, "out": 10.00}}

SYSTEM = (
    "You are an expert literary translator and editor specializing in "
    "English-to-Mongolian. You are given an English SOURCE and an existing "
    "Mongolian DRAFT. Improve the draft ONLY where it increases accuracy to the "
    "source or naturalness/fluency in Mongolian. Rules: "
    "(1) If the draft is already accurate and natural, output the draft text "
    "itself verbatim — NEVER output a placeholder such as 'UNCHANGED'; always "
    "return the actual Mongolian sentence. "
    "(2) Preserve the draft's punctuation and quotation-mark style (e.g. « » "
    "or “ ”) — do not convert quotes. "
    "(3) Never merge, split, drop, summarize, add, or reorder sentences. "
    "(4) Preserve names, numbers, dates, and terminology. "
    "(5) For headings, use natural Mongolian conventions (e.g. 'Introduction' → 'Оршил'). "
    "Output only the improved Mongolian."
)

proj = frappe.get_doc("Translation Project", PROJECT)
glossary = proj.glossary or ""
key = get_decrypted_password("Raven Settings", "Raven Settings", "openai_api_key")
rs = frappe.get_doc("Raven Settings")
HEADERS = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
if rs.openai_organisation_id:
    HEADERS["OpenAI-Organization"] = rs.openai_organisation_id
if rs.openai_project_id:
    HEADERS["OpenAI-Project"] = rs.openai_project_id


def one(model, source, draft):
    user = "ENGLISH SOURCE:\n%s\n\nMONGOLIAN DRAFT:\n%s" % (source, draft or "(none)")
    if glossary:
        user += "\n\nGlossary / style guide:\n" + glossary
    payload = {"model": model, "temperature": 0.2,
               "messages": [{"role": "system", "content": SYSTEM},
                            {"role": "user", "content": user}]}
    resp = make_post_request("https://api.openai.com/v1/chat/completions",
                             headers=HEADERS, data=json.dumps(payload))
    u = resp.get("usage", {})
    return resp["choices"][0]["message"]["content"].strip(), u


segs = frappe.get_all("Translation Segment", filters={"project": PROJECT},
                      fields=["seq", "source_text", "draft_text"], order_by="seq asc")
cost = {m: 0.0 for m in MODELS}
for s in segs:
    print("\n" + "=" * 100)
    print("SEQ %s\nEN    : %s\nDRAFT : %s" % (s["seq"], s["source_text"], s["draft_text"]))
    for m in MODELS:
        out, u = one(m, s["source_text"], s["draft_text"])
        p = PRICES.get(m, {"in": 0, "out": 0})
        cost[m] += u.get("prompt_tokens", 0) / 1e6 * p["in"] + u.get("completion_tokens", 0) / 1e6 * p["out"]
        print("%-12s: %s" % (m, out))

print("\n" + "=" * 100)
for m in MODELS:
    print("Total est. cost %-12s: $%.5f" % (m, cost[m]))
