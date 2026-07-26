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
import zipfile
import xml.etree.ElementTree as ET

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


def _read_docx_content(ref):
    """Resolve a docx from a File id, a file_url, or (fallback) get_file.
    Reading via the File doc avoids Unicode-filename path issues."""
    if frappe.db.exists("File", ref):
        return frappe.get_doc("File", ref).get_content()
    fname = frappe.db.get_value("File", {"file_url": ref}, "name")
    if fname:
        return frappe.get_doc("File", fname).get_content()
    from frappe.utils.file_manager import get_file
    return get_file(ref)[1]


def _docx_paragraphs(file_url):
    content = _read_docx_content(file_url)
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


def _pdf_paragraphs(file_url):
    """Extract (style, text) paragraphs from a PDF using font size to spot
    headings. Drops page numbers, repeated running headers/footers and
    number-table rows; de-hyphenates line-break hyphens; merges body lines into
    paragraphs. Headings get style 'heading' so the chapter logic picks them up."""
    content = _read_docx_content(file_url)
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    import fitz
    doc = fitz.open(stream=io.BytesIO(content), filetype="pdf")
    pages, sizes, linecount = [], {}, {}
    for pno in range(doc.page_count):
        d = doc.load_page(pno).get_text("dict")
        lines = []
        for b in d.get("blocks", []):
            for l in b.get("lines", []):
                spans = l.get("spans", [])
                t = "".join(x.get("text", "") for x in spans).strip()
                if not t:
                    continue
                sz = max((x.get("size", 0) for x in spans), default=0)
                lines.append((t, sz))
                sizes[round(sz)] = sizes.get(round(sz), 0) + len(t)
                k = t.lower()[:40]
                linecount[k] = linecount.get(k, 0) + 1
        pages.append(lines)
    if not sizes:
        return []
    body = max(sizes, key=sizes.get)
    rep = {k for k, c in linecount.items() if c >= max(3, doc.page_count * 0.15) and len(k) < 40}

    out, buf = [], []

    def flush():
        if buf:
            out.append(("normal", " ".join(buf)))
            buf[:] = []

    for lines in pages:
        for t, sz in lines:
            if t.lower()[:40] in rep:
                continue
            low = t.lower()
            if "binnenwerk" in low or ".indd" in low:   # per-page print footer
                continue
            if re.match(r"^\s*\d{1,3}\s*$", t) or _EN_NUM_ROW.match(t):
                continue
            letters = [c for c in t if c.isalpha()]
            is_head = (sz >= body + 2 and 2 <= len(t) < 80 and len(letters) >= 2
                       and not t.endswith((".", "!", "?", ",")))
            if is_head:
                flush()
                out.append(("heading", t))
            elif buf and buf[-1].endswith("-"):
                buf[-1] = buf[-1][:-1] + t
            else:
                buf.append(t)
        flush()
    return out


def _source_paragraphs(file_ref):
    """Dispatch to the PDF or DOCX extractor by the file's extension."""
    url = file_ref or ""
    if not url.lower().endswith(".pdf") and frappe.db.exists("File", file_ref):
        url = frappe.db.get_value("File", file_ref, "file_url") or url
    return _pdf_paragraphs(file_ref) if url.lower().endswith(".pdf") else _docx_paragraphs(file_ref)


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
    any_no_source = False
    for it in items:
        if (it.get("source_text") or "").strip():
            lines.append("[%d] EN: %s" % (it["id"], it["source_text"]))
            lines.append("     MN draft: %s" % (it["draft_text"] or "(none - translate from English)"))
        else:
            any_no_source = True
            lines.append("[%d] MN to polish (no English source): %s" % (it["id"], it["draft_text"] or ""))
    instr = (
        ("For items with no English source, polish the Mongolian for grammar, "
         "naturalness and terminology only, PRESERVING its meaning (do not invent content). " if any_no_source else "")
        + "For EACH numbered item, act as the book editor. Return a JSON object: "
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
# termbase
# --------------------------------------------------------------------------- #
def _termbase(project):
    return frappe.get_all("Translation Term", filters={"project": project},
                          fields=["source_term", "target_term"])


def _eff_glossary(proj):
    """Project glossary + the approved termbase, fed to every AI call so
    terminology stays consistent across the whole book."""
    base = proj.glossary or ""
    terms = _termbase(proj.name)
    if terms:
        base += ("\n\nAPPROVED TERMINOLOGY (use these EXACTLY and consistently; "
                 "adapt Mongolian case endings but keep the term):\n"
                 + "\n".join("- %s = %s" % (t.source_term, t.target_term) for t in terms))
    return base


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
def generate(project, model=None, chapter=None, from_seq=None, to_seq=None, limit=None):
    """Queue the AI pass over a chosen SCOPE of Pending segments:
    whole book (default), one chapter, a seq range, or the first N."""
    frappe.only_for("System Manager")
    filters = {"project": project, "status": "Pending"}
    if chapter:
        filters["chapter"] = chapter
    if from_seq and to_seq:
        filters["seq"] = ["between", [int(from_seq), int(to_seq)]]
    elif from_seq:
        filters["seq"] = [">=", int(from_seq)]
    elif to_seq:
        filters["seq"] = ["<=", int(to_seq)]
    names = frappe.get_all("Translation Segment", pluck="name", filters=filters,
                           order_by="seq asc", limit_page_length=int(limit) if limit else 0)
    if not names:
        return {"queued": 0}
    frappe.enqueue("lac_translation.api._generate_job", queue="long", timeout=6000,
                   project=project, names=names, model=model)
    return {"queued": len(names)}


def _generate_job(project, model=None, names=None):
    proj = frappe.get_doc("Translation Project", project)
    model = model or proj.model or "gpt-4o-mini"
    glossary = _eff_glossary(proj)
    price = PRICES.get(model, {"in": 0.15, "out": 0.60})
    headers = _headers()
    if names:
        segs = frappe.get_all("Translation Segment", filters={"name": ["in", names]},
                              fields=["name", "seq", "source_text", "draft_text"], order_by="seq asc")
    else:
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
    results, _u = _openai(model, _eff_glossary(proj),
                          [{"id": 1, "source_text": s.source_text, "draft_text": s.draft_text or ""}], hint=hint)
    r = results.get(1) or {"mn": "", "alt": "", "notes": ""}
    s.db_set("ai_suggestion", r["mn"]); s.db_set("ai_alternative", r["alt"])
    s.db_set("ai_rationale", r["notes"]); s.db_set("status", "Suggested"); s.db_set("model", model)
    frappe.db.commit()
    return r


@frappe.whitelist()
def ai_suggest_one(segment, hint=""):
    """Return an AI-improved Mongolian for ONE sentence WITHOUT touching the
    stored segment — so a reviewer can ask the model for a better wording and
    then save it as their own suggestion. Callable by reviewers (bounded: one
    sentence per click)."""
    frappe.only_for(["System Manager", "Translation Reviewer"])
    s = frappe.get_doc("Translation Segment", segment)
    proj = frappe.get_doc("Translation Project", s.project)
    model = proj.model or "gpt-4o"
    current = s.final_text or s.ai_suggestion or s.draft_text or ""
    results, _u = _openai(model, _eff_glossary(proj),
                          [{"id": 1, "source_text": s.source_text, "draft_text": current}], hint=hint)
    r = results.get(1) or {"mn": "", "alt": "", "notes": ""}
    return {"mn": r.get("mn", ""), "alt": r.get("alt", ""), "notes": r.get("notes", "")}


@frappe.whitelist()
def term_find(project, find_text):
    """Preview: how many segments' current Mongolian contains find_text.
    Deterministic find (no AI) — the basis of the Find & Replace tool."""
    frappe.only_for(["System Manager", "Translation Reviewer"])
    find_text = (find_text or "").strip()
    if not find_text:
        return {"matches": 0, "seqs": []}
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["seq", "final_text", "ai_suggestion", "draft_text", "status"],
                          order_by="seq asc", limit_page_length=0)
    seqs = []
    for s in segs:
        if s.status == "Locked":
            continue
        cur = s.final_text or s.ai_suggestion or s.draft_text or ""
        if find_text in cur:
            seqs.append(s.seq)
    return {"matches": len(seqs), "seqs": seqs[:50]}


@frappe.whitelist()
def term_replace(project, find_text, replace_text, mode="suggest"):
    """Replace find_text -> replace_text in the Mongolian of every (non-locked)
    matching segment.
      mode='suggest' (default) -> create reviewable red/green suggestions
                                   (safe; reviewers may use this)
      mode='apply'   -> write final_text directly (System Manager only)
    Returns the number of segments changed."""
    frappe.only_for(["System Manager", "Translation Reviewer"])
    find_text = (find_text or "").strip()
    replace_text = (replace_text or "").strip()
    if not find_text or not replace_text:
        frappe.throw("Enter both the text to find and its replacement.")
    if mode == "apply":
        frappe.only_for("System Manager")   # direct edits are editor-only
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "status", "final_text", "ai_suggestion", "draft_text"],
                          order_by="seq asc", limit_page_length=0)
    author = frappe.session.user
    note = "🔤 %s → %s" % (find_text, replace_text)
    changed = 0
    for s in segs:
        if s.status == "Locked":
            continue
        cur = s.final_text or s.ai_suggestion or s.draft_text or ""
        if find_text not in cur:
            continue
        new = cur.replace(find_text, replace_text)
        if new == cur:
            continue
        if mode == "apply":
            frappe.db.set_value("Translation Segment", s.name, {"final_text": new, "status": "Edited"})
        else:
            # skip if an identical open suggestion already exists (idempotent re-runs)
            if frappe.db.exists("Translation Suggestion",
                                {"project": project, "segment": s.name, "status": "Open", "suggested_text": new}):
                continue
            frappe.get_doc({"doctype": "Translation Suggestion", "project": project,
                            "segment": s.name, "origin": "Reviewer", "author": author,
                            "suggested_text": new, "note": note, "status": "Open"}).insert(ignore_permissions=True)
        changed += 1
    frappe.db.commit()
    return {"changed": changed, "mode": mode, "find": find_text, "replace": replace_text}


