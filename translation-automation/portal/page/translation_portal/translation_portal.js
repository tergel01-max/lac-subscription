frappe.pages['translation-portal'].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: 'Translation Portal', single_column: true });

  const STATUS = ['Pending', 'Suggested', 'Accepted', 'Edited', 'Rejected', 'Locked'];
  const DONE = s => s === 'Accepted' || s === 'Edited' || s === 'Locked';
  const S = { projects: [], project: null, glossary: '', segs: [], terms: [], suggestions: [], cur: null, mode: 'segments', filter: 'All', q: '', bilingual: false };
  const esc = s => (s || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const color = st => 'var(--s-' + st + ')';

  function diff(a, b) {
    const ow = (a || '').split(/\s+/).filter(Boolean), nw = (b || '').split(/\s+/).filter(Boolean);
    const m = ow.length, k = nw.length, dp = Array.from({ length: m + 1 }, () => new Array(k + 1).fill(0));
    for (let i = m - 1; i >= 0; i--) for (let j = k - 1; j >= 0; j--) dp[i][j] = ow[i] === nw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    let i = 0, j = 0, o = [];
    while (i < m && j < k) { if (ow[i] === nw[j]) { o.push(esc(ow[i])); i++; j++; } else if (dp[i + 1][j] >= dp[i][j + 1]) { o.push('<del>' + esc(ow[i]) + '</del>'); i++; } else { o.push('<ins>' + esc(nw[j]) + '</ins>'); j++; } }
    while (i < m) { o.push('<del>' + esc(ow[i]) + '</del>'); i++; }
    while (j < k) { o.push('<ins>' + esc(nw[j]) + '</ins>'); j++; }
    return o.join(' ');
  }

  const shell = `
  <div class="tp" id="tpRoot">
    <div class="tp-top">
      <select class="tp-sel" id="tpBook"></select>
      <div class="tp-modes"><button id="tpMSeg" aria-pressed="true">✍ Segments</button><button id="tpMRead" aria-pressed="false">📖 Reading</button></div>
      <div class="tp-spacer"></div>
      <div class="tp-prog"><div class="row"><span>Finalized</span><b id="tpProgLabel">—</b></div><div class="tp-track"><div class="tp-fill" id="tpProgFill" style="width:0"></div></div></div>
      <div class="tp-acts">
        <button class="tp-btn ghost" id="tpNext">⇥ Next</button>
        <button class="tp-btn ghost" id="tpQA">✓ QA</button>
        <button class="tp-btn ghost" id="tpGen">✨ AI</button>
        <div class="tp-menuwrap">
          <button class="tp-btn ghost" id="tpMore">⋯ More</button>
          <div class="tp-menu" id="tpMenu">
            <button class="tp-mi" id="tpTerms">📕 Terms</button>
            <button class="tp-mi" id="tpGloss">📑 Glossary</button>
            <div class="tp-mi-sep"></div>
            <button class="tp-mi" id="tpImport">＋ Import book (EN + MN)</button>
            <button class="tp-mi" id="tpImportReviewed">⇄ Import reviewed translation</button>
            <button class="tp-mi" id="tpImportRev">⇄ Attach review to current book</button>
            <button class="tp-mi" id="tpRealign">🎯 Re-align source (EN↔MN)</button>
            <div class="tp-mi-sep"></div>
            <button class="tp-mi" id="tpAudit">🔎 Find missing passages (audit)</button>
            <button class="tp-mi" id="tpComplete">✚ Complete book — fill missing</button>
            <button class="tp-mi" id="tpRevert">↩ Remove AI-filled passages</button>
            <div class="tp-mi-sep"></div>
            <button class="tp-mi" id="tpTxt">⬇ Export .txt</button>
            <button class="tp-mi" id="tpDocx">📄 Export .docx</button>
          </div>
        </div>
        <button class="tp-btn ghost tp-icon" id="tpTheme" title="Toggle theme">◐</button>
      </div>
    </div>
    <div class="tp-body">
      <div class="seg-layout" id="tpSegLayout">
        <aside class="tp-rail">
          <div class="tp-railhead">
            <div class="tp-chips" id="tpChips"></div>
            <div class="tp-search"><svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="7" cy="7" r="4.5"/><path d="M11 11l3 3"/></svg><input id="tpSearch" placeholder="Search source or Mongolian…"></div>
          </div>
          <div class="tp-list" id="tpList"></div>
        </aside>
        <section class="tp-work"><div class="tp-inner" id="tpWork"></div></section>
      </div>
      <div class="read-layout" id="tpReadLayout" style="display:none">
        <div class="tp-readerpane" id="tpReaderPane"><div class="tp-readerdoc">
          <div class="tp-hint">
            <span>Click a sentence to open its panel and edit — your saved change shows here as <ins>green</ins>/<del>red</del>.</span>
            <span id="tpChangeInfo" style="color:var(--s-Edited);font-weight:700;margin-left:auto"></span>
            <button class="tp-btn ghost tp-icon" id="tpPrevChg" style="width:28px;height:28px;font-size:13px" title="Previous change">◀</button>
            <button class="tp-btn ghost tp-icon" id="tpNextChg" style="width:28px;height:28px;font-size:13px" title="Next change">▶</button>
            <button class="tp-btn ghost" id="tpResume" style="padding:4px 10px;font-size:12px" title="Jump back to where you left off">⤶ Resume</button>
            <button class="tp-btn ghost" id="tpPanel" style="padding:4px 10px;font-size:12px" title="Open the side panel (English, comments, suggestions) for the current sentence">☰ Panel</button>
            <button class="tp-btn ghost" id="tpBiToggle" style="padding:4px 10px;font-size:12px">Show English</button>
          </div>
          <div id="tpReaderBody"></div>
        </div></div>
        <aside class="tp-drawer" id="tpDrawer"><div class="tp-drawerinner" id="tpDrawerInner"></div></aside>
      </div>
    </div>
  </div>`;
  const $body = $(page.body); $body.html(shell);
  const bodyEl = $body[0];
  const root = bodyEl.querySelector('#tpRoot');
  const $id = id => bodyEl.querySelector('#' + id);
  if ((document.documentElement.getAttribute('data-theme') || '') === 'dark' || document.body.classList.contains('dark')) root.classList.add('tp-dark');

  const curSeg = () => S.segs.find(x => x.name === S.cur);
  function pill(st) { return `<span class="tp-pill" style="background:color-mix(in srgb,${color(st)} 16%,transparent);color:${color(st)}"><span style="width:7px;height:7px;border-radius:99px;background:${color(st)}"></span>${st}</span>`; }

  // ---- data ----
  function loadProjects() {
    return frappe.db.get_list('Translation Project', { fields: ['name', 'title', 'glossary'], limit: 0, order_by: 'creation desc' })
      .then(r => {
        S.projects = (r || []).filter(p => !/^\s*\[archived\]/i.test(p.title || ''));
        $id('tpBook').innerHTML = S.projects.map(p => `<option value="${p.name}">${esc(p.title || p.name)}</option>`).join('') || '<option>No projects</option>';
        if (S.projects.length) { S.project = S.projects[0].name; S.glossary = S.projects[0].glossary || ''; return loadSegs(); }
      });
  }
  function loadTerms() {
    return frappe.db.get_list('Translation Term', { filters: { project: S.project }, fields: ['name', 'source_term', 'target_term', 'note'], limit: 0 })
      .then(r => { S.terms = r || []; }).catch(() => { S.terms = []; });
  }
  function loadSuggestions() {
    return frappe.db.get_list('Translation Suggestion', { filters: { project: S.project, status: 'Open' }, fields: ['name', 'segment', 'origin', 'author', 'suggested_text', 'note'], limit: 0 })
      .then(r => { S.suggestions = r || []; }).catch(() => { S.suggestions = []; });
  }
  function loadSegs() {
    return loadTerms().then(loadSuggestions).then(() => frappe.db.get_list('Translation Segment', {
      filters: { project: S.project },
      fields: ['name', 'seq', 'chapter', 'status', 'source_text', 'draft_text', 'ai_suggestion', 'ai_alternative', 'ai_rationale', 'final_text', 'reviewer_comment'],
      order_by: 'seq asc, creation asc', limit: 0,
    })).then(r => { S.segs = r || []; if (!curSeg()) S.cur = S.segs.length ? S.segs[0].name : null; renderAll(); });
  }

  // ---- shared editor ----
  // NOTE: all editor elements use CLASSES (.tp-final, .tp-cmts, .tp-alt …) not
  // IDs, because this markup renders into two panes at once (segments work-pane
  // and reading drawer). Duplicate IDs made saves target the wrong pane/segment.
  const enCardHTML = s => `<div class="tp-card en"><div class="lbl"><span class="name">Original · English</span></div><div class="body">${esc(s.source_text)}</div></div>`;
  const commentsCardHTML = `<div class="tp-card comments"><div class="lbl"><span class="name">Comments</span></div><div class="body tp-cmts"><div style="color:var(--faint);font-size:13px">Loading…</div><form class="cmt-form tp-cmtform"><input class="tp-cmtinput" placeholder="Add a note for the team…"><button class="tp-btn" type="submit">Post</button></form></div></div>`;

  function editorHTML(s) {
    const isAdmin = frappe.user.has_role('System Manager');
    const cur = s.final_text || s.ai_suggestion || s.draft_text || '';
    const sugs = (S.suggestions || []).filter(x => x.segment === s.name);

    // ---- Reviewer (redactor) view: read + suggest inline, no popups. Their
    // edit becomes a Translation Suggestion for the editor to accept. ----
    if (!isAdmin) {
      const mine = sugs.find(x => x.author === frappe.session.user && x.origin === 'Reviewer');
      const others = sugs.filter(x => x !== mine);
      const curCard = `<div class="tp-card"><div class="lbl"><span class="name">Current translation</span></div><div class="body">${esc(cur) || '<span class="nochange">— (no translation yet)</span>'}</div></div>`;
      // AI suggestion: v4 has no stored ai_suggestion, so offer an on-demand
      // "Ask AI" that returns an improved wording she can adopt into her edit.
      const aiEsc = (s.ai_suggestion || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
      const aiPrefill = s.ai_suggestion
        ? `${esc(s.ai_suggestion)}<div class="cta"><button class="tp-btn primary" data-act="useai" style="padding:3px 10px;font-size:12px">Use as my edit</button></div>`
        : '<span class="nochange">Tap “✨ Ask AI” for an improved wording you can adopt.</span>';
      const aiCard = `<div class="tp-card ai"><div class="lbl"><span class="name">AI suggestion</span><button class="tp-btn" data-act="askai" style="padding:3px 9px;font-size:12px">✨ Ask AI</button></div><div class="body tp-aibox" data-mn="${aiEsc}">${aiPrefill}</div></div>`;
      const suggestCard = `<div class="tp-card suggest"><div class="lbl"><span class="name">${mine ? '✎ Your suggested edit' : '✎ Suggest an edit'}</span><button class="tp-btn primary" data-act="savesug" style="padding:4px 12px;font-size:12px">${mine ? 'Update suggestion' : 'Save suggestion'}</button></div><div class="body" style="padding:0"><textarea class="tp-suggest" placeholder="Edit the Mongolian here — one click saves it as a suggestion for the editor.">${esc(mine ? mine.suggested_text : cur)}</textarea></div>${mine ? '<div class="notes"><span>Saved as a suggestion (shown green/red in the text). Edit above and press Update, or remove it below.</span><button class="tp-btn ghost" data-act="delsug" data-sug="' + mine.name + '" style="margin-left:auto;padding:2px 9px;font-size:12px">Remove</button></div>' : ''}</div>`;
      const othersHtml = others.map(su => `<div class="tp-card sug"><div class="lbl"><span class="name">Also suggested · ${esc(su.origin)}${su.author ? ' · ' + esc(su.author) : ''}</span></div><div class="body"><div class="diff">${diff(cur, su.suggested_text)}</div></div>${su.note ? `<div class="notes"><span>${esc(su.note)}</span></div>` : ''}</div>`).join('');
      return `${enCardHTML(s)}${curCard}${aiCard}${suggestCard}${othersHtml}${commentsCardHTML}`;
    }

    // ---- Editor / admin view: full controls ----
    const locked = s.status === 'Locked', finalized = s.status === 'Accepted' || s.status === 'Edited';
    let ai = '';
    if (s.ai_suggestion) {
      const changed = (s.ai_suggestion || '').trim() !== (s.draft_text || '').trim();
      let inner = `<div class="body"><div class="diff">${changed ? diff(s.draft_text, s.ai_suggestion) : '<span class="nochange">No change — AI kept the draft.</span>'}</div></div>`;
      if (s.ai_rationale) inner += `<div class="notes"><svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l2 4 4 .6-3 3 .7 4L8 14.8 4.3 16.7l.7-4-3-3 4-.6z"/></svg><span>${esc(s.ai_rationale)}</span></div>`;
      if (!locked) {
        inner += `<div class="cta"><button class="tp-btn primary" data-act="acceptAI">✔ Accept</button>`;
        if (s.ai_alternative) inner += `<button class="alt-toggle" data-act="toggleAlt">▾ Alternative</button>`;
        inner += `<button class="tp-btn" data-act="addterm">＋ Term</button><button class="tp-btn" data-act="regen" style="margin-left:auto">↻ Regenerate</button>`;
        inner += `</div>`;
        if (s.ai_alternative) inner += `<div class="alt tp-alt"><div class="body" style="border-top:1px solid var(--border)">${esc(s.ai_alternative)}</div><div class="cta"><button class="tp-btn" data-act="useAlt">Use alternative</button></div></div>`;
      }
      ai = `<div class="tp-card ai"><div class="lbl"><span class="name">AI suggestion</span></div>${inner}</div>`;
    } else {
      const gen = locked ? '' : `<div class="cta"><button class="tp-btn" data-act="regen">✨ Generate suggestion</button></div>`;
      ai = `<div class="tp-card ai"><div class="lbl"><span class="name">AI suggestion</span></div><div class="body"><span class="nochange">Not generated yet.</span></div>${gen}</div>`;
    }

    let doneBar = '';
    if (locked) doneBar = `<div class="tp-donebar locked"><span>🔒 Locked · editor signed off</span><button class="tp-btn ghost" data-act="unlock" style="padding:4px 11px;font-size:12px">Unlock</button></div>`;
    else if (finalized) doneBar = `<div class="tp-donebar"><span>✓ Finalized · ${s.status}</span><span style="display:flex;gap:6px"><button class="tp-btn ghost" data-act="undo" style="padding:4px 11px;font-size:12px">↺ Undo</button><button class="tp-btn" data-act="lock" style="padding:4px 11px;font-size:12px">🔒 Lock (sign off)</button></span></div>`;

    const draftBtn = locked ? '' : '<button class="tp-btn ghost" data-act="useDraft" style="padding:3px 9px;font-size:12px">Keep draft</button>';
    const saveBtn = locked ? '' : '<button class="tp-btn primary" data-act="saveFinal" style="padding:4px 12px;font-size:12px">Save</button>';
    const sugHtml = sugs.map(su => `<div class="tp-card sug"><div class="lbl"><span class="name">Suggested change · ${esc(su.origin)}${su.author ? ' · ' + esc(su.author) : ''}</span></div><div class="body"><div class="diff">${diff(s.final_text || s.draft_text || '', su.suggested_text)}</div></div>${su.note ? `<div class="notes"><span>${esc(su.note)}</span></div>` : ''}<div class="cta"><button class="tp-btn primary" data-act="acceptsug" data-sug="${su.name}">✔ Accept change</button><button class="tp-btn" data-act="rejectsug" data-sug="${su.name}">Reject</button></div></div>`).join('');
    return `
      ${doneBar}
      ${enCardHTML(s)}
      <div class="tp-card"><div class="lbl"><span class="name">Translator draft</span>${draftBtn}</div><div class="body">${esc(s.draft_text)}</div></div>
      ${ai}
      ${sugHtml}
      <div class="tp-card final"><div class="lbl"><span class="name">Final</span><span style="display:flex;gap:6px">${saveBtn}<button class="tp-btn" data-act="propose" style="padding:4px 10px;font-size:12px">✎ Propose</button></span></div><div class="body"><textarea class="tp-final" ${locked ? 'readonly' : ''} placeholder="Accept above or type the final Mongolian…">${esc(s.final_text)}</textarea></div></div>
      ${commentsCardHTML}`;
  }

  // ---- segments render ----
  function counts() { const c = { All: S.segs.length }; STATUS.forEach(k => c[k] = S.segs.filter(s => s.status === k).length); return c; }
  function renderChips() { const c = counts(); const order = ['All', 'Pending', 'Suggested', 'Accepted', 'Edited', 'Locked']; $id('tpChips').innerHTML = order.map(k => `<button class="tp-chip" data-f="${k}" aria-pressed="${S.filter === k}">${k} <span class="n">${c[k] || 0}</span></button>`).join(''); }
  function visible() { return S.segs.filter(s => { if (S.filter !== 'All' && s.status !== S.filter) return false; if (S.q) { const q = S.q.toLowerCase(); return ((s.source_text || '') + ' ' + (s.draft_text || '') + ' ' + (s.final_text || s.ai_suggestion || '')).toLowerCase().includes(q); } return true; }); }
  function sugCounts() { const c = {}; (S.suggestions || []).forEach(x => { c[x.segment] = (c[x.segment] || 0) + 1; }); return c; }
  function renderList() {
    const vis = visible(), groups = {}, order = [], sc = sugCounts();
    vis.forEach(s => { const ch = s.chapter || 'Book'; if (!groups[ch]) { groups[ch] = []; order.push(ch); } groups[ch].push(s); });
    let html = '';
    order.forEach(ch => {
      const done = groups[ch].filter(s => DONE(s.status)).length;
      html += `<div class="tp-chapter"><span>${esc(ch)}</span><span>${done}/${groups[ch].length}</span></div>`;
      html += groups[ch].map(s => `<div class="tp-row ${s.name === S.cur ? 'active' : ''}" data-name="${s.name}"><div class="tp-dot" style="background:${color(s.status)}"></div><div><div class="seq">§${s.seq} · ${s.status}${sc[s.name] ? ` · <span class="tp-sugbadge">✎${sc[s.name]}</span>` : ''}</div><div class="src">${esc(s.source_text)}</div><div class="mn">${esc(s.final_text || s.ai_suggestion || s.draft_text)}</div></div></div>`).join('');
    });
    $id('tpList').innerHTML = html || '<div style="padding:20px;color:var(--faint);font-size:13px">No segments match.</div>';
  }
  function renderWork() {
    const s = curSeg(); const w = $id('tpWork');
    if (!s) { w.innerHTML = '<div style="color:var(--faint);padding:40px 0">Select a segment.</div>'; return; }
    w.innerHTML = `<div class="tp-seghead"><h2>${esc(s.chapter || 'Book')} · Segment §${s.seq}</h2>${pill(s.status)}</div>` + editorHTML(s);
    loadComments(s.name, $id('tpWork'));
  }

  // pick the suggestion to show inline for a segment (the viewer's own live edit
  // wins; otherwise show whatever suggestion exists — e.g. the imported review)
  function sugFor(name) { const arr = (S.suggestions || []).filter(x => x.segment === name); return arr.find(x => x.origin === 'Reviewer' && x.author === frappe.session.user) || arr[0] || null; }

  // ---- reading render (A4-like pages so position is easy to remember) ----
  function renderReader() {
    const groups = {}, order = [], sc = sugCounts();
    S.segs.forEach(s => { const ch = s.chapter || 'Book'; if (!groups[ch]) { groups[ch] = []; order.push(ch); } groups[ch].push(s); });
    const open = $id('tpDrawer').classList.contains('open');
    // Flow the whole book into fixed-size "pages" (~one A4 of body text) so the
    // page number is a stable landmark she can come back to.
    const PAGE = 1700, pages = []; let cur = [], n = 0;
    const flush = () => { if (cur.length) { pages.push(cur); cur = []; n = 0; } };
    order.forEach(ch => {
      if (n > PAGE * 0.55) flush();
      cur.push({ t: 'chap', ch }); n += 160;
      groups[ch].forEach(s => {
        const txt = s.final_text || s.ai_suggestion || s.draft_text || '';
        cur.push({ t: 'sent', s, txt });
        n += (txt.length || 1) + (S.bilingual ? (s.source_text || '').length + 30 : 1);
        if (n >= PAGE) flush();
      });
    });
    flush();
    const total = pages.length || 1;
    const html = pages.map((pg, pi) => {
      const inner = pg.map(tok => {
        if (tok.t === 'chap') return `<div class="chap-title">${esc(tok.ch)}</div>`;
        const s = tok.s, sel = (open && s.name === S.cur) ? ' sel' : '';
        const cls = `rsent st-${s.status}${sc[s.name] ? ' has-sug' : ''}${sel}`;
        // show a pending suggestion inline as a red/green tracked change
        const sug = sugFor(s.name);
        const content = (sug && (sug.suggested_text || '').trim() && (sug.suggested_text || '').trim() !== (tok.txt || '').trim()) ? diff(tok.txt, sug.suggested_text) : esc(tok.txt);
        if (S.bilingual) return `<div class="rbi"><div class="en">${esc(s.source_text)}</div><div class="${cls}" data-name="${s.name}">${content}</div></div>`;
        return `<span class="${cls}" data-name="${s.name}">${content} </span>`;
      }).join('');
      return `<div class="tp-page" data-page="${pi + 1}"><div class="tp-pagebody${S.bilingual ? ' bi' : ''}">${inner}</div><div class="tp-pagenum">— ${pi + 1} / ${total} —</div></div>`;
    }).join('') || '<div style="color:var(--faint)">No segments.</div>';
    const pane = $id('tpReaderPane'), keep = pane ? pane.scrollTop : 0;
    $id('tpReaderBody').innerHTML = html;
    if (pane) { if (S.pendingRestore) { restoreScroll(); S.pendingRestore = false; } else pane.scrollTop = keep; }
  }
  function openDrawer(name) {
    S.cur = name; const s = curSeg(); if (!s) return;
    saveSeg();
    $id('tpDrawer').classList.add('open');
    $id('tpDrawerInner').innerHTML = `<div class="tp-drawerhead"><div class="tp-seghead" style="gap:8px"><h2>§${s.seq}</h2>${pill(s.status)}</div><button class="tp-btn ghost tp-icon" data-act="closeDrawer">✕</button></div>` + editorHTML(s);
    loadComments(s.name, $id('tpDrawerInner')); renderReader();
  }

  // ---- reading position memory (so she can find where she left off) ----
  function saveScroll() { const p = $id('tpReaderPane'); if (p && S.project) { try { localStorage.setItem('tpScroll_' + S.project, String(Math.round(p.scrollTop))); } catch (e) { } } }
  function saveSeg() { if (S.project && S.cur) { try { localStorage.setItem('tpSeg_' + S.project, S.cur); } catch (e) { } } }
  function restoreScroll() { const p = $id('tpReaderPane'); if (!p || !S.project) return; let v = 0; try { v = parseInt(localStorage.getItem('tpScroll_' + S.project) || '0', 10) || 0; } catch (e) { } p.scrollTop = v; }
  function scrollToSeg(name) { const el = $id('tpReaderBody').querySelector('.rsent[data-name="' + name + '"]'); if (el) { el.scrollIntoView({ block: 'center' }); el.classList.add('pulse'); setTimeout(() => el.classList.remove('pulse'), 1700); } }
  // sentences that carry a suggestion, in reading order — so she can step through her changes
  function changeNames() { const set = new Set((S.suggestions || []).map(x => x.segment)); return S.segs.filter(s => set.has(s.name)).map(s => s.name); }
  function updateChangeInfo() { const el = $id('tpChangeInfo'); if (!el) return; const n = changeNames().length; el.textContent = n ? ('✎ ' + n + ' change' + (n > 1 ? 's' : '')) : ''; }
  function gotoChange(dir) {
    const names = changeNames();
    if (!names.length) { frappe.show_alert({ message: 'No suggested changes yet.', indicator: 'blue' }); return; }
    let i = names.indexOf(S.cur);
    i = (i < 0) ? (dir > 0 ? 0 : names.length - 1) : (i + dir + names.length) % names.length;
    S.cur = names[i]; saveSeg(); scrollToSeg(names[i]);
  }

  function renderProgress() { const done = S.segs.filter(s => DONE(s.status)).length; const pct = S.segs.length ? Math.round(done * 100 / S.segs.length) : 0; $id('tpProgLabel').textContent = `${done} / ${S.segs.length} · ${pct}%`; $id('tpProgFill').style.width = pct + '%'; updateChangeInfo(); }
  function renderAll() { renderProgress(); if (S.mode === 'segments') { renderChips(); renderList(); renderWork(); } else { renderReader(); if ($id('tpDrawer').classList.contains('open')) openDrawer(S.cur); } }
  function setMode(m) { S.mode = m; $id('tpMSeg').setAttribute('aria-pressed', m === 'segments'); $id('tpMRead').setAttribute('aria-pressed', m === 'reading'); $id('tpSegLayout').style.display = m === 'segments' ? 'grid' : 'none'; $id('tpReadLayout').style.display = m === 'reading' ? 'flex' : 'none'; if (m === 'reading') { $id('tpDrawer').classList.remove('open'); S.pendingRestore = true; } renderAll(); }
  function setCur(name) { S.cur = name; if (S.mode === 'reading') openDrawer(name); else { renderList(); renderWork(); root.querySelector('.tp-row.active')?.scrollIntoView({ block: 'nearest' }); } }
  function jumpNext() { const nx = S.segs.find(s => s.status === 'Pending' || s.status === 'Suggested'); if (!nx) { frappe.show_alert({ message: 'Nothing left to review 🎉', indicator: 'green' }); return; } setCur(nx.name); }

  // ---- comments ----
  function loadComments(name, scope) {
    frappe.db.get_list('Comment', { filters: { reference_doctype: 'Translation Segment', reference_name: name, comment_type: 'Comment' }, fields: ['content', 'comment_by', 'creation'], order_by: 'creation asc', limit: 0 })
      .then(rows => {
        const box = (scope || bodyEl).querySelector('.tp-cmts'); if (!box) return; const form = box.querySelector('.tp-cmtform');
        box.innerHTML = ((rows || []).map(c => `<div class="cmt"><div class="avatar">${esc((c.comment_by || '?')[0].toUpperCase())}</div><div><div class="who"><b>${esc(c.comment_by || '')}</b> · ${frappe.datetime.comment_when ? frappe.datetime.comment_when(c.creation) : ''}</div><div class="txt">${esc((c.content || '').replace(/<[^>]+>/g, ''))}</div></div></div>`).join('') || '<div style="color:var(--faint);font-size:13px">No comments yet.</div>');
        if (form) box.appendChild(form);
      });
  }

  // ---- QA ----
  function parseGlossary(g) {
    const out = []; (g || '').split('\n').forEach(line => { if (line.indexOf('=') < 0 || line.indexOf(':') > -1) return; const p = line.split('='); let en = p[0].replace(/^[-•\s]+/, '').replace(/\/.*$/, '').trim(); let mn = p.slice(1).join('=').replace(/\(.*?\)/g, '').trim(); if (en.length > 1 && mn.length > 1) out.push({ en, mn }); });
    return out;
  }
  function computeQA() {
    const out = [], gloss = parseGlossary(S.glossary).concat((S.terms || []).map(t => ({ en: t.source_term, mn: t.target_term })));
    S.segs.forEach(s => {
      const eff = s.final_text || s.ai_suggestion || s.draft_text || '';
      if (DONE(s.status) && !(s.final_text || '').trim()) out.push({ name: s.name, seq: s.seq, sev: 'hi', type: 'Empty final', detail: 'Marked ' + s.status + ' but Final is empty.' });
      if (eff && !/[Ѐ-ӿ]/.test(eff)) out.push({ name: s.name, seq: s.seq, sev: 'hi', type: 'No Mongolian', detail: 'No Cyrillic — likely untranslated.' });
      else if (eff && eff.trim() === (s.source_text || '').trim()) out.push({ name: s.name, seq: s.seq, sev: 'hi', type: 'Untranslated', detail: 'Identical to the English source.' });
      const ns = (s.source_text.match(/\d+/g) || []).slice().sort(), nf = (eff.match(/\d+/g) || []).slice().sort();
      if (ns.join(',') !== nf.join(',')) out.push({ name: s.name, seq: s.seq, sev: 'mid', type: 'Numbers', detail: 'EN [' + ns.join(', ') + '] vs final [' + nf.join(', ') + ']' });
      gloss.forEach(g => { if ((s.source_text || '').toLowerCase().includes(g.en.toLowerCase()) && eff && !eff.toLowerCase().includes(g.mn.toLowerCase())) out.push({ name: s.name, seq: s.seq, sev: 'mid', type: 'Term', detail: `'${g.en}' → expected '${g.mn}'` }); });
      const dl = (s.draft_text || '').length; if (dl > 20 && eff) { const r = eff.length / dl; if (r > 2.2 || r < 0.45) out.push({ name: s.name, seq: s.seq, sev: 'lo', type: 'Length', detail: 'Final ~' + Math.round(r * 100) + '% of draft length.' }); }
    });
    return out;
  }
  function openQA() {
    const issues = computeQA();
    const body = issues.length
      ? `<div style="font-family:system-ui">${issues.map(it => `<div class="qa-row" data-name="${it.name}"><span class="qa-badge qa-${it.sev}">${esc(it.type)}</span><div><div class="qa-seq">§${it.seq}</div><div class="qa-detail">${esc(it.detail)}</div></div></div>`).join('')}</div>`
      : '<p style="font-family:system-ui;color:var(--muted)">No issues found. 🎉</p>';
    overlay('QA & consistency — ' + issues.length + ' issue(s)', body);
    const ov = document.querySelector('#tpOverlay');
    ov.querySelectorAll('.qa-row').forEach(r => r.addEventListener('click', () => { ov.classList.remove('open'); setCur(r.dataset.name); }));
  }

  // ---- actions ----
  function save(seg, fields) { Object.assign(seg, fields); renderAll(); return frappe.db.set_value('Translation Segment', seg.name, fields).catch(e => frappe.msgprint('Save failed: ' + (e.message || e))); }
  function accept(seg, txt) { save(seg, { final_text: txt, status: 'Accepted' }); frappe.show_alert({ message: 'Accepted §' + seg.seq, indicator: 'green' }); }
  function move(d) { const vis = visible(); if (!vis.length) return; let i = vis.findIndex(s => s.name === S.cur); i = Math.max(0, Math.min(vis.length - 1, (i < 0 ? 0 : i) + d)); S.cur = vis[i].name; renderList(); renderWork(); root.querySelector('.tp-row.active')?.scrollIntoView({ block: 'nearest' }); }

  root.addEventListener('click', e => {
    const chip = e.target.closest('.tp-chip'); if (chip) { S.filter = chip.dataset.f; renderChips(); renderList(); return; }
    const row = e.target.closest('.tp-row'); if (row) { S.cur = row.dataset.name; renderList(); renderWork(); return; }
    const rs = e.target.closest('.rsent');
    if (rs) { openDrawer(rs.dataset.name); return; }   // click a sentence → open its side panel
    const act = e.target.closest('[data-act]')?.dataset.act; if (!act) return;
    if (act === 'closeDrawer') { $id('tpDrawer').classList.remove('open'); renderReader(); return; }
    const s = curSeg(); if (!s) return;
    if (act === 'acceptAI') accept(s, s.ai_suggestion);
    else if (act === 'useAlt') accept(s, s.ai_alternative);
    else if (act === 'useDraft') accept(s, s.draft_text);
    else if (act === 'toggleAlt') e.target.closest('.tp-card.ai')?.querySelector('.tp-alt')?.classList.toggle('open');
    else if (act === 'undo') { save(s, { status: s.ai_suggestion ? 'Suggested' : 'Pending', final_text: '' }); frappe.show_alert({ message: 'Reverted §' + s.seq, indicator: 'orange' }); }
    else if (act === 'lock') { save(s, { status: 'Locked' }); frappe.show_alert({ message: 'Locked §' + s.seq, indicator: 'blue' }); }
    else if (act === 'unlock') { save(s, { status: (s.final_text || '').trim() ? 'Edited' : 'Suggested' }); frappe.show_alert({ message: 'Unlocked §' + s.seq, indicator: 'orange' }); }
    else if (act === 'addterm') {
      frappe.prompt([
        { fieldname: 's', fieldtype: 'Data', label: 'English term', reqd: 1 },
        { fieldname: 't', fieldtype: 'Data', label: 'Approved Mongolian', reqd: 1 },
        { fieldname: 'apply', fieldtype: 'Check', label: 'Apply to the whole book now (re-generate other occurrences)' },
      ], v => {
        const m = v.apply ? 'lac_translation.api.apply_term' : 'lac_translation.api.add_term';
        frappe.call({ method: m, args: { project: S.project, source_term: v.s, target_term: v.t } })
          .then(r => { loadTerms(); frappe.show_alert({ message: v.apply ? ('Applying to ' + ((r.message || {}).affected || 0) + ' segment(s)…') : 'Term added', indicator: v.apply ? 'blue' : 'green' }); });
      }, 'Add approved term', 'Save');
    }
    else if (act === 'regen') {
      frappe.prompt([{ fieldname: 'hint', fieldtype: 'Small Text', label: 'Optional instruction (leave blank to just retry)' }],
        v => {
          frappe.show_alert({ message: 'Regenerating §' + s.seq + '…', indicator: 'blue' });
          frappe.call({ method: 'lac_translation.api.regenerate', args: { segment: s.name, hint: v.hint || '' } })
            .then(r => { const d = r.message || {}; Object.assign(s, { ai_suggestion: d.mn, ai_alternative: d.alt, ai_rationale: d.notes, status: 'Suggested' }); renderAll(); frappe.show_alert({ message: 'Regenerated §' + s.seq, indicator: 'green' }); });
        }, 'Regenerate suggestion', 'Run');
    }
    else if (act === 'acceptsug') {
      const sn = e.target.closest('[data-act]').dataset.sug; const su = S.suggestions.find(x => x.name === sn); if (!su) return;
      S.suggestions = S.suggestions.filter(x => x.name !== sn);
      frappe.db.set_value('Translation Suggestion', sn, 'status', 'Accepted');
      save(s, { final_text: su.suggested_text, status: 'Edited' });
      frappe.show_alert({ message: 'Change accepted §' + s.seq, indicator: 'green' });
    }
    else if (act === 'rejectsug') {
      const sn = e.target.closest('[data-act]').dataset.sug;
      S.suggestions = S.suggestions.filter(x => x.name !== sn);
      frappe.db.set_value('Translation Suggestion', sn, 'status', 'Rejected');
      renderAll(); frappe.show_alert({ message: 'Change rejected', indicator: 'orange' });
    }
    else if (act === 'propose') {
      // Inline (no popup): turn the Final textarea's current text into a suggestion.
      const ed = e.target.closest('.tp-drawerinner, .tp-inner');
      const txt = (ed?.querySelector('.tp-final')?.value || '').trim();
      if (!txt) { frappe.show_alert({ message: 'Type the Final text first, then Propose.', indicator: 'orange' }); return; }
      frappe.call({ method: 'frappe.client.insert', args: { doc: { doctype: 'Translation Suggestion', project: S.project, segment: s.name, origin: 'Reviewer', author: frappe.session.user, suggested_text: txt, status: 'Open' } } })
        .then(() => { loadSuggestions().then(renderAll); frappe.show_alert({ message: 'Proposed a change to §' + s.seq, indicator: 'blue' }); });
    }
    else if (act === 'askai') {
      const box = e.target.closest('.tp-card.ai')?.querySelector('.tp-aibox');
      if (box) box.innerHTML = '<span class="nochange">Asking AI…</span>';
      frappe.call({ method: 'lac_translation.api.ai_suggest_one', args: { segment: s.name } })
        .then(r => {
          const d = r.message || {}; if (!box) return;
          box.dataset.mn = d.mn || '';
          box.innerHTML = d.mn
            ? `${esc(d.mn)}<div class="cta"><button class="tp-btn primary" data-act="useai" style="padding:3px 10px;font-size:12px">Use as my edit</button></div>${d.notes ? `<div class="notes"><span>${esc(d.notes)}</span></div>` : ''}`
            : '<span class="nochange">No suggestion returned.</span>';
        })
        .catch(e2 => { if (box) box.innerHTML = '<span class="nochange">AI error: ' + esc(e2.message || String(e2)) + '</span>'; });
    }
    else if (act === 'useai') { const box = e.target.closest('.tp-card.ai')?.querySelector('.tp-aibox'); const mn = box ? (box.dataset.mn || '') : (s.ai_suggestion || ''); const ta = e.target.closest('.tp-drawerinner, .tp-inner')?.querySelector('.tp-suggest'); if (ta && mn) { ta.value = mn; ta.focus(); } }
    else if (act === 'savesug') {
      // Reviewer inline edit → upsert their suggestion (one per segment), no popup.
      const ta = e.target.closest('.tp-card.suggest')?.querySelector('.tp-suggest');
      const txt = (ta?.value || '').trim();
      const base = (s.final_text || s.ai_suggestion || s.draft_text || '').trim();
      if (!txt) { frappe.show_alert({ message: 'Type your edit first.', indicator: 'orange' }); return; }
      if (txt === base) { frappe.show_alert({ message: 'That is the same as the current text — no change to suggest.', indicator: 'orange' }); return; }
      const mine = (S.suggestions || []).find(x => x.segment === s.name && x.author === frappe.session.user && x.origin === 'Reviewer');
      const done = () => { loadSuggestions().then(renderAll); frappe.show_alert({ message: 'Suggestion saved for §' + s.seq, indicator: 'green' }); };
      if (mine) frappe.db.set_value('Translation Suggestion', mine.name, { suggested_text: txt }).then(done).catch(e2 => frappe.msgprint('Save failed: ' + (e2.message || e2)));
      else frappe.call({ method: 'frappe.client.insert', args: { doc: { doctype: 'Translation Suggestion', project: S.project, segment: s.name, origin: 'Reviewer', author: frappe.session.user, suggested_text: txt, status: 'Open' } } }).then(done).catch(e2 => frappe.msgprint('Save failed: ' + (e2.message || e2)));
    }
    else if (act === 'delsug') {
      const sn = e.target.closest('[data-act]').dataset.sug;
      S.suggestions = S.suggestions.filter(x => x.name !== sn);
      frappe.call({ method: 'frappe.client.delete', args: { doctype: 'Translation Suggestion', name: sn } }).then(() => { loadSuggestions().then(renderAll); frappe.show_alert({ message: 'Suggestion removed', indicator: 'orange' }); });
    }
    else if (act === 'saveFinal') { const ed = e.target.closest('.tp-drawerinner, .tp-inner'); const v = ed.querySelector('.tp-final').value; const st = (v.trim() === (s.ai_suggestion || '').trim() || v.trim() === (s.draft_text || '').trim()) ? 'Accepted' : 'Edited'; save(s, { final_text: v, status: st }); frappe.show_alert({ message: 'Saved §' + s.seq, indicator: 'green' }); }
  });
  root.addEventListener('submit', e => {
    const form = e.target.closest('.tp-cmtform'); if (!form) return;
    e.preventDefault(); const inp = form.querySelector('.tp-cmtinput'); const val = (inp.value || '').trim(); if (!val) return;
    const name = S.cur, scope = form.closest('.tp-drawerinner, .tp-inner');
    frappe.call({ method: 'frappe.client.insert', args: { doc: { doctype: 'Comment', comment_type: 'Comment', reference_doctype: 'Translation Segment', reference_name: name, content: val } } }).then(() => { inp.value = ''; loadComments(name, scope); });
  });
  $id('tpSearch').addEventListener('input', e => { S.q = e.target.value; renderList(); });
  $id('tpBook').addEventListener('change', e => { S.project = e.target.value; const p = S.projects.find(x => x.name === S.project); S.glossary = p ? (p.glossary || '') : ''; S.cur = null; $id('tpDrawer').classList.remove('open'); if (S.mode === 'reading') S.pendingRestore = true; loadSegs(); });
  { let st; $id('tpReaderPane').addEventListener('scroll', () => { clearTimeout(st); st = setTimeout(saveScroll, 250); }); }
  $id('tpResume').onclick = () => { let seg = null; try { seg = localStorage.getItem('tpSeg_' + S.project); } catch (e) { } if (seg && S.segs.some(x => x.name === seg)) scrollToSeg(seg); else restoreScroll(); };
  $id('tpPanel').onclick = () => { const name = (S.cur && S.segs.some(x => x.name === S.cur)) ? S.cur : (S.segs[0] && S.segs[0].name); if (name) openDrawer(name); };
  $id('tpPrevChg').onclick = () => gotoChange(-1);
  $id('tpNextChg').onclick = () => gotoChange(1);
  $id('tpMSeg').onclick = () => setMode('segments');
  $id('tpMRead').onclick = () => setMode('reading');
  $id('tpNext').onclick = jumpNext;
  $id('tpQA').onclick = openQA;
  $id('tpBiToggle').onclick = () => { S.bilingual = !S.bilingual; $id('tpBiToggle').textContent = S.bilingual ? 'Hide English' : 'Show English'; renderReader(); };
  document.addEventListener('keydown', e => {
    if (!page.wrapper.is(':visible')) return;
    if (/input|textarea|select/i.test((document.activeElement || {}).tagName || '')) return;
    if (e.key === 'n' || e.key === 'N') { jumpNext(); return; }
    if (S.mode !== 'segments') return;
    if (e.key === 'j' || e.key === 'J') move(1);
    else if (e.key === 'k' || e.key === 'K') move(-1);
    else if (e.key === 'a' || e.key === 'A') { const s = curSeg(); if (s && s.ai_suggestion && s.status !== 'Locked') accept(s, s.ai_suggestion); }
    else if (e.key === 'e' || e.key === 'E') { e.preventDefault(); $id('tpWork')?.querySelector('.tp-final, .tp-suggest')?.focus(); }
  });

  // ---- overlays ----
  function overlay(title, bodyHtml) {
    let ov = document.querySelector('#tpOverlay');
    if (!ov) { ov = document.createElement('div'); ov.id = 'tpOverlay'; ov.className = 'tp-overlay'; document.body.appendChild(ov); ov.addEventListener('click', e => { if (e.target === ov) ov.classList.remove('open'); }); }
    ov.classList.toggle('tp-dark', root.classList.contains('tp-dark'));
    ov.innerHTML = `<div class="tp-modal"><header><h3>${esc(title)}</h3><button class="mclose" id="tpOvClose">Close</button></header><div class="scroll">${bodyHtml}</div></div>`;
    ov.querySelector('#tpOvClose').onclick = () => ov.classList.remove('open');
    ov.classList.add('open');
  }
  $id('tpGloss').onclick = () => overlay('Glossary / style guide', '<pre>' + esc(S.glossary || 'No glossary set for this project.') + '</pre>');
  $id('tpTxt').onclick = () => { const txt = S.segs.map(s => s.final_text || s.ai_suggestion || s.draft_text || '').join('\n'); const b = new Blob([txt], { type: 'text/plain;charset=utf-8' }); const a = document.createElement('a'); a.href = URL.createObjectURL(b); a.download = (S.projects.find(p => p.name === S.project)?.title || 'book') + '_MN.txt'; a.click(); };
  $id('tpDocx').onclick = () => { if (!S.project) return; frappe.show_alert({ message: 'Building .docx…', indicator: 'blue' }); frappe.call({ method: 'lac_translation.api.export_docx_grouped', args: { project: S.project } }).then(r => { if (r.message && r.message.file_url) window.open(r.message.file_url, '_blank'); }); };

  // Reviewers (non-admins) get a clean review-only view. They KEEP the ⋯ More
  // menu so they can open Terms/Glossary (adding approved terminology is their
  // core job) — we only hide the admin book-management tools (bulk AI generate,
  // import/align/audit/complete/export).
  if (!frappe.user.has_role('System Manager')) {
    ['tpGen', 'tpImport', 'tpImportReviewed', 'tpImportRev', 'tpRealign',
     'tpAudit', 'tpComplete', 'tpRevert', 'tpTxt', 'tpDocx']
      .forEach(id => { const el = $id(id); if (el) el.style.display = 'none'; });
    document.querySelectorAll('#tpMenu .tp-mi-sep').forEach(el => { el.style.display = 'none'; });
  }
  $id('tpGen').onclick = () => {
    if (!S.project) { frappe.msgprint('Pick a book first.'); return; }
    const chapters = [...new Set(S.segs.map(s => s.chapter || 'Book'))];
    const pend = S.segs.filter(s => s.status === 'Pending').length;
    const d = new frappe.ui.Dialog({
      title: 'Generate AI suggestions',
      fields: [
        { fieldname: 'mode', fieldtype: 'Select', label: 'Scope', reqd: 1, default: 'Whole book (all pending)', options: ['Whole book (all pending)', 'This chapter', 'Sentence range', 'First N pending'].join('\n') },
        { fieldname: 'chapter', fieldtype: 'Select', label: 'Chapter', options: chapters.join('\n'), depends_on: 'eval:doc.mode=="This chapter"' },
        { fieldname: 'from_seq', fieldtype: 'Int', label: 'From § (seq)', depends_on: 'eval:doc.mode=="Sentence range"' },
        { fieldname: 'to_seq', fieldtype: 'Int', label: 'To § (seq)', depends_on: 'eval:doc.mode=="Sentence range"' },
        { fieldname: 'count', fieldtype: 'Int', label: 'How many', default: 20, depends_on: 'eval:doc.mode=="First N pending"' },
        { fieldname: 'info', fieldtype: 'HTML', options: `<div style="font-size:12px;color:#888">${pend} pending segment(s). Runs in the background; suggestions appear as it completes. Tip: start with a small range to check quality/cost.</div>` },
      ],
      primary_action_label: 'Generate',
      primary_action(v) {
        const args = { project: S.project };
        if (v.mode === 'This chapter') args.chapter = v.chapter;
        else if (v.mode === 'Sentence range') { args.from_seq = v.from_seq; args.to_seq = v.to_seq; }
        else if (v.mode === 'First N pending') args.limit = v.count;
        d.hide();
        frappe.call({ method: 'lac_translation.api.generate', args }).then(r => frappe.show_alert({ message: 'Queued ' + ((r.message || {}).queued || 0) + ' segment(s)…', indicator: 'blue' }));
      },
    });
    d.show();
  };
  $id('tpMore').onclick = (e) => { e.stopPropagation(); $id('tpMenu').classList.toggle('open'); };
  $id('tpMenu').addEventListener('click', () => $id('tpMenu').classList.remove('open'));
  document.addEventListener('click', () => { const m = $id('tpMenu'); if (m) m.classList.remove('open'); });
  $id('tpImport').onclick = openImport;
  $id('tpTerms').onclick = openTerms;
  $id('tpRealign').onclick = () => {
    if (!S.project) { frappe.msgprint('Pick a book first.'); return; }
    const d = new frappe.ui.Dialog({
      title: 'Re-align source (EN ↔ MN)',
      fields: [{ fieldname: 'english', fieldtype: 'Attach', label: 'English source (.docx)', reqd: 1 },
      { fieldname: 'hint', fieldtype: 'HTML', options: '<div style="font-size:12px;color:#888">Cleans the English (drops page/print artifacts, number tables &amp; table-of-contents lines), splits it into sentences, then uses GPT-4o to match each Mongolian segment to its true English counterpart <b>by meaning</b> — it reads both languages, so it aligns where embeddings can\'t. Mongolian sentences with no English counterpart stay in polish mode (blank source). Runs in the background (~$0.70 for a full book).</div>' }],
      primary_action_label: 'Re-align',
      primary_action(v) { d.hide(); frappe.call({ method: 'lac_translation.api.align_llm', args: { project: S.project, english_file: v.english } }).then(() => frappe.show_alert({ message: 'Re-aligning with GPT-4o in the background…', indicator: 'blue' })); },
    });
    d.show();
  };
  $id('tpAudit').onclick = () => {
    if (!S.project) { frappe.msgprint('Pick a book first.'); return; }
    const d = new frappe.ui.Dialog({
      title: 'Find missing passages (completeness audit)',
      fields: [{ fieldname: 'english', fieldtype: 'Attach', label: 'English source (PDF or .docx)', reqd: 1 },
      { fieldname: 'min_run', fieldtype: 'Int', label: 'Minimum passage length (sentences)', default: 6 },
      { fieldname: 'hint', fieldtype: 'HTML', options: '<div style="font-size:12px;color:#888">Scans the English source for passages that have no Mongolian counterpart, has GPT-4o confirm each is genuinely missing (not just summarised), drafts the Mongolian, and produces a review report. Does <b>not</b> change the book. Runs in the background.</div>' }],
      primary_action_label: 'Run audit',
      primary_action(v) { d.hide(); frappe.call({ method: 'lac_translation.api.omission_report', args: { project: S.project, english_file: v.english, min_run: v.min_run || 6 } }).then(() => frappe.show_alert({ message: 'Auditing in the background — the report will be attached to the project when done.', indicator: 'blue' })); },
    });
    d.show();
  };
  $id('tpComplete').onclick = () => {
    if (!S.project) { frappe.msgprint('Pick a book first.'); return; }
    const d = new frappe.ui.Dialog({
      title: 'Complete book — fill missing passages',
      fields: [{ fieldname: 'english', fieldtype: 'Attach', label: 'English source (PDF or .docx)', reqd: 1 },
      { fieldname: 'min_run', fieldtype: 'Int', label: 'Minimum passage length (sentences)', default: 6 },
      { fieldname: 'hint', fieldtype: 'HTML', options: '<div style="font-size:12px;color:#888">Translates every verified-missing passage and inserts it in place as a reviewable <b>Suggested</b> segment. Re-running rebuilds cleanly (no duplicates). Lower minimum = more thorough but slower/costlier. Runs in the background; refresh when it finishes.</div>' }],
      primary_action_label: 'Fill missing',
      primary_action(v) { d.hide(); frappe.call({ method: 'lac_translation.api.apply_omissions', args: { project: S.project, english_file: v.english, min_run: v.min_run || 6 } }).then(() => frappe.show_alert({ message: 'Filling missing passages in the background — refresh in a few minutes.', indicator: 'blue' })); },
    });
    d.show();
  };
  $id('tpRevert').onclick = () => {
    if (!S.project) { frappe.msgprint('Pick a book first.'); return; }
    frappe.confirm('Remove all AI-filled (Suggested) passages from this book? Your original translation is not touched.', () => {
      frappe.call({ method: 'lac_translation.api.revert_omissions', args: { project: S.project } })
        .then(r => { frappe.show_alert({ message: 'Removed ' + ((r.message || {}).deleted || 0) + ' AI passage(s)', indicator: 'orange' }); loadSegs(); });
    });
  };
  $id('tpImportReviewed').onclick = () => {
    const d = new frappe.ui.Dialog({
      title: 'Import reviewed translation', size: 'large',
      fields: [
        { fieldname: 'title', fieldtype: 'Data', label: 'Book title', reqd: 1 },
        { fieldname: 'model', fieldtype: 'Data', label: 'OpenAI model', default: 'gpt-4o' },
        { fieldname: 'mongolian', fieldtype: 'Attach', label: 'Reviewed Mongolian (.docx with tracked changes/comments)', reqd: 1 },
        { fieldname: 'english', fieldtype: 'Attach', label: 'English source (.docx, optional reference)' },
        { fieldname: 'glossary', fieldtype: 'Small Text', label: 'Glossary / style (optional)' },
        { fieldname: 'hint', fieldtype: 'HTML', options: '<div style="font-size:12px;color:#888">Creates a book where the draft is the “before” text, each tracked change is an Accept/Reject suggestion, and comments attach to the timeline.</div>' },
      ],
      primary_action_label: 'Import',
      primary_action(v) {
        d.hide(); frappe.show_alert({ message: 'Importing reviewed translation…', indicator: 'blue' });
        frappe.call({ method: 'lac_translation.api.import_reviewed', args: { title: v.title, mongolian_file_url: v.mongolian, english_file_url: v.english || '', model: v.model || 'gpt-4o', glossary: v.glossary || '' } })
          .then(r => { const m = r.message || {}; frappe.show_alert({ message: 'Imported ' + (m.segments || 0) + ' segments, ' + (m.suggestions || 0) + ' change(s), ' + (m.comments || 0) + ' comment(s)', indicator: 'green' }); loadProjects().then(() => { S.project = m.project; const sel = $id('tpBook'); if (sel) sel.value = m.project; S.cur = null; loadSegs(); }); });
      },
    });
    d.show();
  };
  $id('tpImportRev').onclick = () => {
    if (!S.project) { frappe.msgprint('Pick a book first.'); return; }
    const d = new frappe.ui.Dialog({
      title: 'Import reviewer changes (comments + tracked changes)',
      fields: [{ fieldname: 'file', fieldtype: 'Attach', label: 'Reviewed .docx (of the CURRENT book)', reqd: 1 },
      { fieldname: 'hint', fieldtype: 'HTML', options: '<div style="font-size:12px;color:#888">Upload the reviewer\'s Word (.docx) of <b>the book selected above</b>. This <b>replaces</b> any earlier review (no duplicates): their edits attach as Accept/Reject cards and their comments attach to each sentence. Runs in the background.</div>' }],
      primary_action_label: 'Import review',
      primary_action(v) {
        d.hide(); frappe.show_alert({ message: 'Importing review in the background — refresh in a moment…', indicator: 'blue' });
        frappe.call({ method: 'lac_translation.api.reimport_review', args: { project: S.project, file_url: v.file } });
      },
    });
    d.show();
  };

  function openTerms() {
    const isAdmin = frappe.user.has_role('System Manager');
    const ea = s => (s || '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
    const rows = (S.terms || []).map(t => {
      const uses = S.segs.filter(s => (s.source_text || '').toLowerCase().includes((t.source_term || '').toLowerCase())).length;
      return `<tr><td><b>${esc(t.source_term)}</b></td><td>${esc(t.target_term)}</td><td style="text-align:center">${uses}</td><td><button class="tp-btn" data-fr="${ea(t.source_term)}|||${ea(t.target_term)}" style="padding:3px 9px;font-size:12px">Fix in text →</button></td></tr>`;
    }).join('') || '<tr><td colspan="4" style="color:var(--faint);padding:14px">No terms yet — add one below.</td></tr>';
    const body = `<div style="font-family:system-ui">
      <table class="tp-terms"><thead><tr><th>Source (EN)</th><th>Approved (MN)</th><th>Uses</th><th></th></tr></thead><tbody>${rows}</tbody></table>
      <div class="tp-termadd"><input id="tt_s" placeholder="English term"><input id="tt_t" placeholder="Approved Mongolian"><button class="tp-btn primary" id="tt_add">Add term</button></div>
      <div style="font-size:12px;color:var(--faint);margin-top:8px">The termbase keeps future AI translations consistent. To fix wording that is <b>already</b> in the book, use Find &amp; Replace below (“Fix in text →” fills it in for you).</div>
      <div class="tp-fr">
        <div class="tp-frhead">🔤 Find &amp; Replace in the book <span>— fix a spelling/name everywhere (recommended for names &amp; terms; instant, no AI)</span></div>
        <div class="tp-termadd"><input id="fr_find" placeholder="Find (exact text in the translation)"><input id="fr_rep" placeholder="Replace with"><button class="tp-btn" id="fr_findbtn">🔍 Find</button></div>
        <div id="fr_count" style="font-size:12.5px;color:var(--muted);margin:9px 2px;min-height:16px"></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="tp-btn primary" id="fr_suggest">Replace as suggestions</button>
          ${isAdmin ? '<button class="tp-btn" id="fr_apply">Replace directly</button>' : ''}
        </div>
        <div style="font-size:12px;color:var(--faint);margin-top:8px">“As suggestions” adds reviewable <ins>green</ins>/<del>red</del> changes for the editor to accept.${isAdmin ? ' “Replace directly” edits the text immediately.' : ''} Locked sentences are never touched. Case-sensitive, exact match.</div>
      </div>
    </div>`;
    overlay('Termbase & Find/Replace — ' + (S.terms || []).length + ' term(s)', body);
    const ov = document.querySelector('#tpOverlay');
    ov.querySelector('#tt_add').onclick = () => {
      const s = ov.querySelector('#tt_s').value.trim(), t = ov.querySelector('#tt_t').value.trim();
      if (!s || !t) return;
      frappe.call({ method: 'lac_translation.api.add_term', args: { project: S.project, source_term: s, target_term: t } })
        .then(() => { frappe.show_alert({ message: 'Term added', indicator: 'green' }); loadTerms().then(openTerms); });
    };
    const fFind = ov.querySelector('#fr_find'), fRep = ov.querySelector('#fr_rep'), fCount = ov.querySelector('#fr_count');
    const runFind = () => {
      const f = fFind.value.trim(); if (!f) { fCount.textContent = ''; return; }
      fCount.textContent = 'Searching…';
      frappe.call({ method: 'lac_translation.api.term_find', args: { project: S.project, find_text: f } })
        .then(r => { const d = r.message || {}; fCount.innerHTML = d.matches ? `<b>${d.matches}</b> sentence(s) contain “${esc(f)}”${d.seqs && d.seqs.length ? ' — §' + d.seqs.slice(0, 12).join(', §') + (d.matches > 12 ? '…' : '') : ''}` : `No sentences contain “${esc(f)}”.`; });
    };
    const runReplace = (mode) => {
      const f = fFind.value.trim(), t = fRep.value.trim();
      if (!f || !t) { frappe.show_alert({ message: 'Enter both find and replace text.', indicator: 'orange' }); return; }
      const go = () => frappe.call({ method: 'lac_translation.api.term_replace', args: { project: S.project, find_text: f, replace_text: t, mode } })
        .then(r => { const d = r.message || {}; ov.classList.remove('open'); frappe.show_alert({ message: (mode === 'apply' ? 'Replaced directly in ' : 'Added suggestions for ') + (d.changed || 0) + ' sentence(s)', indicator: 'green' }); loadSegs(); });
      if (mode === 'apply') frappe.confirm(`Directly replace “${esc(f)}” → “${esc(t)}” in every non-locked sentence?`, go); else go();
    };
    ov.querySelector('#fr_findbtn').onclick = runFind;
    fFind.addEventListener('keydown', e => { if (e.key === 'Enter') runFind(); });
    ov.querySelector('#fr_suggest').onclick = () => runReplace('suggest');
    const applyBtnEl = ov.querySelector('#fr_apply'); if (applyBtnEl) applyBtnEl.onclick = () => runReplace('apply');
    ov.querySelectorAll('[data-fr]').forEach(b => b.onclick = () => {
      const [f, t] = b.getAttribute('data-fr').split('|||');
      fFind.value = f; fRep.value = t; runFind(); fFind.focus();
    });
  }

  function openImport() {
    const d = new frappe.ui.Dialog({
      title: 'Import a book', size: 'large',
      fields: [
        { fieldname: 'title', fieldtype: 'Data', label: 'Book title', reqd: 1 },
        { fieldname: 'model', fieldtype: 'Data', label: 'OpenAI model', default: 'gpt-4o' },
        { fieldname: 'english', fieldtype: 'Attach', label: 'English (.docx)', reqd: 1 },
        { fieldname: 'mongolian', fieldtype: 'Attach', label: 'Mongolian draft (.docx, optional)' },
        { fieldname: 'glossary', fieldtype: 'Small Text', label: 'Glossary / style (optional)' },
      ],
      primary_action_label: 'Import',
      primary_action(v) {
        d.hide(); frappe.show_alert({ message: 'Importing… this can take a moment', indicator: 'blue' });
        frappe.call({ method: 'lac_translation.api.import_book', args: { title: v.title, english_file_url: v.english, mongolian_file_url: v.mongolian || '', model: v.model || 'gpt-4o', glossary: v.glossary || '' } })
          .then(r => { const m = r.message || {}; frappe.show_alert({ message: 'Imported ' + (m.segments || 0) + ' segments', indicator: 'green' }); loadProjects().then(() => { S.project = m.project; const sel = $id('tpBook'); if (sel) sel.value = m.project; const p = S.projects.find(x => x.name === S.project); S.glossary = p ? (p.glossary || '') : ''; S.cur = null; loadSegs(); }); });
      },
    });
    d.show();
  }

  frappe.realtime.on('lac_translation_progress', d => {
    if (!d || d.project !== S.project) return;
    frappe.show_alert({ message: 'AI: ' + d.done + '/' + d.total + '…', indicator: 'blue' });
    if (d.done >= d.total) loadSegs();
  });
  $id('tpTheme').onclick = () => root.classList.toggle('tp-dark');

  // Redactors live in the Reading view — open there and jump to where they left off.
  loadProjects().then(() => { if (!frappe.user.has_role('System Manager')) { setMode('reading'); } });
};
