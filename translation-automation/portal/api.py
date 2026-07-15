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


@frappe.whitelist()
def add_term(project, source_term, target_term, note=""):
    """Create or update one approved term in the book's termbase."""
    frappe.only_for("System Manager")
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