def _norm_en(t):
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _norm_mn(t):
    t = re.sub(r"[^Ѐ-ӿ0-9]+", " ", (t or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _word_ops(a, b):
    """Word-level LCS diff -> list of ('eq'|'del'|'ins', word)."""
    m, k = len(a), len(b)
    dp = [[0] * (k + 1) for _ in range(m + 1)]
    for i in range(m - 1, -1, -1):
        for j in range(k - 1, -1, -1):
            dp[i][j] = dp[i + 1][j + 1] + 1 if a[i] == b[j] else max(dp[i + 1][j], dp[i][j + 1])
    ops, i, j = [], 0, 0
    while i < m and j < k:
        if a[i] == b[j]:
            ops.append(("eq", a[i])); i += 1; j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            ops.append(("del", a[i])); i += 1
        else:
            ops.append(("ins", b[j])); j += 1
    while i < m:
        ops.append(("del", a[i])); i += 1
    while j < k:
        ops.append(("ins", b[j])); j += 1
    return ops


def _edit_pairs(before, after, ctx=3):
    """Extract each minimal change between `before` and `after` as an
    (old_phrase, new_phrase) pair, each a contiguous substring (a few context
    words on either side) so it can be located in another text."""
    a, b = (before or "").split(), (after or "").split()
    ops = _word_ops(a, b)
    n, idx, out = len(ops), 0, []
    while idx < n:
        if ops[idx][0] == "eq":
            idx += 1
            continue
        start = idx
        while idx < n and ops[idx][0] != "eq":
            idx += 1
        end = idx
        pre = [ops[t][1] for t in range(max(0, start - ctx), start) if ops[t][0] == "eq"]
        post = [ops[t][1] for t in range(end, min(n, end + ctx)) if ops[t][0] == "eq"]
        dels = [ops[t][1] for t in range(start, end) if ops[t][0] == "del"]
        inss = [ops[t][1] for t in range(start, end) if ops[t][0] == "ins"]
        old_str = " ".join(pre + dels + post).strip()
        new_str = " ".join(pre + inss + post).strip()
        if len(old_str) >= 10 and old_str != new_str:
            out.append((old_str, new_str))
    return out


@frappe.whitelist()
def bridge_review(source_project="TRP-00003", target_project="TRP-00004", author="erdenetuya.m@gmail.com"):
    """Bring an imported redactor review from `source_project` onto the working
    book `target_project`.

    The source is a DIFFERENT, paragraph-level translation, so its suggestions
    can't be pasted whole onto v4's sentences (that shows the whole paragraph as
    an insertion). Instead we EXTRACT each actual edit the redactor made
    (changed words + a little context) and apply just that edit to the specific
    target sentence that contains the phrase — producing small, correct red/green
    changes. Edits whose context does not exist in the target are skipped (the
    two translations genuinely differ there). Idempotent: prior origin='Imported'
    suggestions on the target are cleared first."""
    frappe.only_for("System Manager")

    tsegs = frappe.get_all("Translation Segment", filters={"project": target_project},
                           fields=["name", "source_text", "final_text", "ai_suggestion", "draft_text", "status"],
                           limit_page_length=0)
    cur_of, en_map = {}, {}
    for r in tsegs:
        cur_of[r.name] = "" if r.status == "Locked" else (r.final_text or r.ai_suggestion or r.draft_text or "")
        ke = _norm_en(r.source_text)
        if ke and ke not in en_map:
            en_map[ke] = r.name

    # idempotent: clear previously bridged suggestions
    for n in frappe.get_all("Translation Suggestion",
                            filters={"project": target_project, "origin": "Imported"}, pluck="name"):
        frappe.delete_doc("Translation Suggestion", n, force=1, ignore_permissions=True)

    src = frappe.db.sql("""
        SELECT sg.suggested_text AS sug, seg.draft_text AS v3mn, seg.source_text AS en, seg.name AS v3seg
        FROM `tabTranslation Suggestion` sg
        JOIN `tabTranslation Segment` seg ON seg.name = sg.segment
        WHERE sg.project=%s AND sg.origin='Imported' AND sg.status='Open'
    """, (source_project,), as_dict=True)

    # collect edits per target segment (a sentence may receive several)
    edits_for, notes_for, seg_for_v3, edits_total, placed = {}, {}, {}, 0, 0
    for s in src:
        for old_str, new_str in _edit_pairs(s.v3mn, s.sug):
            edits_total += 1
            hits = [name for name, cur in cur_of.items() if cur and old_str in cur]
            if not hits or len(hits) > 3:
                continue  # not locatable, or too generic to be safe
            placed += 1
            for name in hits:
                edits_for.setdefault(name, []).append((old_str, new_str))
                notes_for.setdefault(name, []).append("%s → %s" % (old_str, new_str))
                seg_for_v3[s.v3seg] = name

    made = 0
    for name, edits in edits_for.items():
        cur = cur_of.get(name) or ""
        new = cur
        for old_str, new_str in edits:
            new = new.replace(old_str, new_str)
        if new == cur:
            continue
        frappe.get_doc({
            "doctype": "Translation Suggestion", "project": target_project,
            "segment": name, "origin": "Imported", "author": author,
            "suggested_text": new, "status": "Open",
            "note": "🔤 " + " · ".join(notes_for.get(name, [])[:3]),
        }).insert(ignore_permissions=True)
        made += 1

    # bridge the redactor's comments by English match (skip duplicates)
    cmts = frappe.db.sql("""
        SELECT c.content AS content, seg.source_text AS en, seg.name AS v3seg
        FROM `tabComment` c JOIN `tabTranslation Segment` seg ON seg.name = c.reference_name
        WHERE c.reference_doctype='Translation Segment' AND c.comment_type='Comment' AND seg.project=%s
    """, (source_project,), as_dict=True)
    cmade = 0
    for c in cmts:
        tgt = seg_for_v3.get(c.v3seg) or en_map.get(_norm_en(c.en))
        if not tgt:
            continue
        if frappe.db.exists("Comment", {"reference_doctype": "Translation Segment",
                                        "reference_name": tgt, "comment_type": "Comment",
                                        "content": c.content}):
            continue
        frappe.get_doc({
            "doctype": "Comment", "comment_type": "Comment",
            "reference_doctype": "Translation Segment", "reference_name": tgt,
            "content": c.content,
        }).insert(ignore_permissions=True)
        cmade += 1

    frappe.db.commit()
    return {"source": source_project, "target": target_project,
            "source_suggestions": len(src), "edits_found": edits_total,
            "edits_placed": placed, "suggestions_created": made, "comments_created": cmade}


@frappe.whitelist()
def export_docx(project):
    frappe.only_for("System Manager")
    proj = frappe.get_doc("Translation Project", project)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["seq", "chapter", "final_text", "ai_suggestion", "draft_text"],
                          order_by="seq asc, creation asc")
    from docx import Document
    doc = Document()
    doc.add_heading(proj.title or project, 0)
    # Pure Mongolian manuscript: the book already contains its own Mongolian
    # chapter titles as segments, so style those as headings and emit the rest
    # as body. No English/"Book" scaffolding.
    for s in segs:
        text = (s.get("final_text") or s.get("ai_suggestion") or s.get("draft_text") or "").strip()
        if not text:
            continue
        if _is_headingish(text):
            doc.add_heading(text, level=1)
        else:
            doc.add_paragraph(text)
    buf = io.BytesIO(); doc.save(buf)
    from frappe.utils.file_manager import save_file
    fname = re.sub(r"[^\w\-]+", "_", (proj.title or project)) + "_MN.docx"
    f = save_file(fname, buf.getvalue(), "Translation Project", project, is_private=1)
    frappe.db.commit()   # persist the File record for direct-console runs
    return {"file_url": f.file_url}


@frappe.whitelist()
def add_term(project, source_term, target_term, note=""):
    """Create or update one approved term in the book's termbase."""
    frappe.only_for(["System Manager", "Translation Reviewer"])
    existing = frappe.db.exists("Translation Term", {"project": project, "source_term": source_term})
    if existing:
        d = frappe.get_doc("Translation Term", existing)
        d.target_term = target_term
        if note:
            d.note = note
        d.save(ignore_permissions=True)
    else:
        d = frappe.get_doc({"doctype": "Translation Term", "project": project,
                            "source_term": source_term, "target_term": target_term,
                            "note": note}).insert(ignore_permissions=True)
    frappe.db.commit()
    return {"term": d.name}


@frappe.whitelist()
def apply_term(project, source_term, target_term, note=""):
    """Save the term, then re-generate every non-locked segment that contains
    the source term but doesn't yet use the approved translation — propagating
    the decision across the book."""
    frappe.only_for("System Manager")
    add_term(project, source_term, target_term, note)
    names = frappe.get_all("Translation Segment", pluck="name", filters={
        "project": project, "status": ["!=", "Locked"],
        "source_text": ["like", "%" + source_term + "%"]})
    frappe.enqueue("lac_translation.api._apply_term_job", queue="long", timeout=6000,
                   project=project, source_term=source_term, target_term=target_term, names=names)
    return {"affected": len(names)}


def _apply_term_job(project, source_term, target_term, names):
    proj = frappe.get_doc("Translation Project", project)
    model = proj.model or "gpt-4o-mini"
    glossary = _eff_glossary(proj)
    headers = _headers()
    hint = ("Use exactly '%s' as the Mongolian for the English term '%s' "
            "(adapt case endings, keep the term)." % (target_term, source_term))
    total = len(names)
    for i, name in enumerate(names):
        s = frappe.get_doc("Translation Segment", name)
        eff = s.final_text or s.ai_suggestion or s.draft_text or ""
        if target_term.lower() in eff.lower():
            frappe.publish_realtime("lac_translation_progress", {"project": project, "done": i + 1, "total": total})
            continue  # already consistent
        results, _u = _openai(model, glossary,
                              [{"id": 1, "source_text": s.source_text, "draft_text": s.final_text or s.draft_text or ""}],
                              headers, hint=hint)
        r = results.get(1)
        if r:
            s.db_set("ai_suggestion", r["mn"]); s.db_set("ai_alternative", r["alt"]); s.db_set("ai_rationale", r["notes"])
            if s.status not in ("Accepted", "Edited"):
                s.db_set("status", "Suggested")
        frappe.db.commit()
        frappe.publish_realtime("lac_translation_progress", {"project": project, "done": i + 1, "total": total})


# --------------------------------------------------------------------------- #
# import a marked-up .docx: tracked changes -> Suggestions, comments -> Comments
# --------------------------------------------------------------------------- #
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _para_revisions(p):
    """Return (original_text, suggested_text, authors, comment_ids) for a w:p,
    where original = reject-all-changes and suggested = accept-all-changes."""
    orig, sugg, authors, cids = [], [], set(), set()

    def walk(node, in_ins, in_del):
        for ch in node:
            tag = ch.tag
            if tag == _W + "ins":
                if ch.get(_W + "author"):
                    authors.add(ch.get(_W + "author"))
                walk(ch, True, in_del)
            elif tag == _W + "del":
                if ch.get(_W + "author"):
                    authors.add(ch.get(_W + "author"))
                walk(ch, in_ins, True)
            elif tag == _W + "commentRangeStart":
                cids.add(ch.get(_W + "id"))
                walk(ch, in_ins, in_del)
            elif tag == _W + "t":
                t = ch.text or ""
                if not in_del:
                    sugg.append(t)
                if not in_ins:
                    orig.append(t)
            elif tag == _W + "delText":
                orig.append(ch.text or "")
            else:
                walk(ch, in_ins, in_del)

    walk(p, False, False)
    return "".join(orig).strip(), "".join(sugg).strip(), authors, cids


@frappe.whitelist()
def import_revisions(project, file_url):
    """Read a downloaded Google/Word .docx that has tracked changes + comments,
    and attach them to an existing project's segments as Translation Suggestions
    (Accept/Reject) and Comments. Best-effort text matching to segments."""
    frappe.only_for("System Manager")
    from frappe.utils.file_manager import get_file
    _n, content = get_file(file_url)
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    z = zipfile.ZipFile(io.BytesIO(content))

    comments = {}
    if "word/comments.xml" in z.namelist():
        ct = ET.fromstring(z.read("word/comments.xml"))
        for c in ct.iter(_W + "comment"):
            comments[c.get(_W + "id")] = {
                "author": c.get(_W + "author") or "Reviewer",
                "text": "".join(t.text or "" for t in c.iter(_W + "t")).strip(),
            }

    doc = ET.fromstring(z.read("word/document.xml"))
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "draft_text", "final_text"], order_by="seq asc")

    def norm(t):
        return re.sub(r"\s+", " ", (t or "")).strip()

    def match_seg(text):
        text = norm(text)
        if not text:
            return None
        for s in segs:
            d = norm(s.get("final_text") or s.get("draft_text"))
            if d and (d in text or text in d):
                return s["name"]
        return None

    n_sug = n_cmt = 0
    for p in doc.iter(_W + "p"):
        orig, sugg, authors, cids = _para_revisions(p)
        seg = match_seg(orig) or match_seg(sugg)
        if seg and sugg and sugg != orig:
            frappe.get_doc({
                "doctype": "Translation Suggestion", "project": project, "segment": seg,
                "origin": "Imported", "author": ", ".join(sorted(authors)) or "Reviewer",
                "suggested_text": sugg, "status": "Open",
            }).insert(ignore_permissions=True)
            n_sug += 1
        for cid in cids:
            c = comments.get(cid)
            if c and seg and c["text"]:
                frappe.get_doc({
                    "doctype": "Comment", "comment_type": "Comment",
                    "reference_doctype": "Translation Segment", "reference_name": seg,
                    "content": "[%s] %s" % (c["author"], c["text"]),
                }).insert(ignore_permissions=True)
                n_cmt += 1
    frappe.db.commit()
    return {"suggestions": n_sug, "comments": n_cmt, "segments": len(segs)}


# --------------------------------------------------------------------------- #
# unified: import a whole reviewed translation (marked-up MN [+ optional EN])
# --------------------------------------------------------------------------- #
def _docx_zip(file_url):
    content = _read_docx_content(file_url)
    if isinstance(content, str):
        content = content.encode("utf-8", "ignore")
    return zipfile.ZipFile(io.BytesIO(content))


