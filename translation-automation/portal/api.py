"""
Server-side API for the Translation Portal (app: lac_translation).

Install path on the server:
    apps/lac_translation/lac_translation/api.py

Whitelisted methods (call via frappe.call({method: 'lac_translation.api.<fn>'})):
  - import_book(title, english_file_url, mongolian_file_url, model, glossary)
  - generate(project, model)              -> enqueues the AI pass (background)
  - regenerate(segment, hint)             -> redo one segment's AI suggestion
  - export_docx(project)                  -> build a .docx, return its file_url

The OpenAI key/org/project are read from Raven Settings (same as the bench
scripts); the server reaches OpenAI directly.
"""

import io
import re
import json

import frappe
from frappe.integrations.utils import make_post_request
from frappe.utils.password import get_decrypted_password

PRICES = {"gpt-4o-mini": {"in": 0.15, "out": 0.60}, "gpt-4o": {"in": 2.50, "out": 10.00}}
BATCH = 8
_ABBR = {"dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "inc", "ltd", "no", "fig", "al", "vol", "pp"}

SYSTEM = (
    "You are an expert literary translator and editor preparing an OFFICIAL, "
    "PUBLISHED Mongolian edition of an English medical/nutrition book. You are "
    "given an English SOURCE and an existing Mongolian DRAFT. Act as a "
    "professional book editor.\n\n"
    "MONGOLIAN LANGUAGE QUALITY:\n"
    "- Write polished, formal, literary Mongolian in the register of a published "
    "book; obey standard grammar, orthography, vowel harmony, case/suffix "
    "agreement, postpositions and word order (SOV).\n"
    "- Read as native professional prose, NOT a word-for-word ('wooden'/calque) "
    "rendering. Recast structure the way Mongolian requires.\n"
    "- Replace colloquial wording with formal equivalents; avoid over-literal calques.\n\n"
    "MEANING, IDIOM & TONE (be a bold editor):\n"
    "- Translate idioms/metaphors by MEANING; figurative statements about a "
    "person's ambition/fame are not literal.\n"
    "- Preserve the author's tone; do not overstate.\n"
    "- NEVER transliterate a descriptive foreign word a reader won't understand; "
    "translate its meaning. Keep only established chemical names, proper nouns and "
    "glossary terms as given.\n\n"
    "TERMINOLOGY (STRICT): render medical/scientific terms precisely and "
    "CONSISTENTLY per the project glossary. Keep names, numbers, dates, units and "
    "abbreviations (e.g. OPC) exact.\n\n"
    "RULES: if the draft is already accurate and natural, keep it (never a "
    "placeholder). Never merge/split/drop/summarize/add/reorder sentences. "
    "Preserve the draft's quotation-mark style."
)


# --------------------------------------------------------------------------- #
# text helpers
# --------------------------------------------------------------------------- #
def _split_sentences(text):
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        return []
    text = re.sub(r"([^\W\d_])([.!?…])(\d)", r"\1\2 \3", text)
    text = re.sub(r"\b([A-Za-z]{1,4})\.",
                  lambda m: m.group(1) + "<DOT>" if m.group(1).lower() in _ABBR else m.group(0), text)
    text = re.sub(r"\b([A-ZА-ЯӨҮ])\.(?=[\sA-ZА-ЯӨҮ])", r"\1<DOT>", text)
    parts = re.split(r"(?<=[.!?…])\s+", text)
    return [p.strip().replace("<DOT>", ".") for p in parts if p.strip()]


def _is_heading(style, text):
    if style and style.lower().startswith("heading"):
        return True
    words = text.split()
    if len(words) <= 12 and text == text.upper() and re.search(r"[A-ZА-ЯӨҮ]", text) and not re.search(r"[.!?…]$", text):
        return True
    return False


def _docx_paragraphs(file_url):
    from frappe.utils.file_manager import get_file
    _name, content = get_file(file_url)
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    from docx import Document
    d = Document(io.BytesIO(content))
    out = []
    for p in d.paragraphs:
        t = (p.text or "").strip()
        if t:
            style = p.style.name if (p.style and p.style.name) else ""
            out.append((style, t))
    return out


# --------------------------------------------------------------------------- #
# OpenAI helpers
# --------------------------------------------------------------------------- #
def _headers():
    key = get_decrypted_password("Raven Settings", "Raven Settings", "openai_api_key")
    if not key:
        frappe.throw("No OpenAI key in Raven Settings.")
    h = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    rs = frappe.get_doc("Raven Settings")
    if rs.openai_organisation_id:
        h["OpenAI-Organization"] = rs.openai_organisation_id
    if rs.openai_project_id:
        h["OpenAI-Project"] = rs.openai_project_id
    return h


def _openai(model, glossary, items, headers=None, hint=""):
    """items: [{id, source_text, draft_text}] -> ({id:{mn,alt,notes}}, usage)."""
    headers = headers or _headers()
    lines = []
    for it in items:
        lines.append("[%d] EN: %s" % (it["id"], it["source_text"]))
        lines.append("     MN draft: %s" % (it["draft_text"] or "(none - translate from English)"))
    instr = (
        "For EACH numbered item, act as the book editor. Return a JSON object: "
        '{"items":[{"id":<int>,'
        '"mn":"<polished publication-ready book translation - prefer a bold natural rewrite>",'
        '"alt":"<a more faithful/literal alternative>",'
        '"notes":"<one short line in Mongolian naming the key fixes; empty if unchanged>"}]}. '
        "Return EXACTLY one element per input id, same ids. No merge/split/drop/reorder/add."
    )
    if hint:
        instr += "\n\nEXTRA INSTRUCTION FROM THE EDITOR: " + hint
    if glossary:
        instr += "\n\nGlossary / style guide:\n" + glossary
    payload = {
        "model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
        "messages": [{"role": "system", "content": SYSTEM},
                     {"role": "user", "content": instr + "\n\nItems:\n" + "\n".join(lines)}],
    }
    resp = make_post_request("https://api.openai.com/v1/chat/completions",
                             headers=headers, data=json.dumps(payload))
    data = json.loads(resp["choices"][0]["message"]["content"])
    result = {int(x["id"]): {"mn": x.get("mn", ""), "alt": x.get("alt", ""), "notes": x.get("notes", "")}
              for x in data.get("items", [])}
    return result, resp.get("usage", {})


# --------------------------------------------------------------------------- #
# whitelisted API
# --------------------------------------------------------------------------- #
@frappe.whitelist()
def import_book(title, english_file_url, mongolian_file_url=None, model="gpt-4o", glossary=""):
    frappe.only_for("System Manager")
    en = _docx_paragraphs(english_file_url)
    mn_texts = [t for (_s, t) in _docx_paragraphs(mongolian_file_url)] if mongolian_file_url else []

    proj = frappe.get_doc({
        "doctype": "Translation Project", "title": title, "status": "In Progress",
        "source_language": "English", "target_language": "Mongolian",
        "model": model, "glossary": glossary,
    }).insert()

    seq = 0
    chapter = "Book"
    for idx, (style, en_text) in enumerate(en):
        if _is_heading(style, en_text):
            chapter = en_text[:130]
        mn_text = mn_texts[idx] if idx < len(mn_texts) else ""
        es, ms = _split_sentences(en_text), _split_sentences(mn_text)
        pairs = list(zip(es, ms)) if (len(es) == len(ms) and len(es) >= 1) else [(en_text, mn_text)]
        for e, m in pairs:
            seq += 1
            frappe.get_doc({
                "doctype": "Translation Segment", "project": proj.name, "seq": seq,
                "chapter": chapter, "status": "Pending", "source_text": e, "draft_text": m,
            }).insert(ignore_permissions=True)
        if seq and seq % 200 == 0:
            frappe.db.commit()
    proj.db_set("total_segments", seq)
    frappe.db.commit()
    return {"project": proj.name, "segments": seq}


@frappe.whitelist()
def generate(project, model=None):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._generate_job", queue="long", timeout=6000,
                   project=project, model=model)
    return {"queued": True}


