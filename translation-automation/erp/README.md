# ERP-side configuration (no app deploy)

All of this lives as **DocType / Custom Field / Client Script records in the
ERPNext DB** (module *LAC Custom*), so it installs without deploying app code.
The files here are the source of truth for reinstalling/porting.

## DocTypes
- **Translation Project** (`TRP-#####`) — one per book.
  Fields: `title`, `status` (Draft/In Progress/Review/Completed),
  `source_language`, `target_language`, `model`, `glossary` (Long Text),
  `total_segments`, `accepted_segments`, `prompt_tokens`, `completion_tokens`,
  `cost`.
  Custom fields: `sb_book` (Section Break "Book Review"), `book_view_html` (HTML).
- **Translation Segment** (`TRS-######`) — one per sentence.
  Fields: `project` (Link), `seq` (Int), `status`
  (Pending/Suggested/Accepted/Edited/Rejected), `source_text` (Original EN),
  `draft_text` (Translator draft), `ai_suggestion` (recommended),
  `final_text` (editable), `model`, `prompt_tokens`, `completion_tokens`, `cost`.
  Custom fields: `changes_html` (HTML diff), `ai_alternative` (Long Text),
  `ai_rationale` (Small Text notes), `reviewer_comment` (Small Text).

## Client Scripts
- `client_script_segment.js` → record **"Translation Segment Review UX"**
  (Form): word-diff of draft vs AI, buttons **Accept AI / Use alternative /
  Use draft / Back to book**, and auto-marks manual edits as *Edited*.
- `client_script_project.js` → record **"Translation Project Book View"**
  (Form): progress bar + status legend, a clickable **Original / Draft / Final**
  table, and buttons **Refresh view / Accept all unchanged / Reading view /
  Export final**.

## Commenting
Per-segment discussion uses Frappe's built-in document timeline comments (the
Comments box on every Translation Segment), plus the structured
`reviewer_comment` field.

## Reviewer flow
1. Open a **Translation Project** → the Book View shows progress + all segments.
2. Click a row → the **Translation Segment** opens with the diff and buttons.
3. **Accept AI** / **Use alternative** / edit **Final** manually (→ *Edited*) /
   add a **Reviewer Comment**.
4. Back on the project: **Accept all unchanged** for the no-change ones,
   **Reading view** to read the book, **Export final** to download.

## Notes / next
- The book table currently renders all segments client-side; for very large
  books we'll paginate or add a chapter field.
- To port to another site, recreate these Custom Field + Client Script records
  (or package them as fixtures in a small app later).