def _docx_comment_map(z):
    out = {}
    if "word/comments.xml" in z.namelist():
        ct = ET.fromstring(z.read("word/comments.xml"))
        for c in ct.iter(_W + "comment"):
            out[c.get(_W + "id")] = {
                "author": c.get(_W + "author") or "Reviewer",
                "text": "".join(t.text or "" for t in c.iter(_W + "t")).strip(),
            }
    return out


def _docx_rev_paragraphs(z):
    doc = ET.fromstring(z.read("word/document.xml"))
    return [_para_revisions(p) for p in doc.iter(_W + "p")]


@frappe.whitelist()
def import_reviewed(title, mongolian_file_url, english_file_url=None, model="gpt-4o", glossary=""):
    """Create a project from a marked-up Mongolian .docx: draft = the 'before'
    (reject-changes) text; each tracked change becomes an Open Suggestion; each
    comment becomes a segment Comment. English (optional) is position-aligned as
    the source reference (approximate)."""
    frappe.only_for("System Manager")
    zmn = _docx_zip(mongolian_file_url)
    mn = _docx_rev_paragraphs(zmn)
    comments = _docx_comment_map(zmn)
    en = _docx_paragraphs(english_file_url) if english_file_url else []

    proj = frappe.get_doc({
        "doctype": "Translation Project", "title": title, "status": "Review",
        "source_language": "English", "target_language": "Mongolian",
        "model": model, "glossary": glossary,
    }).insert()

    seq = 0
    chapter = "Book"
    n_sug = n_cmt = 0
    for idx, (orig, sugg, authors, cids) in enumerate(mn):
        if not orig and not sugg:
            continue
        en_text = ""
        if idx < len(en):
            style, en_text = en[idx]
            if _is_heading(style, en_text):
                chapter = en_text[:130]
        seq += 1
        seg = frappe.get_doc({
            "doctype": "Translation Segment", "project": proj.name, "seq": seq,
            "chapter": chapter, "status": "Pending",
            "source_text": en_text, "draft_text": orig or sugg,
        }).insert(ignore_permissions=True)
        if sugg and sugg != orig:
            frappe.get_doc({
                "doctype": "Translation Suggestion", "project": proj.name, "segment": seg.name,
                "origin": "Imported", "author": ", ".join(sorted(authors)) or "Reviewer",
                "suggested_text": sugg, "status": "Open",
            }).insert(ignore_permissions=True)
            n_sug += 1
        for cid in cids:
            c = comments.get(cid)
            if c and c["text"]:
                frappe.get_doc({
                    "doctype": "Comment", "comment_type": "Comment",
                    "reference_doctype": "Translation Segment", "reference_name": seg.name,
                    "content": "[%s] %s" % (c["author"], c["text"]),
                }).insert(ignore_permissions=True)
                n_cmt += 1
        if seq % 200 == 0:
            frappe.db.commit()
    proj.db_set("total_segments", seq)
    frappe.db.commit()
    return {"project": proj.name, "segments": seq, "suggestions": n_sug, "comments": n_cmt}


# --------------------------------------------------------------------------- #
# embedding-based re-alignment: fix each segment's source_text + chapter by
# matching the Mongolian draft to its true English counterpart (cross-lingual).
# --------------------------------------------------------------------------- #
def _en_clean_paragraphs(file_ref):
    """English body paragraphs, artifacts stripped, with a running chapter."""
    paras = _docx_paragraphs(file_ref)
    art = re.compile(r"binnenwerk\.indd|^\s*[\divxlcDIVXLC]+\s*$", re.I)
    out = []
    chapter = "Front matter"
    for style, text in paras:
        if not text or art.search(text):
            continue
        is_ch = (style and style.lower().startswith("heading")) or \
                bool(re.match(r"^\d{1,2}\s*[A-Z]", text)) or \
                (text == text.upper() and 1 < len(text.split()) <= 9 and not re.search(r"[.!?]$", text))
        if is_ch:
            chapter = re.sub(r"^\d+\s*", "", text).strip()[:130] or chapter
        out.append({"text": text, "chapter": chapter})
    return out


def _embed(texts, headers):
    vecs = []
    for i in range(0, len(texts), 200):
        chunk = [t if t.strip() else "-" for t in texts[i:i + 200]]
        resp = make_post_request("https://api.openai.com/v1/embeddings", headers=headers,
                                 data=json.dumps({"model": "text-embedding-3-small", "input": chunk}))
        vecs.extend(d["embedding"] for d in resp["data"])
    return vecs


def _unit(v):
    import math
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@frappe.whitelist()
def realign(project, english_file):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._realign_job", queue="long", timeout=8000,
                   project=project, english_file=english_file)
    return {"queued": True}


def _realign_job(project, english_file):
    headers = _headers()
    en = _en_clean_paragraphs(english_file)
    en_texts = [e["text"] for e in en]
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "draft_text"], order_by="seq asc")
    if not en_texts or not segs:
        return
    en_vecs = [_unit(v) for v in _embed(en_texts, headers)]
    mn_vecs = [_unit(v) for v in _embed([s["draft_text"] or "-" for s in segs], headers)]

    cursor = 0
    W = 25
    total = len(segs)
    for i, s in enumerate(segs):
        mv = mn_vecs[i]
        lo = max(0, cursor - 4)
        hi = min(len(en_texts), cursor + W)
        best, bi = -2.0, cursor
        for j in range(lo, hi):
            ev = en_vecs[j]
            c = sum(a * b for a, b in zip(mv, ev))
            if c > best:
                best, bi = c, j
        cursor = max(cursor, bi)
        frappe.db.set_value("Translation Segment", s["name"],
                            {"source_text": en_texts[bi], "chapter": en[bi]["chapter"]},
                            update_modified=False)
        if i % 100 == 0:
            frappe.db.commit()
            frappe.publish_realtime("lac_translation_progress", {"project": project, "done": i, "total": total})
    frappe.db.commit()
    frappe.publish_realtime("lac_translation_progress", {"project": project, "done": total, "total": total})


@frappe.whitelist()
def reset_alignment(project, clear_source=1):
    """Undo bad auto-alignment: clear chapter (and optionally the wrong
    source_text) so segments group cleanly and AI runs in Mongolian-polish
    mode. Keeps draft, suggestions and comments intact."""
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._reset_alignment_job", queue="long",
                   timeout=3000, project=project, clear_source=int(clear_source))
    return {"queued": True}


def _reset_alignment_job(project, clear_source=1):
    names = frappe.get_all("Translation Segment", pluck="name", filters={"project": project})
    for i, n in enumerate(names):
        vals = {"chapter": ""}
        if clear_source:
            vals["source_text"] = ""
        frappe.db.set_value("Translation Segment", n, vals, update_modified=False)
        if i % 200 == 0:
            frappe.db.commit()
    frappe.db.commit()
    return {"reset": len(names)}


# --------------------------------------------------------------------------
# Alignment v2: clean -> sentence-split -> global banded DP over cross-lingual
# embeddings, with a confidence gate. Replaces the old greedy _realign_job.
# --------------------------------------------------------------------------

_EN_NUM_ROW = re.compile(r"^[\d.,%\s\-–:/()]+$")   # table-cell number rows / page nos
_EN_ROMAN = re.compile(r"^[ivxlcdm]+$", re.I)


def _clean_en_sentences(file_ref):
    """English body as sentences, print artifacts & number-tables stripped,
    each tagged with its running chapter. Heading lines set the chapter but
    are not emitted as alignable source. Accepts a PDF or DOCX source."""
    paras = _source_paragraphs(file_ref)
    out = []
    chapter = "Front matter"
    for style, text in paras:
        t = (text or "").strip()
        if not t:
            continue
        low = t.lower()
        if "binnenwerk.indd" in low:                 # print artifact
            continue
        if _EN_NUM_ROW.match(t) or _EN_ROMAN.match(t):  # page no / number table cell
            continue
        is_ch = (style and style.lower().startswith("heading")) or \
                bool(re.match(r"^\d{1,2}\s*[A-Z]", t)) or \
                (t == t.upper() and 1 < len(t.split()) <= 9 and not re.search(r"[.!?]$", t))
        if is_ch:
            chapter = re.sub(r"^\d+\s*", "", t).strip()[:130] or chapter
            continue
        for s in _split_sentences(t):
            s = s.strip()
            if len(s) >= 2:
                out.append({"text": s, "chapter": chapter})
    return out


def _align_dp(S, band):
    """Monotonic (non-decreasing) assignment of each MN row j to an EN col e
    that maximises total cosine, within a diagonal band. Allows many MN -> one
    EN (Mongolian splits English sentences). Returns list of (col, score)."""
    import numpy as np
    P, N = S.shape
    NEG = -1e9

    def center(j):
        return int(round(j * (N - 1) / max(1, (P - 1))))

    def window(j):
        c = center(j)
        return max(0, c - band), min(N, c + band + 1)

    idxN = np.arange(N)
    dp = np.full(N, NEG)
    lo, hi = window(0)
    dp[lo:hi] = S[0, lo:hi]
    back = [None] * P

    for j in range(1, P):
        # prefix max + argmax of previous row (enforces e(j) >= e(j-1)),
        # fully vectorised: pm[e] = max_{e'<=e} dp[e'], pa[e] = its argmax
        pm = np.maximum.accumulate(dp)
        newmax = np.concatenate(([True], pm[1:] > pm[:-1]))
        pa = np.maximum.accumulate(np.where(newmax, idxN, 0))
        lo, hi = window(j)
        ndp = np.full(N, NEG)
        ndp[lo:hi] = S[j, lo:hi] + pm[lo:hi]
        bj = np.full(N, -1, dtype="int64")
        bj[lo:hi] = pa[lo:hi]
        back[j] = bj
        dp = ndp

    e = int(np.argmax(dp))
    path = [0] * P
    for j in range(P - 1, -1, -1):
        path[j] = e
        if j > 0:
            e = int(back[j][e])
            if e < 0:
                e = 0
    return [(path[j], float(S[j, path[j]])) for j in range(P)]


@frappe.whitelist()
def align2(project, english_file, threshold=0.40, write=1):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._align2_job", queue="long", timeout=9000,
                   project=project, english_file=english_file,
                   threshold=float(threshold), write=int(write))
    return {"queued": True}


