# Translation Portal (full-screen ERP app)

A dedicated, full-screen review portal (not the ERP forms) served inside your
ERPNext at **/app/translation-portal**. It reads/writes your live
`Translation Project` / `Translation Segment` data.

Design matches the approved prototype: left rail = the book grouped by
**chapter** with status dots + filters + search; centre = one segment as cards
(**Original / Translator draft / AI suggestion w/ word-diff / faithful
alternative / editable Final / comments**); top = book picker, live progress,
**Glossary**, **Reading view**, **Export**, theme toggle. Keyboard: **J/K** move,
**A** accept, **E** edit.

It is a **Frappe Page** in a tiny app (`lac_translation`) — one-time install,
no Python logic, just the page. The AI pass is still `server/bench_run.py`.

## Install (on the server)

```bash
cd /home/frappe/frappe-bench

# 1. scaffold a tiny app (answer the prompts; title can be "Lac Translation")
bench new-app lac_translation

# 2. drop the portal page into it (from this repo, cloned at /tmp/lac-trans)
DEST=apps/lac_translation/lac_translation/lac_translation/page
mkdir -p "$DEST"
cp -r /tmp/lac-trans/translation-automation/portal/page/translation_portal "$DEST"/

# 3. install on the site, sync the page, build assets
bench --site site1.local install-app lac_translation
bench --site site1.local migrate
bench build --app lac_translation
bench --site site1.local clear-cache
```

Then open **https://erp.lac.mn/app/translation-portal** (hard-refresh).

### Updating later
After `cd /tmp/lac-trans && git pull`, re-copy the folder (step 2) then:
```bash
bench build --app lac_translation && bench --site site1.local clear-cache
```
(Tip: instead of copying, you can symlink `translation_portal` to the repo path
so `git pull` + `bench build` is enough.)

## Notes
- Requires the DocTypes + custom fields already created (Translation Project /
  Segment, incl. `chapter`, `ai_alternative`, `ai_rationale`, `reviewer_comment`).
- Comments use the standard Frappe `Comment` timeline (also visible on the
  segment form).
- Permissions: the page is limited to **System Manager** (edit
  `translation_portal.json` roles to widen).
