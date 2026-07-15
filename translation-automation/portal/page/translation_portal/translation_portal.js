frappe.pages['translation-portal'].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({ parent: wrapper, title: 'Translation Portal', single_column: true });

  const STATUS = ['Pending', 'Suggested', 'Accepted', 'Edited', 'Rejected', 'Locked'];
  const DONE = s => s === 'Accepted' || s === 'Edited' || s === 'Locked';
  const S = { projects: [], project: null, glossary: '', segs: [], cur: null, mode: 'segments', filter: 'All', q: '', bilingual: false };
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
        <button class="tp-btn ghost" id="tpImport">＋ Import</button>
        <button class="tp-btn ghost" id="tpGen">✨ AI</button>
        <button class="tp-btn ghost" id="tpGloss">📑 Gloss</button>
        <button class="tp-btn ghost" id="tpTxt">⬇ txt</button>
        <button class="tp-btn ghost" id="tpDocx">📄 docx</button>
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
        <div class="tp-readerpane"><div class="tp-readerdoc">
          <div class="tp-hint">
            <span>Click any sentence to review &amp; edit.</span>
            <span style="color:var(--s-Suggested)"><span class="u"></span> AI suggestion waiting</span>
            <span style="color:var(--s-Pending)"><span class="u"></span> not reviewed</span>
            <button class="tp-btn ghost" id="tpBiToggle" style="margin-left:auto;padding:4px 10px;font-size:12px">Show English</button>
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
        S.projects = r || [];
        $id('tpBook').innerHTML = S.projects.map(p => `<option value="${p.name}">${esc(p.title || p.name)}</option>`).join('') || '<option>No projects</option>';
        if (S.projects.length) { S.project = S.projects[0].name; S.glossary = S.projects[0].glossary || ''; return loadSegs(); }
      });
  }
  function loadSegs() {
    return frappe.db.get_list('Translation Segment', {
      filters: { project: S.project },
      fields: ['name', 'seq', 'chapter', 'status', 'source_text', 'draft_text', 'ai_suggestion', 'ai_alternative', 'ai_rationale', 'final_text', 'reviewer_comment'],
      order_by: 'seq asc', limit: 0,
    }).then(r => { S.segs = r || []; if (!curSeg()) S.cur = S.segs.length ? S.segs[0].name : null; renderAll(); });
  }

  // ---- shared editor ----
  function editorHTML(s) {
    const locked = s.status === 'Locked', finalized = s.status === 'Accepted' || s.status === 'Edited';
    let ai;
    if (s.ai_suggestion) {
      const changed = (s.ai_suggestion || '').trim() !== (s.draft_text || '').trim();
      let inner = `<div class="body"><div class="diff">${changed ? diff(s.draft_text, s.ai_suggestion) : '<span class="nochange">No change — AI kept the draft.</span>'}</div></div>`;
      if (s.ai_rationale) inner += `<div class="notes"><svg width="13" height="13" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l2 4 4 .6-3 3 .7 4L8 14.8 4.3 16.7l.7-4-3-3 4-.6z"/></svg><span>${esc(s.ai_rationale)}</span></div>`;
      if (!locked) {
        inner += `<div class="cta"><button class="tp-btn primary" data-act="acceptAI">✔ Accept</button>`;
        if (s.ai_alternative) inner += `<button class="alt-toggle" data-act="toggleAlt">▾ Alternative</button>`;
        inner += `<button class="tp-btn" data-act="regen" style="margin-left:auto">↻ Regenerate</button></div>`;
        if (s.ai_alternative) inner += `<div class="alt" id="tpAlt"><div class="body" style="border-top:1px solid var(--border)">${esc(s.ai_alternative)}</div><div class="cta"><button class="tp-btn" data-act="useAlt">Use alternative</button></div></div>`;
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
    return `
      ${doneBar}
      <div class="tp-card en"><div class="lbl"><span class="name">Original · English</span></div><div class="body">${esc(s.source_text)}</div></div>
      <div class="tp-card"><div class="lbl"><span class="name">Translator draft</span>${draftBtn}</div><div class="body">${esc(s.draft_text)}</div></div>
      ${ai}
      <div class="tp-card final"><div class="lbl"><span class="name">Final</span>${saveBtn}</div><div class="body"><textarea id="tpFinal" ${locked ? 'readonly' : ''} placeholder="Accept above or type the final Mongolian…">${esc(s.final_text)}</textarea></div></div>
      <div class="tp-card comments"><div class="lbl"><span class="name">Comments</span></div><div class="body" id="tpCmts"><div style="color:var(--faint);font-size:13px">Loading…</div><form class="cmt-form" id="tpCmtForm"><input id="tpCmtInput" placeholder="Add a note for the team…"><button class="tp-btn" type="submit">Post</button></form></div></div>`;
  }

  // ---- segments render ----
  function counts() { const c = { All: S.segs.length }; STATUS.forEach(k => c[k] = S.segs.filter(s => s.status === k).length); return c; }
  function renderChips() { const c = counts(); const order = ['All', 'Pending', 'Suggested', 'Accepted', 'Edited', 'Locked']; $id('tpChips').innerHTML = order.map(k => `<button class="tp-chip" data-f="${k}" aria-pressed="${S.filter === k}">${k} <span class="n">${c[k] || 0}</span></button>`).join(''); }
  function visible() { return S.segs.filter(s => { if (S.filter !== 'All' && s.status !== S.filter) return false; if (S.q) { const q = S.q.toLowerCase(); return ((s.source_text || '') + ' ' + (s.draft_text || '') + ' ' + (s.final_text || s.ai_suggestion || '')).toLowerCase().includes(q); } return true; }); }
  function renderList() {
    const vis = visible(), groups = {}, order = [];
    vis.forEach(s => { const ch = s.chapter || 'Book'; if (!groups[ch]) { groups[ch] = []; order.push(ch); } groups[ch].push(s); });
    let html = '';
    order.forEach(ch => {
      const done = groups[ch].filter(s => DONE(s.status)).length;
      html += `<div class="tp-chapter"><span>${esc(ch)}</span><span>${done}/${groups[ch].length}</span></div>`;
      html += groups[ch].map(s => `<div class="tp-row ${s.name === S.cur ? 'active' : ''}" data-name="${s.name}"><div class="tp-dot" style="background:${color(s.status)}"></div><div><div class="seq">§${s.seq} · ${s.status}</div><div class="src">${esc(s.source_text)}</div><div class="mn">${esc(s.final_text || s.ai_suggestion || s.draft_text)}</div></div></div>`).join('');
    });
    $id('tpList').innerHTML = html || '<div style="padding:20px;color:var(--faint);font-size:13px">No segments match.</div>';
  }
  function renderWork() {
    const s = curSeg(); const w = $id('tpWork');
    if (!s) { w.innerHTML = '<div style="color:var(--faint);padding:40px 0">Select a segment.</div>'; return; }
    w.innerHTML = `<div class="tp-seghead"><h2>${esc(s.chapter || 'Book')} · Segment §${s.seq}</h2>${pill(s.status)}</div>` + editorHTML(s);
    loadComments(s.name);
  }

  // ---- reading render ----
  function renderReader() {
    const groups = {}, order = [];
    S.segs.forEach(s => { const ch = s.chapter || 'Book'; if (!groups[ch]) { groups[ch] = []; order.push(ch); } groups[ch].push(s); });
    const open = $id('tpDrawer').classList.contains('open');
    $id('tpReaderBody').innerHTML = order.map(ch => {
      let block;
      if (S.bilingual) {
        block = groups[ch].map(s => {
          const txt = s.final_text || s.ai_suggestion || s.draft_text || '';
          const sel = (open && s.name === S.cur) ? ' sel' : '';
          return `<div class="rbi"><div class="en">${esc(s.source_text)}</div><div class="rsent st-${s.status}${sel}" data-name="${s.name}">${esc(txt)}</div></div>`;
        }).join('');
      } else {
        block = '<p>' + groups[ch].map(s => {
          const txt = s.final_text || s.ai_suggestion || s.draft_text || '';
          const sel = (open && s.name === S.cur) ? ' sel' : '';
          return `<span class="rsent st-${s.status}${sel}" data-name="${s.name}">${esc(txt)}</span>`;
        }).join(' ') + '</p>';
      }
      return `<div class="chap-title">${esc(ch)}</div>${block}`;
    }).join('') || '<div style="color:var(--faint)">No segments.</div>';
  }
  function openDrawer(name) {
    S.cur = name; const s = curSeg(); if (!s) return;
    $id('tpDrawer').classList.add('open');
    $id('tpDrawerInner').innerHTML = `<div class="tp-drawerhead"><div class="tp-seghead" style="gap:8px"><h2>§${s.seq}</h2>${pill(s.status)}</div><button class="tp-btn ghost tp-icon" data-act="closeDrawer">✕</button></div>` + editorHTML(s);
    loadComments(s.name); renderReader();
  }

  function renderProgress() { const done = S.segs.filter(s => DONE(s.status)).length; const pct = S.segs.length ? Math.round(done * 100 / S.segs.length) : 0; $id('tpProgLabel').textContent = `${done} / ${S.segs.length} · ${pct}%`; $id('tpProgFill').style.width = pct + '%'; }
  function renderAll() { renderProgress(); if (S.mode === 'segments') { renderChips(); renderList(); renderWork(); } else { renderReader(); if ($id('tpDrawer').classList.contains('open')) openDrawer(S.cur); } }
  function setMode(m) { S.mode = m; $id('tpMSeg').setAttribute('aria-pressed', m === 'segments'); $id('tpMRead').setAttribute('aria-pressed', m === 'reading'); $id('tpSegLayout').style.display = m === 'segments' ? 'grid' : 'none'; $id('tpReadLayout').style.display = m === 'reading' ? 'flex' : 'none'; if (m === 'reading') $id('tpDrawer').classList.remove('open'); renderAll(); }
  function setCur(name) { S.cur = name; if (S.mode === 'reading') openDrawer(name); else { renderList(); renderWork(); root.querySelector('.tp-row.active')?.scrollIntoView({ block: 'nearest' }); } }
  function jumpNext() { const nx = S.segs.find(s => s.status === 'Pending' || s.status === 'Suggested'); if (!nx) { frappe.show_alert({ message: 'Nothing left to review 🎉', indicator: 'green' }); return; } setCur(nx.name); }

  // ---- comments ----
  function loadComments(name) {
    frappe.db.get_list('Comment', { filters: { reference_doctype: 'Translation Segment', reference_name: name, comment_type: 'Comment' }, fields: ['content', 'comment_by', 'creation'], order_by: 'creation asc', limit: 0 })
      .then(rows => {
        const box = $id('tpCmts'); if (!box) return; const form = box.querySelector('#tpCmtForm');
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
    const out = [], gloss = parseGlossary(S.glossary);
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
    const rs = e.target.closest('.rsent'); if (rs) { openDrawer(rs.dataset.name); return; }
    const act = e.target.closest('[data-act]')?.dataset.act; if (!act) return;
    if (act === 'closeDrawer') { $id('tpDrawer').classList.remove('open'); renderReader(); return; }
    const s = curSeg(); if (!s) return;
    if (act === 'acceptAI') accept(s, s.ai_suggestion);
    else if (act === 'useAlt') accept(s, s.ai_alternative);
    else if (act === 'useDraft') accept(s, s.draft_text);
    else if (act === 'toggleAlt') $id('tpAlt')?.classList.toggle('open');
    else if (act === 'undo') { save(s, { status: s.ai_suggestion ? 'Suggested' : 'Pending', final_text: '' }); frappe.show_alert({ message: 'Reverted §' + s.seq, indicator: 'orange' }); }
    else if (act === 'lock') { save(s, { status: 'Locked' }); frappe.show_alert({ message: 'Locked §' + s.seq, indicator: 'blue' }); }
    else if (act === 'unlock') { save(s, { status: (s.final_text || '').trim() ? 'Edited' : 'Suggested' }); frappe.show_alert({ message: 'Unlocked §' + s.seq, indicator: 'orange' }); }
    else if (act === 'regen') {
      frappe.prompt([{ fieldname: 'hint', fieldtype: 'Small Text', label: 'Optional instruction (leave blank to just retry)' }],
        v => {
          frappe.show_alert({ message: 'Regenerating §' + s.seq + '…', indicator: 'blue' });
          frappe.call({ method: 'lac_translation.api.regenerate', args: { segment: s.name, hint: v.hint || '' } })
            .then(r => { const d = r.message || {}; Object.assign(s, { ai_suggestion: d.mn, ai_alternative: d.alt, ai_rationale: d.notes, status: 'Suggested' }); renderAll(); frappe.show_alert({ message: 'Regenerated §' + s.seq, indicator: 'green' }); });
        }, 'Regenerate suggestion', 'Run');
    }
    else if (act === 'saveFinal') { const v = $id('tpFinal').value; const st = (v.trim() === (s.ai_suggestion || '').trim() || v.trim() === (s.draft_text || '').trim()) ? 'Accepted' : 'Edited'; save(s, { final_text: v, status: st }); frappe.show_alert({ message: 'Saved §' + s.seq, indicator: 'green' }); }
  });
  root.addEventListener('submit', e => {
    if (e.target.id === 'tpCmtForm') {
      e.preventDefault(); const inp = $id('tpCmtInput'); const val = inp.value.trim(); if (!val) return; const name = S.cur;
      frappe.call({ method: 'frappe.client.insert', args: { doc: { doctype: 'Comment', comment_type: 'Comment', reference_doctype: 'Translation Segment', reference_name: name, content: val } } }).then(() => { inp.value = ''; loadComments(name); });
    }
  });
  $id('tpSearch').addEventListener('input', e => { S.q = e.target.value; renderList(); });
  $id('tpBook').addEventListener('change', e => { S.project = e.target.value; const p = S.projects.find(x => x.name === S.project); S.glossary = p ? (p.glossary || '') : ''; S.cur = null; $id('tpDrawer').classList.remove('open'); loadSegs(); });
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
    else if (e.key === 'e' || e.key === 'E') { e.preventDefault(); $id('tpFinal')?.focus(); }
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
  $id('tpDocx').onclick = () => { if (!S.project) return; frappe.show_alert({ message: 'Building .docx…', indicator: 'blue' }); frappe.call({ method: 'lac_translation.api.export_docx', args: { project: S.project } }).then(r => { if (r.message && r.message.file_url) window.open(r.message.file_url, '_blank'); }); };
  $id('tpGen').onclick = () => { if (!S.project) return; frappe.confirm('Generate AI suggestions for all Pending segments in this book? (runs in the background)', () => { frappe.call({ method: 'lac_translation.api.generate', args: { project: S.project } }).then(() => frappe.show_alert({ message: 'Queued — suggestions will appear as it runs.', indicator: 'blue' })); }); };
  $id('tpImport').onclick = openImport;

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

  loadProjects();
};