def _align2_job(project, english_file, threshold=0.40, write=1):
    """Clean+align English onto the project's Mongolian segments. Writes
    source_text + chapter only where confidence >= threshold; below that the
    segment is left in Mongolian-polish mode (blank source). Prints a summary
    and sample pairs so the threshold can be calibrated; returns that summary."""
    import numpy as np
    threshold = float(threshold)
    write = int(write)
    headers = _headers()
    en = _clean_en_sentences(english_file)
    en_texts = [e["text"] for e in en]
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "draft_text"], order_by="seq asc")
    if not en_texts or not segs:
        return {"error": "missing english or segments"}

    EN = np.asarray([_unit(v) for v in _embed(en_texts, headers)], dtype="float64")
    MN = np.asarray([_unit(v) for v in _embed([s["draft_text"] or "-" for s in segs], headers)],
                    dtype="float64")
    S = MN.dot(EN.T)                      # P x N cosine matrix
    N = EN.shape[0]
    band = max(35, int(N * 0.07))
    matches = _align_dp(S, band)          # per MN seg: (en_col, score)

    scores = sorted(m[1] for m in matches)
    P = len(matches)
    def pct(p):
        return round(scores[min(P - 1, int(p / 100.0 * P))], 3)
    hist = {"p10": pct(10), "p25": pct(25), "p50": pct(50),
            "p75": pct(75), "p90": pct(90)}
    kept = sum(1 for _, sc in matches if sc >= threshold)

    if write:
        for i, s in enumerate(segs):
            col, sc = matches[i]
            if sc >= threshold:
                vals = {"source_text": en_texts[col], "chapter": en[col]["chapter"]}
            else:
                vals = {"source_text": "", "chapter": ""}
            frappe.db.set_value("Translation Segment", s["name"], vals, update_modified=False)
            if i % 100 == 0:
                frappe.db.commit()
                frappe.publish_realtime("lac_translation_progress",
                                        {"project": project, "done": i, "total": P})
        frappe.db.commit()
        frappe.publish_realtime("lac_translation_progress",
                                {"project": project, "done": P, "total": P})

    # sample pairs spread across the book for eyeball calibration
    samples = []
    step = max(1, P // 18)
    for i in range(0, P, step):
        col, sc = matches[i]
        samples.append({
            "seq": segs[i]["seq"], "score": round(sc, 3),
            "en": en_texts[col][:70], "mn": (segs[i]["draft_text"] or "")[:70],
        })
    summary = {"en_sentences": N, "mn_segments": P, "score_pctiles": hist,
               "threshold": threshold, "kept": kept, "blanked": P - kept,
               "written": bool(write), "samples": samples}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# --------------------------------------------------------------------------
# Alignment v3: LLM-based. Embeddings can't separate true MN<->EN pairs
# (text-embedding-3-small scores real pairs ~0.3-0.45, same as noise), so we
# let the model that DOES read both languages do the alignment by meaning.
# Slides an English cursor down the book; each batch of Mongolian sentences is
# aligned against a generous English window. Robust to 1-EN->2-MN splits and
# to Mongolian-only additions (which map to nothing and stay in polish mode).
# --------------------------------------------------------------------------

def _coerce_idx(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, int):
        return x
    s = re.sub(r"[^\d]", "", str(x))
    return int(s) if s else None


def _align_llm_pairs(model, headers, win_en, mn_lines_text):
    """win_en: list of (global_idx, text). Returns {mn_local:int -> [en_global_idx]}"""
    en_block = "\n".join("E%d: %s" % (gi, t) for gi, t in win_en)
    instr = (
        "You are aligning an English source book to its Mongolian translation. "
        "Below are numbered English sentences (E#) and numbered Mongolian sentences "
        "(M#) from the SAME region of the book, in reading order. For EACH Mongolian "
        "sentence, decide which English sentence(s) it translates. A Mongolian "
        "sentence usually maps to ONE English sentence, sometimes TWO consecutive "
        "ones, or NONE (a heading, page number, or translator addition with no "
        "English counterpart -> return an empty list). Judge by MEANING, not "
        "position. Use the exact numbers shown. Return JSON: "
        '{"pairs":[{"mn":<M number>,"en":[<E numbers>]}]} with one entry per M.'
    )
    payload = {
        "model": model, "temperature": 0, "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a meticulous bilingual (English/Mongolian) alignment tool. Output only valid JSON."},
            {"role": "user", "content": instr + "\n\nENGLISH:\n" + en_block + "\n\nMONGOLIAN:\n" + mn_lines_text},
        ],
    }
    resp = make_post_request("https://api.openai.com/v1/chat/completions",
                             headers=headers, data=json.dumps(payload))
    data = json.loads(resp["choices"][0]["message"]["content"])
    out = {}
    for p in data.get("pairs", []):
        m = _coerce_idx(p.get("mn"))
        if m is None:
            continue
        ens = [_coerce_idx(e) for e in (p.get("en") or [])]
        out[m] = [e for e in ens if e is not None]
    return out, resp.get("usage", {})


def _is_headingish(t):
    t = (t or "").strip()
    if not t or len(t) > 95:
        return False
    letters = [c for c in t if c.isalpha()]
    if len(letters) < 3:
        return False
    up = sum(1 for c in letters if c.upper() == c and c.lower() != c)
    return up / len(letters) > 0.7 and not t.endswith((".", "!", "?", ",", ";"))


def _en_chapters(en):
    """[(en_index, title)] at each chapter change in the cleaned English."""
    out, last = [], None
    for i, e in enumerate(en):
        c = e.get("chapter") or ""
        if c and c != last:
            out.append((i, c))
            last = c
    return out


def _mn_chapters(segs):
    """[(position_in_segs, heading_text)] for heading-like Mongolian segments."""
    return [(i, s["draft_text"]) for i, s in enumerate(segs)
            if _is_headingish(s.get("draft_text"))]


def _pair_chapters(model, headers, en_ch, mn_ch):
    """Ask the model to match Mongolian chapter titles to English ones.
    Returns sorted, strictly-increasing anchors [(mn_pos, en_index)]."""
    if not en_ch or not mn_ch:
        return [], {}
    en_lines = "\n".join("E%d: %s" % (i, t[:80]) for i, (_, t) in enumerate(en_ch))
    mn_lines = "\n".join("M%d: %s" % (i, t[:80]) for i, (_, t) in enumerate(mn_ch))
    instr = ("These are chapter/section TITLES from an English book (E#) and its "
             "Mongolian translation (M#), each list in book order. Match each "
             "Mongolian title to the English title with the same meaning. Ignore "
             "titles that clearly have no counterpart. Judge by meaning. Return "
             'JSON: {"pairs":[{"m":<M number>,"e":<E number>}]}.')
    payload = {"model": model, "temperature": 0, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": "You are a bilingual (English/Mongolian) alignment tool. Output only valid JSON."},
                            {"role": "user", "content": instr + "\n\nENGLISH TITLES:\n" + en_lines + "\n\nMONGOLIAN TITLES:\n" + mn_lines}]}
    resp = make_post_request("https://api.openai.com/v1/chat/completions",
                             headers=headers, data=json.dumps(payload))
    data = json.loads(resp["choices"][0]["message"]["content"])
    anchors = []
    for p in data.get("pairs", []):
        mi, ei = _coerce_idx(p.get("m")), _coerce_idx(p.get("e"))
        if mi is None or ei is None or mi >= len(mn_ch) or ei >= len(en_ch):
            continue
        anchors.append((mn_ch[mi][0], en_ch[ei][0]))
    anchors.sort()
    # keep only strictly increasing en_index (drop any out-of-order pair)
    clean, last_en = [], -1
    for mp, ei in anchors:
        if ei > last_en:
            clean.append((mp, ei))
            last_en = ei
    return clean, resp.get("usage", {})


def _interp_center(anchors, pos, P, N):
    """Piecewise-linear English index for Mongolian position `pos`, using the
    chapter anchors plus (0,0) and (P,N) endpoints. Adapts the rate per chapter."""
    pts = [(0, 0)] + list(anchors) + [(max(1, P - 1), max(1, N - 1))]
    for k in range(len(pts) - 1):
        (m0, e0), (m1, e1) = pts[k], pts[k + 1]
        if m0 <= pos <= m1:
            if m1 == m0:
                return e1
            return e0 + (e1 - e0) * (pos - m0) / (m1 - m0)
    return pos * (float(N) / float(P))


@frappe.whitelist()
def align_llm(project, english_file, model="gpt-4o", batch=12, from_seq=None, to_seq=None):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._align_llm_job", queue="long", timeout=12000,
                   project=project, english_file=english_file, model=model, batch=int(batch),
                   from_seq=from_seq, to_seq=to_seq)
    return {"queued": True}


def _align_llm_job(project, english_file, model="gpt-4o", batch=12,
                   from_seq=None, to_seq=None):
    """Align English onto the Mongolian segments with GPT-4o. The English
    window is anchored to the running EN<->MN correspondence and extrapolated
    at the true EN/MN rate, re-anchoring on every confident match, so it cannot
    drift (the bug that made the plain sliding cursor fall off after ~90 segs).
    Optional from_seq/to_seq restrict to a slice for cheap validation; segments
    outside the slice are left untouched."""
    headers = _headers()
    batch = int(batch)
    en = _clean_en_sentences(english_file)
    # drop table-of-contents dotted leaders and stray number rows that survive
    en = [e for e in en
          if "....." not in e["text"] and not _EN_NUM_ROW.match(e["text"].strip())]
    en_texts = [e["text"] for e in en]
    N = len(en_texts)
    allsegs = frappe.get_all("Translation Segment", filters={"project": project},
                             fields=["name", "seq", "draft_text"], order_by="seq asc")
    P = len(allsegs)
    if not N or not P:
        return {"error": "missing english or segments"}
    seq_of = {s["name"]: idx for idx, s in enumerate(allsegs)}  # position by name

    fs = int(from_seq) if from_seq not in (None, "") else None
    ts = int(to_seq) if to_seq not in (None, "") else None
    segs = [s for s in allsegs
            if (fs is None or s["seq"] >= fs) and (ts is None or s["seq"] <= ts)]
    if not segs:
        return {"error": "no segments in range"}

    half = batch + 20                                  # English window half-width
    written = 0
    in_tok = out_tok = 0

    # Chapter anchoring: the MN<->EN rate varies wildly per chapter, so a single
    # global rate slides off. Pair chapter titles once, then interpolate the
    # window position between those anchors so the rate adapts per chapter.
    en_ch = _en_chapters(en)
    mn_ch = _mn_chapters(allsegs)
    anchors = []
    try:
        anchors, pu = _pair_chapters(model, headers, en_ch, mn_ch)
        in_tok += pu.get("prompt_tokens", 0)
        out_tok += pu.get("completion_tokens", 0)
    except Exception:
        anchors = []

    i = 0
    while i < len(segs):
        chunk = segs[i:i + batch]
        pos0 = seq_of[chunk[0]["name"]]
        posN = seq_of[chunk[-1]["name"]]
        c0 = _interp_center(anchors, pos0, P, N)
        c1 = _interp_center(anchors, posN, P, N)
        lo = max(0, int(round(min(c0, c1))) - half)
        hi = min(N, int(round(max(c0, c1))) + half)
        win_en = [(k, en_texts[k]) for k in range(lo, hi)]
        mn_lines = "\n".join("M%d: %s" % (n, (chunk[n]["draft_text"] or "")[:280])
                             for n in range(len(chunk)))
        try:
            pairs, usage = _align_llm_pairs(model, headers, win_en, mn_lines)
        except Exception:
            pairs, usage = {}, {}
        in_tok += usage.get("prompt_tokens", 0)
        out_tok += usage.get("completion_tokens", 0)

        for n in range(len(chunk)):
            ens = [e for e in pairs.get(n, []) if lo <= e < hi]
            if ens:
                ens = sorted(set(ens))
                src = " ".join(en_texts[e] for e in ens)
                vals = {"source_text": src, "chapter": en[ens[0]]["chapter"]}
                written += 1
            else:
                vals = {"source_text": "", "chapter": ""}
            frappe.db.set_value("Translation Segment", chunk[n]["name"], vals,
                                update_modified=False)

        frappe.db.commit()
        frappe.publish_realtime("lac_translation_progress",
                                {"project": project, "done": min(i + batch, len(segs)),
                                 "total": len(segs)})
        i += batch

    cost = round(in_tok / 1e6 * 2.5 + out_tok / 1e6 * 10.0, 4)   # gpt-4o pricing
    summary = {"en_sentences": N, "mn_segments_total": P, "processed": len(segs),
               "range": [fs, ts], "chapter_anchors": len(anchors), "aligned": written,
               "blank_polish_mode": len(segs) - written, "model": model,
               "prompt_tokens": in_tok, "completion_tokens": out_tok,
               "est_cost_usd": cost}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# --------------------------------------------------------------------------
# Gap-fill pass: reuse the first pass's confident matches as DENSE anchors so
# each still-blank run is bracketed by known-good points, and re-align only the
# blanks. Fixes regions where Mongolian chapter headings weren't preserved as
# segments (so the first pass had to interpolate blindly). Never overwrites an
# existing alignment.
# --------------------------------------------------------------------------

@frappe.whitelist()
def align_gapfill(project, english_file, model="gpt-4o", batch=12):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._align_gapfill_job", queue="long", timeout=12000,
                   project=project, english_file=english_file, model=model, batch=int(batch))
    return {"queued": True}