def _generate_job(project, model=None):
    proj = frappe.get_doc("Translation Project", project)
    model = model or proj.model or "gpt-4o-mini"
    glossary = proj.glossary or ""
    price = PRICES.get(model, {"in": 0.15, "out": 0.60})
    headers = _headers()
    segs = frappe.get_all("Translation Segment", filters={"project": project, "status": "Pending"},
                          fields=["name", "seq", "source_text", "draft_text"], order_by="seq asc")
    tp = tc = 0
    for start in range(0, len(segs), BATCH):
        chunk = segs[start:start + BATCH]
        items = [{"id": i + 1, "source_text": s["source_text"], "draft_text": s.get("draft_text") or ""}
                 for i, s in enumerate(chunk)]
        id_to_name = {i + 1: s["name"] for i, s in enumerate(chunk)}
        results, usage = _openai(model, glossary, items, headers)
        pt, ct = usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0)
        missing = [i for i in id_to_name if i not in results]
        for i in missing:
            s = next(s for s in chunk if id_to_name[i] == s["name"])
            single, u = _openai(model, glossary,
                                [{"id": i, "source_text": s["source_text"], "draft_text": s.get("draft_text") or ""}], headers)
            results.update(single)
            pt += u.get("prompt_tokens", 0); ct += u.get("completion_tokens", 0)
        per_p, per_c = pt / max(len(items), 1), ct / max(len(items), 1)
        for i, name in id_to_name.items():
            r = results.get(i)
            if not r:
                frappe.db.set_value("Translation Segment", name, "status", "Rejected"); continue
            cost = per_p / 1e6 * price["in"] + per_c / 1e6 * price["out"]
            frappe.db.set_value("Translation Segment", name, {
                "ai_suggestion": r["mn"], "ai_alternative": r["alt"], "ai_rationale": r["notes"],
                "status": "Suggested", "model": model,
                "prompt_tokens": int(per_p), "completion_tokens": int(per_c), "cost": round(cost, 6)})
        tp += pt; tc += ct
        frappe.db.commit()
        frappe.publish_realtime("lac_translation_progress",
                                {"project": project, "done": start + len(chunk), "total": len(segs)})
    total_cost = tp / 1e6 * price["in"] + tc / 1e6 * price["out"]
    proj.db_set("status", "Review")
    proj.db_set("prompt_tokens", (proj.prompt_tokens or 0) + tp)
    proj.db_set("completion_tokens", (proj.completion_tokens or 0) + tc)
    proj.db_set("cost", round((proj.cost or 0) + total_cost, 4))
    frappe.db.commit()