def _gapfill_once(project, headers, en, en_texts, N, model, batch, half):
    """One gap-fill pass. Returns (newly_aligned, anchors, blank_runs, in_tok, out_tok)."""
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "draft_text", "source_text"], order_by="seq asc")
    P = len(segs)
    pref = {}
    for i, t in enumerate(en_texts):
        k = t[:40]
        if k not in pref:
            pref[k] = i
    raw = []
    for pos, s in enumerate(segs):
        src = (s.get("source_text") or "").strip()
        if not src:
            continue
        idx = pref.get(src[:40])
        if idx is not None:
            raw.append((pos, idx))
    raw.sort()
    anchors, last = [], -1
    for mp, ei in raw:
        if ei > last:
            anchors.append((mp, ei))
            last = ei

    runs, cur = [], []
    for pos, s in enumerate(segs):
        if (s.get("source_text") or "").strip():
            if cur:
                runs.append(cur); cur = []
        else:
            cur.append(pos)
    if cur:
        runs.append(cur)

    written = 0
    in_tok = out_tok = 0
    for run in runs:
        i = 0
        while i < len(run):
            positions = run[i:i + batch]
            chunk = [segs[p] for p in positions]
            c0 = _interp_center(anchors, positions[0], P, N)
            c1 = _interp_center(anchors, positions[-1], P, N)
            lo = max(0, int(round(min(c0, c1))) - half)
            hi = min(N, int(round(max(c0, c1))) + half)
            if hi > lo:
                win_en = [(k, en_texts[k]) for k in range(lo, hi)]
                mn_lines = "\n".join("M%d: %s" % (n, (chunk[n]["draft_text"] or "")[:280])
                                     for n in range(len(chunk)))
                try:
                    pairs, usage = _align_llm_pairs(model, headers, win_en, mn_lines)
                except Exception:
                    pairs, usage = {}, {}
                in_tok += usage.get("prompt_tokens", 0)
                out_tok += usage.get("completion_tokens", 0)
                for n in range(len(chunk)):
                    ens = [e for e in pairs.get(n, []) if lo <= e < hi]
                    if ens:
                        ens = sorted(set(ens))
                        frappe.db.set_value("Translation Segment", chunk[n]["name"],
                                            {"source_text": " ".join(en_texts[e] for e in ens),
                                             "chapter": en[ens[0]]["chapter"]},
                                            update_modified=False)
                        written += 1
                frappe.db.commit()
            i += batch
        frappe.publish_realtime("lac_translation_progress",
                                {"project": project, "done": written, "total": P})
    return written, len(anchors), len(runs), in_tok, out_tok


def _align_gapfill_job(project, english_file, model="gpt-4o", batch=12,
                       max_iters=4, min_new=4):
    """Iterated gap-fill. Each pass turns the previous pass's new matches into
    anchors, so coverage cascades into anchor-starved regions. Stops when a
    pass adds fewer than min_new, or after max_iters."""
    headers = _headers()
    batch = int(batch)
    max_iters = int(max_iters)
    min_new = int(min_new)
    en = _clean_en_sentences(english_file)
    en = [e for e in en
          if "....." not in e["text"] and not _EN_NUM_ROW.match(e["text"].strip())]
    en_texts = [e["text"] for e in en]
    N = len(en_texts)
    if not N:
        return {"error": "missing english"}
    half = batch + 30

    passes = []
    tot_new = tot_in = tot_out = 0
    for it in range(max_iters):
        new, nanch, nruns, itok, otok = _gapfill_once(
            project, headers, en, en_texts, N, model, batch, half)
        tot_new += new
        tot_in += itok
        tot_out += otok
        passes.append({"pass": it + 1, "anchors": nanch, "blank_runs": nruns, "newly_aligned": new})
        if new < min_new:
            break

    cost = round(tot_in / 1e6 * 2.5 + tot_out / 1e6 * 10.0, 4)
    summary = {"passes": passes, "total_newly_aligned": tot_new,
               "prompt_tokens": tot_in, "completion_tokens": tot_out, "est_cost_usd": cost}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# --------------------------------------------------------------------------
# Targeted range aligner: for an anchor-starved zone, give the whole English
# chapter-band as the window so the model can find each match by meaning even
# with no interior anchors. The band is auto-derived from the aligned segments
# bracketing the range (or passed explicitly). A monotonic cursor keeps it
# ordered and trims tokens as it advances. Only fills blanks.
# --------------------------------------------------------------------------

@frappe.whitelist()
def align_range(project, english_file, from_seq, to_seq, en_from=None, en_to=None,
                model="gpt-4o", batch=10):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._align_range_job", queue="long", timeout=9000,
                   project=project, english_file=english_file, from_seq=int(from_seq),
                   to_seq=int(to_seq), en_from=en_from, en_to=en_to, model=model, batch=int(batch))
    return {"queued": True}


def _align_range_job(project, english_file, from_seq, to_seq, en_from=None, en_to=None,
                     model="gpt-4o", batch=10):
    headers = _headers()
    from_seq, to_seq, batch = int(from_seq), int(to_seq), int(batch)
    en = _clean_en_sentences(english_file)
    en = [e for e in en
          if "....." not in e["text"] and not _EN_NUM_ROW.match(e["text"].strip())]
    en_texts = [e["text"] for e in en]
    N = len(en_texts)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "draft_text", "source_text"], order_by="seq asc")
    if not N or not segs:
        return {"error": "missing"}

    pref = {}
    for i, t in enumerate(en_texts):
        pref.setdefault(t[:40], i)

    # auto-derive the English band from the nearest aligned segments outside [from,to]
    if en_from is None or en_to is None:
        before = [pref.get((s["source_text"] or "")[:40]) for s in segs
                  if s["seq"] < from_seq and (s.get("source_text") or "").strip()]
        after = [pref.get((s["source_text"] or "")[:40]) for s in segs
                 if s["seq"] > to_seq and (s.get("source_text") or "").strip()]
        before = [x for x in before if x is not None]
        after = [x for x in after if x is not None]
        if en_from is None:
            en_from = max(before) if before else 0
        if en_to is None:
            en_to = min(after) if after else N - 1
    en_from = max(0, int(en_from) - 5)
    en_to = min(N, int(en_to) + 6)

    target = [s for s in segs if from_seq <= s["seq"] <= to_seq
              and not (s.get("source_text") or "").strip()]
    written = 0
    in_tok = out_tok = 0
    cursor = en_from
    i = 0
    while i < len(target):
        chunk = target[i:i + batch]
        lo = max(en_from, cursor - 8)
        win_en = [(k, en_texts[k]) for k in range(lo, en_to)]
        mn_lines = "\n".join("M%d: %s" % (n, (chunk[n]["draft_text"] or "")[:280])
                             for n in range(len(chunk)))
        try:
            pairs, usage = _align_llm_pairs(model, headers, win_en, mn_lines)
        except Exception:
            pairs, usage = {}, {}
        in_tok += usage.get("prompt_tokens", 0)
        out_tok += usage.get("completion_tokens", 0)
        used = []
        for n in range(len(chunk)):
            ens = [e for e in pairs.get(n, []) if lo <= e < en_to]
            if ens:
                ens = sorted(set(ens))
                frappe.db.set_value("Translation Segment", chunk[n]["name"],
                                    {"source_text": " ".join(en_texts[e] for e in ens),
                                     "chapter": en[ens[0]]["chapter"]}, update_modified=False)
                written += 1
                used.extend(ens)
        if used:
            cursor = min(max(used) + 1, en_to - 1)
        frappe.db.commit()
        frappe.publish_realtime("lac_translation_progress",
                                {"project": project, "done": written, "total": len(target)})
        i += batch

    cost = round(in_tok / 1e6 * 2.5 + out_tok / 1e6 * 10.0, 4)
    summary = {"range": [from_seq, to_seq], "en_band": [en_from, en_to],
               "target_blanks": len(target), "aligned": written,
               "prompt_tokens": in_tok, "completion_tokens": out_tok, "est_cost_usd": cost}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# --------------------------------------------------------------------------
# Translation Completeness Audit: find English passages omitted from the
# Mongolian, verify each is genuinely missing (not just summarised nearby),
# draft the Mongolian, and write a review report attached to the project.
# --------------------------------------------------------------------------

def _omission_runs(english_file, project, min_run):
    """Return [{chapter, after_seq, en_from, en_to, en_sents[], mn_before, mn_after}]
    for runs of >= min_run consecutive body English sentences with no Mongolian."""
    en = _clean_en_sentences(english_file)
    en = [e for e in en
          if "....." not in e["text"] and not _EN_NUM_ROW.match(e["text"].strip())]
    en_texts = [e["text"] for e in en]
    N = len(en_texts)
    pref = {}
    for i, t in enumerate(en_texts):
        pref.setdefault(t[:40], i)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["seq", "source_text", "draft_text"], order_by="seq asc")
    used = {}
    byseq = {}
    for s in segs:
        byseq[s["seq"]] = s
        src = (s.get("source_text") or "").strip()
        if src:
            idx = pref.get(src[:40])
            if idx is not None:
                used[idx] = s["seq"]
    BODY_LO, BODY_HI = 132, 4815
    back = {"Index", "LITERATURE", "ABOUT THE AUTHOR", "DISCLAIMER", "MASQUELIER"}
    runs, cur = [], []
    for i in range(N):
        good = (i not in used) and BODY_LO <= i < BODY_HI and len(en_texts[i]) >= 60 \
            and en[i]["chapter"] not in back
        if good:
            cur.append(i)
        else:
            if len(cur) >= min_run:
                runs.append(cur)
            cur = []
    if len(cur) >= min_run:
        runs.append(cur)

    def mn_before(run):
        j = run[0] - 1
        while j >= 0:
            if j in used:
                return byseq.get(used[j], {}).get("draft_text") or ""
            j -= 1
        return ""

    def mn_after(run):
        j = run[-1] + 1
        while j < N:
            if j in used:
                return byseq.get(used[j], {}).get("draft_text") or ""
            j += 1
        return ""

    def ins_seq(run):
        j = run[0] - 1
        while j >= 0:
            if j in used:
                return used[j]
            j -= 1
        return None

    out = []
    for r in runs:
        out.append({
            "chapter": en[r[0]]["chapter"], "after_seq": ins_seq(r),
            "en_from": r[0], "en_to": r[-1],
            "en_sents": [en_texts[k] for k in r],
            "mn_before": mn_before(r)[:400], "mn_after": mn_after(r)[:400],
        })
    return out


def _omission_check(model, headers, glossary, gap):
    """One call: verdict (missing/present) + Mongolian draft if missing."""
    en_block = "\n".join("- " + s for s in gap["en_sents"])
    instr = (
        "A passage from an English book may be MISSING from its Mongolian "
        "translation. Below is the Mongolian immediately BEFORE and AFTER the "
        "spot, then the English passage. Decide whether this English content is "
        "genuinely ABSENT from the surrounding Mongolian, or whether it is "
        "actually PRESENT there (summarised or reworded). If it is absent, "
        "produce a faithful, publication-ready Mongolian translation of the "
        "whole passage. Return JSON: "
        '{"verdict":"missing"|"present","mn":"<Mongolian translation if missing, else empty>"}.'
    )
    if glossary:
        instr += "\n\nGlossary / approved terminology:\n" + glossary
    user = (instr
            + "\n\nMONGOLIAN BEFORE:\n" + (gap["mn_before"] or "(start)")
            + "\n\nMONGOLIAN AFTER:\n" + (gap["mn_after"] or "(end)")
            + "\n\nENGLISH PASSAGE:\n" + en_block)
    payload = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": "You are a meticulous bilingual (English/Mongolian) book translator and editor. Output only valid JSON."},
                            {"role": "user", "content": user}]}
    resp = make_post_request("https://api.openai.com/v1/chat/completions",
                             headers=headers, data=json.dumps(payload))
    data = json.loads(resp["choices"][0]["message"]["content"])
    return (data.get("verdict", "missing"), data.get("mn", ""), resp.get("usage", {}))


@frappe.whitelist()
def omission_report(project, english_file, min_run=6, model="gpt-4o"):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._omission_report_job", queue="long", timeout=15000,
                   project=project, english_file=english_file, min_run=int(min_run), model=model)
    return {"queued": True}


def _esc(t):
    return (t or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _omission_report_job(project, english_file, min_run=6, model="gpt-4o"):
    headers = _headers()
    min_run = int(min_run)
    proj = frappe.get_doc("Translation Project", project)
    glossary = _eff_glossary(proj)
    gaps = _omission_runs(english_file, project, min_run)

    in_tok = out_tok = 0
    confirmed = []
    for g in gaps:
        try:
            verdict, mn, usage = _omission_check(model, headers, glossary, g)
        except Exception:
            verdict, mn, usage = "missing", "", {}
        in_tok += usage.get("prompt_tokens", 0)
        out_tok += usage.get("completion_tokens", 0)
        if verdict == "missing":
            g["mn"] = mn
            confirmed.append(g)
        frappe.publish_realtime("lac_translation_progress",
                                {"project": project, "done": len(confirmed), "total": len(gaps)})

    n_sents = sum(len(g["en_sents"]) for g in confirmed)
    rows = []
    for g in confirmed:
        rows.append(
            '<div class="gap"><div class="loc">Chapter: <b>%s</b> &nbsp;·&nbsp; insert after Mongolian sentence #%s &nbsp;·&nbsp; %d sentences</div>'
            '<div class="cols"><div class="en"><div class="lbl">English (missing)</div><p>%s</p></div>'
            '<div class="mn"><div class="lbl">Proposed Mongolian</div><p>%s</p></div></div></div>'
            % (_esc(g["chapter"]), g["after_seq"], len(g["en_sents"]),
               _esc(" ".join(g["en_sents"])), _esc(g.get("mn", "")))
        )
    html = (
        "<html><head><meta charset='utf-8'><title>Translation Completeness Audit — %s</title>"
        "<style>body{font-family:system-ui,Arial,sans-serif;max-width:1000px;margin:24px auto;color:#1a1a1a}"
        "h1{color:#00707E}.sum{background:#f2f7f8;border:1px solid #d7e6e8;padding:12px 16px;border-radius:8px;margin-bottom:20px}"
        ".gap{border:1px solid #e2e2e2;border-radius:8px;margin:14px 0;padding:12px 14px}"
        ".loc{font-size:12px;color:#666;margin-bottom:8px}.cols{display:flex;gap:16px}"
        ".en,.mn{flex:1}.lbl{font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:#00707E;margin-bottom:4px}"
        ".en p{background:#fff8f0;padding:8px;border-radius:6px}.mn p{background:#f0f8f4;padding:8px;border-radius:6px}"
        "p{white-space:pre-wrap;line-height:1.5;margin:0}</style></head><body>"
        "<h1>Translation Completeness Audit</h1>"
        "<div class='sum'>Project <b>%s</b> · English source vs Mongolian translation.<br>"
        "<b>%d</b> confirmed omitted passages (of %d candidate gaps scanned), totalling <b>%d</b> English sentences "
        "with no Mongolian counterpart. Each shows a proposed Mongolian translation for the editor to review.</div>%s"
        "</body></html>"
        % (_esc(project), _esc(project), len(confirmed), len(gaps), n_sents, "".join(rows) or "<p>No substantial omissions found.</p>")
    )

    from frappe.utils.file_manager import save_file
    fname = "omission_audit_%s.html" % project
    f = save_file(fname, html.encode("utf-8"), "Translation Project", project, is_private=1)
    frappe.db.commit()   # so the File record persists for direct-console runs too

    cost = round(in_tok / 1e6 * 2.5 + out_tok / 1e6 * 10.0, 4)
    summary = {"candidate_gaps": len(gaps), "confirmed_missing": len(confirmed),
               "omitted_sentences": n_sents, "report_url": f.file_url,
               "prompt_tokens": in_tok, "completion_tokens": out_tok, "est_cost_usd": cost}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


# --------------------------------------------------------------------------
# Complete the book: translate the verified-missing English passages and insert
# them as reviewable Mongolian segments at their correct position. Marked with
# model="AI-omission" and status="Suggested" so they're clearly AI-added,
# reviewable in the portal, exportable, and fully reversible (revert_omissions).
# --------------------------------------------------------------------------

@frappe.whitelist()
def apply_omissions(project, english_file, min_run=4, model="gpt-4o"):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._apply_omissions_job", queue="long", timeout=20000,
                   project=project, english_file=english_file, min_run=int(min_run), model=model)
    return {"queued": True}


def _apply_omissions_job(project, english_file, min_run=4, model="gpt-4o"):
    headers = _headers()
    min_run = int(min_run)
    proj = frappe.get_doc("Translation Project", project)
    glossary = _eff_glossary(proj)
    # Idempotent: clear any previous AI-inserted passages first so re-running
    # (e.g. at a different min_run) rebuilds cleanly from the original
    # translation instead of duplicating earlier fills.
    old = frappe.get_all("Translation Segment",
                         filters={"project": project, "model": "AI-omission"}, pluck="name")
    for n in old:
        frappe.delete_doc("Translation Segment", n, ignore_permissions=True, force=True)
    if old:
        frappe.db.commit()
    gaps = _omission_runs(english_file, project, min_run)

    in_tok = out_tok = 0
    inserted = 0
    ins_sents = 0
    for gi, g in enumerate(gaps):
        try:
            verdict, mn, usage = _omission_check(model, headers, glossary, g)
        except Exception:
            verdict, mn, usage = "missing", "", {}
        in_tok += usage.get("prompt_tokens", 0)
        out_tok += usage.get("completion_tokens", 0)
        if verdict == "missing" and (mn or "").strip():
            seq = g["after_seq"] if g["after_seq"] is not None else 0
            frappe.get_doc({
                "doctype": "Translation Segment", "project": project, "seq": seq,
                "chapter": g["chapter"], "status": "Suggested", "model": "AI-omission",
                "source_text": " ".join(g["en_sents"]),
                "ai_suggestion": mn, "final_text": mn, "draft_text": "",
            }).insert(ignore_permissions=True)
            inserted += 1
            ins_sents += len(g["en_sents"])
        if gi % 20 == 0:
            frappe.db.commit()
        frappe.publish_realtime("lac_translation_progress",
                                {"project": project, "done": gi + 1, "total": len(gaps)})
    frappe.db.commit()

    cost = round(in_tok / 1e6 * 2.5 + out_tok / 1e6 * 10.0, 4)
    summary = {"candidate_gaps": len(gaps), "inserted_passages": inserted,
               "english_sentences_added": ins_sents, "model": model,
               "prompt_tokens": in_tok, "completion_tokens": out_tok, "est_cost_usd": cost}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


@frappe.whitelist()
def revert_omissions(project):
    """Delete every AI-inserted omission segment (fully undo apply_omissions)."""
    frappe.only_for("System Manager")
    names = frappe.get_all("Translation Segment",
                           filters={"project": project, "model": "AI-omission"}, pluck="name")
    for n in names:
        frappe.delete_doc("Translation Segment", n, ignore_permissions=True, force=True)
    frappe.db.commit()
    return {"deleted": len(names)}


@frappe.whitelist()
def faithfulness_audit(project, model="gpt-4o", batch=8):
    """Flag translator passages whose Mongolian DROPS content from the English
    source (the condensation risk the omission audit can't see — a long EN
    sentence rendered as a short MN one that quietly loses a fact/clause/number).

    Context-aware to avoid alignment false positives: rows whose English is
    duplicated across many segments (cover/list splitting) are skipped, and each
    English sentence is judged against a MONGOLIAN WINDOW (previous + current +
    next rows) so content that drifted into a neighbouring row is not mistaken
    for a drop. Sets a ⚠ note on each flagged segment (shown in the reading
    view) and writes a summary report. Changes NO translation. Idempotent."""
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._faithfulness_audit_job", queue="long", timeout=20000,
                   project=project, model=model, batch=int(batch))
    return {"queued": True}


def _faithfulness_audit_job(project, model="gpt-4o", batch=8):
    from collections import Counter
    import re
    headers = _headers()
    batch = int(batch)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "chapter", "model", "source_text", "draft_text", "final_text"],
                          order_by="seq asc", limit_page_length=0)
    # the translator's own passages only, in reading order (AI-omission fills excluded)
    human = [s for s in segs if (s.get("model") or "") != "AI-omission"]

    def mn(s):
        return (s.get("final_text") or s.get("draft_text") or "").strip()

    # English that repeats across rows = list/cover splitting artifact, not a
    # reliable per-row source — exclude so we don't "miss" the sibling list items.
    en_counts = Counter((s.get("source_text") or "").strip() for s in human if (s.get("source_text") or "").strip())

    def window(i):
        # current row's Mongolian plus its immediate neighbours in the same chapter
        ch = human[i].get("chapter")
        parts = [mn(human[j]) for j in (i - 1, i, i + 1)
                 if 0 <= j < len(human) and human[j].get("chapter") == ch and mn(human[j])]
        return " ".join(parts)

    def is_datacell(mn_text, en_text):
        # numbers / dosage tables / split cells — not prose worth a completeness check
        letters = re.sub(r"[^A-Za-zА-Яа-яӨөҮүЁё]", "", mn_text)
        if len(letters) < 8:
            return True
        if re.search(r"\bLot\s*\d|\bmg\s*/\s*kg|\bkg\s*/\s*day|\bg\s*/\s*l\b", en_text or "", re.I):
            return True
        d = sum(c.isdigit() for c in en_text)
        return bool(en_text) and d / len(en_text) > 0.20

    items, skipped_data = [], 0  # items: (segment, english, mn_window)
    for i, s in enumerate(human):
        en = (s.get("source_text") or "").strip()
        if not en or not mn(s) or en_counts[en] > 1:
            continue
        if is_datacell(mn(s), en):
            skipped_data += 1
            continue
        items.append((s, en, window(i)))

    # clear previous ⚠ flags (idempotent) — leave human reviewer notes untouched
    for n in frappe.get_all("Translation Segment",
                            filters={"project": project, "reviewer_comment": ["like", "⚠%"]}, pluck="name"):
        frappe.db.set_value("Translation Segment", n, "reviewer_comment", "")
    frappe.db.commit()

    SYS = ("You are a meticulous bilingual editor checking a Mongolian book translation for "
           "COMPLETENESS against its English source.")
    JSON_SHAPE = ('\nReturn JSON {"items":[{"id":<int>,"complete":<true|false>,'
                  '"missing":"<short Mongolian phrase naming exactly what is absent; empty when complete>"}]} '
                  "with exactly one element per id.")
    instr_a = (
        "Each item gives one English sentence (EN) and the surrounding Mongolian passage (MN) that "
        "should contain its content. The Mongolian may use different wording or order, or split the "
        "content across sentences — judge by MEANING over the WHOLE MN passage, not word-for-word, "
        "and treat content as present if it appears anywhere in the MN passage. Mongolian is "
        "naturally more compact, so DO NOT flag brevity, style, word order or dropped filler. Flag "
        "ONLY when a specific fact, number, named entity or claim in EN has NO counterpart anywhere "
        "in the MN passage." + JSON_SHAPE)
    # Second, stricter pass — biased toward dismissing to remove false alarms.
    instr_b = (
        "You are RE-CHECKING suspected omissions to eliminate false alarms. Each item gives an "
        "English sentence (EN) and the FULL surrounding Mongolian passage (MN). Search the ENTIRE "
        "MN passage carefully. If every substantive element of EN (facts, numbers, names, claims) "
        "appears somewhere in MN in ANY wording, paraphrase or word order, answer complete=true. "
        "Answer complete=false ONLY if a specific element is genuinely and wholly absent, and name "
        "it. When in doubt, answer complete=true." + JSON_SHAPE)

    usage = {"in": 0, "out": 0}

    def run_pass(triples, instruction):
        """Return the subset still judged incomplete: list of (segment, en, win, missing)."""
        out = []
        n = len(triples)
        for b in range(0, n, batch):
            chunk = triples[b:b + batch]
            lines = ["[%d]\nEN: %s\nMN passage: %s" % (k, en, win) for k, (s, en, win) in enumerate(chunk)]
            payload = {"model": model, "temperature": 0.0, "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": SYS},
                                    {"role": "user", "content": instruction + "\n\nItems:\n" + "\n\n".join(lines)}]}
            try:
                resp = make_post_request("https://api.openai.com/v1/chat/completions",
                                         headers=headers, data=json.dumps(payload))
                data = json.loads(resp["choices"][0]["message"]["content"])
                u = resp.get("usage", {}); usage["in"] += u.get("prompt_tokens", 0); usage["out"] += u.get("completion_tokens", 0)
                by_id = {int(x["id"]): x for x in data.get("items", [])}
            except Exception:
                by_id = {}
            for k, (s, en, win) in enumerate(chunk):
                r = by_id.get(k)
                if r and not r.get("complete", True) and (r.get("missing") or "").strip():
                    out.append((s, en, win, r["missing"].strip()))
            frappe.publish_realtime("lac_translation_progress", {"project": project, "done": min(b + batch, n), "total": n})
        return out

    total = len(items)
    candidates = run_pass(items, instr_a)
    verified = run_pass([(s, en, win) for (s, en, win, _m) in candidates], instr_b)

    flagged = []
    for (s, en, win, miss) in verified:
        frappe.db.set_value("Translation Segment", s["name"], "reviewer_comment", ("⚠ " + miss)[:500])
        flagged.append({"seq": s["seq"], "chapter": s.get("chapter") or "", "missing": miss, "en": en, "mn": mn(s)})
    frappe.db.commit()

    from frappe.utils.file_manager import save_file
    rows = "".join("<tr><td>%s</td><td>%s</td><td><b>%s</b></td><td>%s</td><td>%s</td></tr>" % (
        f["seq"], _esc(f["chapter"]), _esc(f["missing"]), _esc((f["en"] or "")[:400]), _esc((f["mn"] or "")[:400]))
        for f in flagged)
    skipped_dupe = sum(1 for s in human if en_counts.get((s.get("source_text") or "").strip(), 0) > 1)
    html = ("<html><meta charset='utf-8'><body style='font-family:system-ui;font-size:14px'>"
            "<h2>Faithfulness audit — %s</h2><p>Checked %d translator passages (context-aware, "
            "two-pass; skipped %d duplicated-English list/cover rows and %d number/table rows). "
            "%d passed the first pass; <b>%d</b> confirmed by the stricter second pass as possibly "
            "dropping content. Each confirmed one is marked with a ⚠ note on its sentence in the portal.</p>"
            "<table border=1 cellpadding=6 style='border-collapse:collapse'>"
            "<tr><th>Seq</th><th>Chapter</th><th>Possibly dropped</th><th>English</th><th>Mongolian</th></tr>%s"
            "</table></body></html>") % (project, total, skipped_dupe, skipped_data,
                                         len(candidates), len(flagged), rows)
    save_file("faithfulness_audit_%s.html" % project, html.encode("utf-8"), "Translation Project", project, is_private=1)
    frappe.db.commit()

    cost = round(usage["in"] / 1e6 * 2.5 + usage["out"] / 1e6 * 10.0, 4)
    summary = {"checked": total, "skipped_duplicated_english": skipped_dupe, "skipped_data_rows": skipped_data,
               "first_pass_candidates": len(candidates), "flagged": len(flagged), "est_cost_usd": cost}
    print(json.dumps(summary, ensure_ascii=False))
    return summary