@frappe.whitelist()
def regenerate(segment, hint=""):
    frappe.only_for("System Manager")
    s = frappe.get_doc("Translation Segment", segment)
    proj = frappe.get_doc("Translation Project", s.project)
    model = proj.model or "gpt-4o-mini"
    results, _u = _openai(model, proj.glossary or "",
                          [{"id": 1, "source_text": s.source_text, "draft_text": s.draft_text or ""}], hint=hint)
    r = results.get(1) or {"mn": "", "alt": "", "notes": ""}
    s.db_set("ai_suggestion", r["mn"]); s.db_set("ai_alternative", r["alt"])
    s.db_set("ai_rationale", r["notes"]); s.db_set("status", "Suggested"); s.db_set("model", model)
    frappe.db.commit()
    return r


@frappe.whitelist()
def export_docx(project):
    frappe.only_for("System Manager")
    proj = frappe.get_doc("Translation Project", project)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["seq", "chapter", "final_text", "ai_suggestion", "draft_text"],
                          order_by="seq asc")
    from docx import Document
    doc = Document()
    doc.add_heading(proj.title or project, 0)
    cur = None
    for s in segs:
        ch = s.get("chapter") or "Book"
        if ch != cur:
            doc.add_heading(ch, level=1); cur = ch
        doc.add_paragraph(s.get("final_text") or s.get("ai_suggestion") or s.get("draft_text") or "")
    buf = io.BytesIO(); doc.save(buf)
    from frappe.utils.file_manager import save_file
    fname = re.sub(r"[^\w\-]+", "_", (proj.title or project)) + "_MN.docx"
    f = save_file(fname, buf.getvalue(), "Translation Project", project, is_private=1)
    return {"file_url": f.file_url}