@frappe.whitelist()
def normalize_structure(project):
    """One-time structure cleanup so the reader groups the book cleanly:
    (1) forward-fill blank chapter labels from the nearest preceding titled
    segment in reading order, so every paragraph sits under its section; and
    (2) smooth single-segment chapter anomalies (a lone segment whose chapter
    differs from BOTH neighbours — usually an AI-fill placed at a chapter
    boundary — is relabelled to the surrounding chapter) so chapter ranges stop
    overlapping. Changes only the 'chapter' label — never the translation."""
    frappe.only_for("System Manager")
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "chapter"], order_by="seq asc, creation asc",
                          limit_page_length=0)
    # pass 1: forward-fill blanks
    cur, filled = "", 0
    for s in segs:
        ch = (s.get("chapter") or "").strip()
        if ch:
            cur = ch
        elif cur:
            s["chapter"] = cur
            frappe.db.set_value("Translation Segment", s["name"], "chapter", cur, update_modified=False)
            filled += 1
    # pass 2: force chapters to be contiguous in reading order. Walking in
    # reading order a chapter may only advance; a segment carrying a chapter we
    # have already passed is a stray (usually an AI-fill dropped at a chapter
    # boundary) and is relabelled to the current section. This removes
    # overlapping chapter ranges so the reader groups the book strictly in order.
    current, done, moved = "", set(), 0
    for s in segs:
        ch = (s.get("chapter") or "").strip()
        if not ch or ch == current:
            continue
        if ch in done:                       # points back to a finished section -> stray
            frappe.db.set_value("Translation Segment", s["name"], "chapter", current, update_modified=False)
            s["chapter"] = current
            moved += 1
        else:                                # a genuinely new section begins here
            if current:
                done.add(current)
            current = ch
    frappe.db.commit()
    return {"filled_blank_chapters": filled, "relabelled_stray_segments": moved}


# --------------------------------------------------------------------------
# PDF-faithful full build (Option B): translate EVERY English sentence of the
# source-of-truth PDF, in PDF order, into a NEW project. Uses the existing
# translation (aligned + filled) as a per-passage reference for terminology and
# style. Guarantees: complete, in PDF order, no overlaps. Mostly AI by nature
# (the human translation covered only part of the book).
# --------------------------------------------------------------------------

def _translate_en(model, headers, glossary, items, reference=""):
    """items: [{id, en}] -> ({id: mn}, usage). Faithful EN->MN, one per id."""
    lines = ["[%d] %s" % (it["id"], it["en"]) for it in items]
    instr = ("Translate EACH numbered English sentence into natural, publication-ready "
             "Mongolian. The English is the SOURCE OF TRUTH: translate it faithfully and "
             "completely — do not add, omit, merge or split. Return a JSON object "
             '{"items":[{"id":<int>,"mn":"<Mongolian>"}]} with EXACTLY one entry per input '
             "id, using the same ids.")
    if glossary:
        instr += "\n\nGlossary / approved terminology (use exactly, adapt case endings):\n" + glossary
    if reference:
        instr += ("\n\nExisting Mongolian translation of this same passage — reuse its wording "
                  "and terminology where it fits, for consistency:\n" + reference)
    payload = {"model": model, "temperature": 0.2, "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": "You are an expert English-to-Mongolian book translator. Output only valid JSON."},
                            {"role": "user", "content": instr + "\n\nSentences:\n" + "\n".join(lines)}]}
    resp = make_post_request("https://api.openai.com/v1/chat/completions",
                             headers=headers, data=json.dumps(payload))
    data = json.loads(resp["choices"][0]["message"]["content"])
    out = {}
    for x in data.get("items", []):
        try:
            out[int(x["id"])] = x.get("mn", "")
        except Exception:
            pass
    return out, resp.get("usage", {})


@frappe.whitelist()
def build_faithful(source_project, english_file, model="gpt-4o", chunk=12, title=None):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._build_faithful_job", queue="long", timeout=36000,
                   source_project=source_project, english_file=english_file, model=model,
                   chunk=int(chunk), title=title)
    return {"queued": True}


def _build_faithful_job(source_project, english_file, model="gpt-4o", chunk=12, title=None):
    headers = _headers()
    chunk = int(chunk)
    src = frappe.get_doc("Translation Project", source_project)
    glossary = _eff_glossary(src)
    en = _clean_en_sentences(english_file)
    en = [e for e in en
          if "....." not in e["text"] and not _EN_NUM_ROW.match(e["text"].strip())]
    SKIP_CH = {"TABLE OF CONTENTS:", "Index", "LITERATURE"}
    en = [e for e in en if e["chapter"] not in SKIP_CH]
    en_texts = [e["text"] for e in en]
    N = len(en_texts)
    if not N:
        return {"error": "no english"}

    # reference map: en_index -> existing Mongolian (aligned human + earlier AI fills)
    pref = {}
    for i, t in enumerate(en_texts):
        pref.setdefault(t[:40], i)
    ref = {}
    for s in frappe.get_all("Translation Segment", filters={"project": source_project},
                            fields=["source_text", "draft_text", "final_text"],
                            limit_page_length=0):
        mn = (s.get("final_text") or s.get("draft_text") or "").strip()
        if not mn:
            continue
        for sent in _split_sentences(s.get("source_text") or ""):
            j = pref.get(sent.strip()[:40])
            if j is not None:
                ref.setdefault(j, mn)

    if not title:
        title = (src.title or source_project) + " — Full (PDF-faithful)"
    proj = frappe.get_doc({"doctype": "Translation Project", "title": title,
                           "status": "In Progress", "source_language": "English",
                           "target_language": "Mongolian", "model": model,
                           "glossary": src.glossary or ""}).insert(ignore_permissions=True)

    seq = 0
    in_tok = out_tok = 0
    chap_mn = {}
    i = 0
    while i < N:
        block = list(range(i, min(i + chunk, N)))
        ch_en = en[block[0]]["chapter"]
        if ch_en and ch_en not in chap_mn:
            try:
                tt, u = _translate_en(model, headers, glossary, [{"id": 0, "en": ch_en}])
                in_tok += u.get("prompt_tokens", 0); out_tok += u.get("completion_tokens", 0)
                chap_mn[ch_en] = (tt.get(0) or ch_en).upper()[:130]
            except Exception:
                chap_mn[ch_en] = ch_en[:130]
        items = [{"id": k, "en": en_texts[k]} for k in block]
        reftxt = "\n".join(ref[k] for k in block if k in ref)
        try:
            tr, u = _translate_en(model, headers, glossary, items, reftxt)
        except Exception:
            tr, u = {}, {}
        in_tok += u.get("prompt_tokens", 0); out_tok += u.get("completion_tokens", 0)
        for k in block:
            mn = tr.get(k) or ref.get(k) or ""
            seq += 1
            frappe.get_doc({"doctype": "Translation Segment", "project": proj.name, "seq": seq,
                            "chapter": chap_mn.get(en[k]["chapter"], en[k]["chapter"]),
                            "status": "Pending", "source_text": en_texts[k],
                            "draft_text": mn, "final_text": mn, "model": model}).insert(ignore_permissions=True)
        frappe.db.commit()
        frappe.publish_realtime("lac_translation_progress",
                                {"project": proj.name, "done": seq, "total": N})
        i += chunk
    proj.db_set("total_segments", seq)
    frappe.db.commit()
    cost = round(in_tok / 1e6 * 2.5 + out_tok / 1e6 * 10.0, 4)
    summary = {"project": proj.name, "segments": seq, "english_sentences": N,
               "prompt_tokens": in_tok, "completion_tokens": out_tok, "est_cost_usd": cost}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary


@frappe.whitelist()
def export_docx_grouped(project):
    """Export a project as .docx, adding a heading whenever the chapter field
    changes (used by the PDF-faithful build, whose chapter field holds the
    translated chapter title)."""
    frappe.only_for("System Manager")
    proj = frappe.get_doc("Translation Project", project)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["seq", "chapter", "final_text", "draft_text"],
                          order_by="seq asc, creation asc", limit_page_length=0)
    from docx import Document
    doc = Document()
    doc.add_heading(proj.title or project, 0)
    cur = None
    for s in segs:
        ch = (s.get("chapter") or "").strip()
        if ch and ch != cur:
            doc.add_heading(ch, level=1)
            cur = ch
        t = (s.get("final_text") or s.get("draft_text") or "").strip()
        if t:
            doc.add_paragraph(t)
    buf = io.BytesIO(); doc.save(buf)
    from frappe.utils.file_manager import save_file
    fname = re.sub(r"[^\w\-]+", "_", (proj.title or project)) + "_MN.docx"
    f = save_file(fname, buf.getvalue(), "Translation Project", project, is_private=1)
    frappe.db.commit()
    return {"file_url": f.file_url}


# --------------------------------------------------------------------------
# Re-import a reviewer's marked-up docx cleanly: clear the previous imported
# review first so an updated file never piles duplicates on top. Idempotent.
# --------------------------------------------------------------------------

@frappe.whitelist()
def reimport_review(project, file_url):
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._reimport_review_job", queue="long", timeout=6000,
                   project=project, file_url=file_url)
    return {"queued": True}


def _reimport_review_job(project, file_url):
    # remove the previous imported review (suggestions + segment comments)
    for n in frappe.get_all("Translation Suggestion",
                            filters={"project": project, "origin": "Imported"}, pluck="name"):
        frappe.delete_doc("Translation Suggestion", n, ignore_permissions=True, force=True)
    seg_names = frappe.get_all("Translation Segment", filters={"project": project}, pluck="name")
    if seg_names:
        for n in frappe.get_all("Comment", filters={
                "reference_doctype": "Translation Segment",
                "reference_name": ["in", seg_names], "comment_type": "Comment"}, pluck="name"):
            frappe.delete_doc("Comment", n, ignore_permissions=True, force=True)
    frappe.db.commit()
    res = import_revisions(project, file_url)
    frappe.db.commit()
    print(json.dumps(res, ensure_ascii=False))
    return res


@frappe.whitelist()
def import_review_doc(file_url, staging_project="TRP-00003", target_project="TRP-00004"):
    """One step for a fresh export of the redactor's Google Doc (Download →
    Microsoft Word .docx, which carries her suggestions as tracked changes and
    her comments): refresh the staging book from the doc, then re-extract the
    edits + comments onto the working book. Idempotent — safe to re-run whenever
    she makes more changes."""
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._import_review_doc_job", queue="long", timeout=6000,
                   file_url=file_url, staging_project=staging_project, target_project=target_project)
    return {"queued": True}


def _import_review_doc_job(file_url, staging_project, target_project):
    _reimport_review_job(staging_project, file_url)          # refresh staging from the fresh export
    res = bridge_review(staging_project, target_project)      # place edits + comments onto the working book
    frappe.db.commit()
    frappe.publish_realtime("lac_translation_progress",
                            {"project": target_project, "done": 1, "total": 1})
    print(json.dumps(res, ensure_ascii=False))
    return res


@frappe.whitelist()
def clear_review(project):
    """Remove all imported review (suggestions + segment comments) from a
    project, without importing anything. For undoing a review imported onto the
    wrong book."""
    frappe.only_for("System Manager")
    ns = frappe.get_all("Translation Suggestion",
                        filters={"project": project, "origin": "Imported"}, pluck="name")
    for n in ns:
        frappe.delete_doc("Translation Suggestion", n, ignore_permissions=True, force=True)
    seg = frappe.get_all("Translation Segment", filters={"project": project}, pluck="name")
    nc = 0
    if seg:
        for n in frappe.get_all("Comment", filters={
                "reference_doctype": "Translation Segment",
                "reference_name": ["in", seg], "comment_type": "Comment"}, pluck="name"):
            frappe.delete_doc("Comment", n, ignore_permissions=True, force=True)
            nc += 1
    frappe.db.commit()
    return {"cleared_suggestions": len(ns), "cleared_comments": nc}


# --------------------------------------------------------------------------
# finalize_review: one action to (1) clear any review wrongly imported onto the
# PDF-faithful book, (2) import the reviewer's docx onto the human translation,
# and (3) bridge the reviewer's COMMENTS onto the PDF-faithful book by matching
# English position. Comments carry over; detailed sentence edits stay on the
# human copy (they only line up there).
# --------------------------------------------------------------------------

def _rnorm(t):
    return re.sub(r"\s+", " ", (t or "")).strip()


def _clear_review_inner(project):
    ns = frappe.get_all("Translation Suggestion",
                        filters={"project": project, "origin": "Imported"}, pluck="name")
    for n in ns:
        frappe.delete_doc("Translation Suggestion", n, ignore_permissions=True, force=True)
    seg = frappe.get_all("Translation Segment", filters={"project": project}, pluck="name")
    nc = 0
    if seg:
        for n in frappe.get_all("Comment", filters={
                "reference_doctype": "Translation Segment",
                "reference_name": ["in", seg], "comment_type": "Comment"}, pluck="name"):
            frappe.delete_doc("Comment", n, ignore_permissions=True, force=True)
            nc += 1
    return len(ns), nc


def _bridge_comments(human_project, final_project):
    """Copy reviewer comments from the human project's segments onto the
    PDF-faithful project's segments, matched by English sentence."""
    fmap = {}
    for s in frappe.get_all("Translation Segment", filters={"project": final_project},
                            fields=["name", "source_text"], limit_page_length=0):
        k = _rnorm(s.get("source_text"))[:60]
        if k:
            fmap.setdefault(k, s["name"])
    hsegs = {s["name"]: s.get("source_text") for s in frappe.get_all(
             "Translation Segment", filters={"project": human_project},
             fields=["name", "source_text"], limit_page_length=0)}
    hnames = list(hsegs.keys())
    n = 0
    if not hnames:
        return 0
    for c in frappe.get_all("Comment", filters={
            "reference_doctype": "Translation Segment", "reference_name": ["in", hnames],
            "comment_type": "Comment"}, fields=["reference_name", "content"], limit_page_length=0):
        parts = _split_sentences(hsegs.get(c["reference_name"]) or "")
        key = _rnorm(parts[0])[:60] if parts else ""
        target = fmap.get(key)
        if target:
            frappe.get_doc({"doctype": "Comment", "comment_type": "Comment",
                            "reference_doctype": "Translation Segment", "reference_name": target,
                            "content": "[reviewer] " + (c["content"] or "")}).insert(ignore_permissions=True)
            n += 1
    return n


@frappe.whitelist()
def finalize_review(human_project, review_file_url, final_project=None):
    frappe.only_for("System Manager")
    title = frappe.db.get_value("Translation Project", human_project, "title") or ""
    if "pdf-faithful" in title.lower():
        frappe.throw("Select the human translation (not the PDF-faithful book) as the current book, then import the review.")
    frappe.enqueue("lac_translation.api._finalize_review_job", queue="long", timeout=9000,
                   human_project=human_project, review_file_url=review_file_url,
                   final_project=final_project)
    return {"queued": True}


def _finalize_review_job(human_project, review_file_url, final_project=None):
    if not final_project:
        cand = frappe.get_all("Translation Project", filters={"title": ["like", "%PDF-faithful%"]},
                              fields=["name"], order_by="creation desc", limit_page_length=1)
        final_project = cand[0]["name"] if cand else None
    cleared_final = _clear_review_inner(final_project) if final_project else (0, 0)
    _clear_review_inner(human_project)
    frappe.db.commit()
    res = import_revisions(human_project, review_file_url)
    bridged = _bridge_comments(human_project, final_project) if final_project else 0
    frappe.db.commit()
    out = {"human_project": human_project, "final_project": final_project,
           "cleared_from_final": cleared_final, "imported_to_human": res,
           "comments_bridged_to_final": bridged}
    print(json.dumps(out, ensure_ascii=False))
    return out


# --------------------------------------------------------------------------
# Reviewer access: create a limited "Translation Reviewer" role (portal +
# review permissions only, no admin), grant the portal page, and create/enable
# a reviewer user. Lets a redactor review live in the portal — no docx round-trip.
# --------------------------------------------------------------------------

@frappe.whitelist()
def setup_reviewer(email, first_name="Reviewer", password=None):
    frappe.only_for("System Manager")
    from frappe.permissions import add_permission, update_permission_property
    role = "Translation Reviewer"
    if not frappe.db.exists("Role", role):
        frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)

    perms = {
        "Translation Project": ["read"],
        "Translation Segment": ["read", "write"],
        "Translation Suggestion": ["read", "write", "create", "delete"],
        # reviewer curates terminology (their core concern) — but apply_term
        # (AI re-generation across the book) stays System-Manager-only.
        "Translation Term": ["read", "write", "create"],
        "Comment": ["read", "create"],
        # so commenting/editing doesn't fail on Frappe's notification writes
        "Notification Log": ["read", "write", "create", "delete"],
        "ToDo": ["read", "write", "create", "delete"],
    }
    for dt, ptypes in perms.items():
        try:
            add_permission(dt, role, 0)
        except Exception:
            pass
        for p in ptypes:
            try:
                update_permission_property(dt, role, 0, p, 1)
            except Exception:
                pass

    # grant the portal page to the role
    pg = frappe.get_doc("Page", "translation-portal")
    if not any((r.role == role) for r in (pg.roles or [])):
        pg.append("roles", {"role": role})
        pg.save(ignore_permissions=True)

    # create / enable the reviewer user
    if frappe.db.exists("User", email):
        u = frappe.get_doc("User", email)
        u.enabled = 1
    else:
        u = frappe.get_doc({"doctype": "User", "email": email, "first_name": first_name,
                            "send_welcome_email": 0, "user_type": "System User"}).insert(ignore_permissions=True)
    # Reviewer gets EXACTLY this role and nothing else, so their access is
    # limited to the translation portal and its data — no other ERP module.
    # Clear any Role Profile first: a profile enforces roles and would override
    # our assignment on save.
    u.role_profile_name = ""
    if u.meta.has_field("role_profiles"):
        u.set("role_profiles", [])
    u.set("roles", [])
    u.append("roles", {"role": role})
    u.user_type = "System User"
    u.enabled = 1
    u.save(ignore_permissions=True)
    if password:
        from frappe.utils.password import update_password
        update_password(email, password)
    frappe.db.commit()
    return {"role": role, "user": email, "portal": "/app/translation-portal"}


@frappe.whitelist()
def fill_empties(project, model="gpt-4o"):
    """Translate any segments left with an empty Mongolian (e.g. the few the
    faithful build returned blank), so the book is 100% complete."""
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._fill_empties_job", queue="long", timeout=4000,
                   project=project, model=model)
    return {"queued": True}


def _fill_empties_job(project, model="gpt-4o"):
    headers = _headers()
    proj = frappe.get_doc("Translation Project", project)
    glossary = _eff_glossary(proj)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "source_text", "draft_text", "final_text"],
                          order_by="seq asc", limit_page_length=0)
    empties = [s for s in segs
               if not (s.get("final_text") or s.get("draft_text") or "").strip()
               and (s.get("source_text") or "").strip()]
    n = 0
    for i in range(0, len(empties), 12):
        chunk = empties[i:i + 12]
        items = [{"id": j, "en": chunk[j]["source_text"]} for j in range(len(chunk))]
        try:
            tr, _u = _translate_en(model, headers, glossary, items)
        except Exception:
            tr = {}
        for j in range(len(chunk)):
            mn = (tr.get(j) or "").strip()
            if mn:
                frappe.db.set_value("Translation Segment", chunk[j]["name"],
                                    {"draft_text": mn, "final_text": mn}, update_modified=False)
                n += 1
        frappe.db.commit()
    return {"filled": n, "of": len(empties)}


@frappe.whitelist()
def fix_chapter_labels(project, model="gpt-4o"):
    """Give every chapter a single Mongolian heading: the faithful build left
    the first few sentences of each chapter tagged with the English title (then
    the Mongolian title on the rest), which prints a double heading. Merge the
    English head into the following Mongolian chapter title (or translate it if
    there is none)."""
    frappe.only_for("System Manager")
    frappe.enqueue("lac_translation.api._fix_chapter_labels_job", queue="long",
                   timeout=4000, project=project, model=model)
    return {"queued": True}


def _fix_chapter_labels_job(project, model="gpt-4o"):
    headers = _headers()
    proj = frappe.get_doc("Translation Project", project)
    glossary = _eff_glossary(proj)
    segs = frappe.get_all("Translation Segment", filters={"project": project},
                          fields=["name", "seq", "chapter"],
                          order_by="seq asc, creation asc", limit_page_length=0)
    runs = []
    for s in segs:
        c = s.get("chapter") or ""
        if not runs or runs[-1]["ch"] != c:
            runs.append({"ch": c, "items": [s["name"]]})
        else:
            runs[-1]["items"].append(s["name"])

    def is_latin(t):
        return len(re.findall(r"[A-Za-z]", t or "")) > len(re.findall(r"[Ѐ-ӿ]", t or ""))

    cache = {}
    updates = []
    for idx, r in enumerate(runs):
        ch = r["ch"]
        if not ch or not is_latin(ch):
            continue
        target = None
        if idx + 1 < len(runs) and runs[idx + 1]["ch"].strip() and not is_latin(runs[idx + 1]["ch"]):
            target = runs[idx + 1]["ch"]
        else:
            if ch not in cache:
                try:
                    tt, _u = _translate_en(model, headers, glossary, [{"id": 0, "en": ch}])
                    cache[ch] = (tt.get(0) or ch).upper()[:130]
                except Exception:
                    cache[ch] = ch
            target = cache[ch]
        if target and target != ch:
            for nm in r["items"]:
                updates.append((nm, target))
    for i, (nm, tc) in enumerate(updates):
        frappe.db.set_value("Translation Segment", nm, {"chapter": tc}, update_modified=False)
        if i % 200 == 0:
            frappe.db.commit()
    frappe.db.commit()
    return {"relabeled_segments": len(updates)}
