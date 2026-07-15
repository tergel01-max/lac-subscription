frappe.pages['translation-portal'].on_page_load = function (wrapper) {
  const page = frappe.ui.make_app_page({
    parent: wrapper, title: 'Translation Portal', single_column: true,
  });

  const STATUS = ['Pending', 'Suggested', 'Accepted', 'Edited', 'Rejected'];
  const S = { projects: [], project: null, glossary: '', segs: [], cur: null, filter: 'All', q: '' };

  const esc = s => (s || '').replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));

  function diff(a, b) {
    const ow = (a || '').split(/\s+/).filter(Boolean), nw = (b || '').split(/\s+/).filter(Boolean);
    const m = ow.length, k = nw.length, dp = Array.from({ length: m + 1 }, () => new Array(k + 1).fill(0));
    for (let i = m - 1; i >= 0; i--) for (let j = k - 1; j >= 0; j--)
      dp[i][j] = ow[i] === nw[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
    let i = 0, j = 0, out = [];
    while (i < m && j < k) {
      if (ow[i] === nw[j]) { out.push(esc(ow[i])); i++; j++; }
      else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push('<del>' + esc(ow[i]) + '</del>'); i++; }
      else { out.push('<ins>' + esc(nw[j]) + '</ins>'); j++; }
    }
    while (i < m) { out.push('<del>' + esc(ow[i]) + '</del>'); i++; }
    while (j < k) { out.push('<ins>' + esc(nw[j]) + '</ins>'); j++; }
    return out.join(' ');
  }

  const shell = `
  <div class="tp" id="tpRoot">
    <div class="tp-top">
      <select class="tp-sel" id="tpBook"></select>
      <div class="tp-prog"><div class="row"><span>Finalized</span><b id="tpProgLabel">—</b></div>
        <div class="tp-track"><div class="tp-fill" id="tpProgFill" style="width:0"></div></div></div>
      <div class="tp-acts">
        <button class="tp-btn ghost" id="tpGloss">📑 Glossary</button>
        <button class="tp-btn ghost" id="tpRead">📖 Reading</button>
        <button class="tp-btn ghost" id="tpExport">⬇ Export</button>
        <button class="tp-btn ghost" id="tpTheme" title="Toggle theme">◐</button>
      </div>
    </div>
    <div class="tp-main">
      <aside class="tp-rail">
        <div class="tp-railhead">
          <div class="tp-chips" id="tpChips"></div>
          <div class="tp-search">
            <svg width="15" height="15" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><circle cx="7" cy="7" r="4.5"/><path d="M11 11l3 3"/></svg>
            <input id="tpSearch" placeholder="Search source or Mongolian…">
          </div>
        </div>
        <div class="tp-list" id="tpList"></div>
      </aside>
      <section class="tp-work"><div class="tp-inner" id="tpWork"></div></section>
    </div>
  </div>`;
  $(page.body).html(shell);
  const root = page.body.querySelector('#tpRoot');
  const $id = id => page.body.querySelector('#' + id);

  // theme: follow desk, allow toggle
  if ((document.documentElement.getAttribute('data-theme') || '') === 'dark'
    || document.body.classList.contains('dark')) root.classList.add('tp-dark');

  const color = st => 'var(--s-' + st + ')';
  function pill(st) {
    return `<span class="tp-pill" style="background:color-mix(in srgb, ${color(st)} 16%, transparent);color:${color(st)}">
      <span style="width:7px;height:7px;border-radius:99px;background:${color(st)}"></span>${st}</span>`;
  }

  // ---- data ----
  function loadProjects() {
    return frappe.db.get_list('Translation Project',
      { fields: ['name', 'title', 'glossary'], limit: 0, order_by: 'creation desc' })
      .then(r => {
        S.projects = r || [];
        const sel = $id('tpBook');
        sel.innerHTML = S.projects.map(p => `<option value="${p.name}">${esc(p.title || p.name)}</option>`).join('')
          || '<option>No projects</option>';
        if (S.projects.length) { S.project = S.projects[0].name; S.glossary = S.projects[0].glossary || ''; return loadSegs(); }
      });
  }
  function loadSegs() {
    return frappe.db.get_list('Translation Segment', {
      filters: { project: S.project },
      fields: ['name', 'seq', 'chapter', 'status', 'source_text', 'draft_text',
        'ai_suggestion', 'ai_alternative', 'ai_rationale', 'final_text', 'reviewer_comment'],
      order_by: 'seq asc', limit: 0,
    }).then(r => {
      S.segs = r || [];
      if (!S.segs.find(s => s.name === S.cur)) S.cur = S.segs.length ? S.segs[0].name : null;
      renderAll();
    });
  }

  // ---- render ----
  function counts() { const c = { All: S.segs.length }; STATUS.forEach(k => c[k] = S.segs.filter(s => s.status === k).length); return c; }
  function renderChips() {
    const c = counts(); const order = ['All', 'Pending', 'Suggested', 'Accepted', 'Edited'];
    $id('tpChips').innerHTML = order.map(k =>
      `<button class="tp-chip" data-f="${k}" aria-pressed="${S.filter === k}">${k} <span class="n">${c[k] || 0}</span></button>`).join('');
  }
  function visible() {
    return S.segs.filter(s => {
      if (S.filter !== 'All' && s.status !== S.filter) return false;
      if (S.q) { const q = S.q.toLowerCase(); return ((s.source_text || '') + ' ' + (s.draft_text || '') + ' ' + (s.final_text || s.ai_suggestion || '')).toLowerCase().includes(q); }
      return true;
    });
  }
  function renderList() {
    const vis = visible(); const groups = {}; const order = [];
    vis.forEach(s => { const ch = s.chapter || 'Book'; if (!groups[ch]) { groups[ch] = []; order.push(ch); } groups[ch].push(s); });
    let html = '';
    order.forEach(ch => {
      const done = groups[ch].filter(s => s.status === 'Accepted' || s.status === 'Edited').length;
      html += `<div class="tp-chapter"><span>${esc(ch)}</span><span>${done}/${groups[ch].length}</span></div>`;
      html += groups[ch].map(s => `
        <div class="tp-row ${s.name === S.cur ? 'active' : ''}" data-name="${s.name}">
          <div class="tp-dot" style="background:${color(s.status)}"></div>
          <div>
            <div class="seq">§${s.seq} · ${s.status}</div>
            <div class="src">${esc(s.source_text)}</div>
            <div class="mn">${esc(s.final_text || s.ai_suggestion || s.draft_text)}</div>
          </div>
        </div>`).join('');
    });
    $id('tpList').innerHTML = html || '<div style="padding:20px;color:var(--faint);font-size:13px">No segments match.</div>';
  }
  function renderProgress() {
    const done = S.segs.filter(s => s.status === 'Accepted' || s.status === 'Edited').length;
    const pct = S.segs.length ? Math.round(done * 100 / S.segs.length) : 0;
    $id('tpProgLabel').textContent = `${done} / ${S.segs.length} · ${pct}%`;
    $id('tpProgFill').style.width = pct + '%';
  }
  function card(cls, label, inner, extra = '') {
    return `<div class="tp-card ${cls}"><div class="lbl"><span class="name">${label}</span>${extra}</div>${inner}</div>`;
  }
  function renderWork() {
    const s = S.segs.find(x => x.name === S.cur); const w = $id('tpWork');
    if (!s) { w.innerHTML = '<div style="color:var(--faint);padding:40px 0">Select a segment.</div>'; return; }
    let ai;
    if (s.ai_suggestion) {
      const changed = (s.ai_suggestion || '').trim() !== (s.draft_text || '').trim();
      let inner = `<div class="body"><div class="diff">${changed ? diff(s.draft_text, s.ai_suggestion) : '<span class="nochange">No change — AI kept the draft.</span>'}</div></div>`;
      if (s.ai_rationale) inner += `<div class="notes"><svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor"><path d="M8 1l2 4 4 .6-3 3 .7 4L8 14.8 4.3 16.7l.7-4-3-3 4-.6z"/></svg><span>${esc(s.ai_rationale)}</span></div>`;
      inner += `<div class="cta"><button class="tp-btn primary" data-act="acceptAI">✔ Accept suggestion</button>`;
      if (s.ai_alternative) inner += `<button class="alt-toggle" data-act="toggleAlt">▾ Faithful alternative</button>`;
      inner += `</div>`;
      if (s.ai_alternative) inner += `<div class="alt" id="tpAlt"><div class="body" style="border-top:1px solid var(--border)">${esc(s.ai_alternative)}</div><div class="cta"><button class="tp-btn" data-act="useAlt">Use alternative</button></div></div>`;
      ai = card('ai', 'AI suggestion', inner);
    } else {
      ai = card('ai', 'AI suggestion', '<div class="body"><span class="nochange">Not generated yet — run the AI pass.</span></div>');
    }
    w.innerHTML = `
      <div class="tp-seghead"><h2>${esc(s.chapter || 'Book')} · Segment §${s.seq}</h2>${pill(s.status)}</div>
      ${card('en', 'Original · English', `<div class="body">${esc(s.source_text)}</div>`)}
      ${card('', 'Translator draft · Mongolian', `<div class="body">${esc(s.draft_text)}</div>`, '<button class="tp-btn ghost" data-act="useDraft" style="padding:3px 9px;font-size:12px">Keep draft</button>')}
      ${ai}
      ${card('final', 'Final · accepted text', `<div class="body"><textarea id="tpFinal" placeholder="Accept a version above, or type the final Mongolian…">${esc(s.final_text)}</textarea></div>`, '<button class="tp-btn primary" data-act="saveFinal" style="padding:4px 12px;font-size:12px">Save</button>')}
      ${card('comments', 'Comments', `<div class="body" id="tpCmts"><div style="color:var(--faint);font-size:13px">Loading…</div><form class="cmt-form" id="tpCmtForm"><input id="tpCmtInput" placeholder="Add a note for the team…"><button class="tp-btn" type="submit">Post</button></form></div>`)}
      <div class="tp-nav">
        <button class="tp-btn ghost" data-act="prev">← Previous</button>
        <span class="kbd"><b>J</b>/<b>K</b> move · <b>A</b> accept · <b>E</b> edit</span>
        <button class="tp-btn ghost" data-act="next">Next →</button>
      </div>`;
    loadComments(s.name);
  }
  function renderAll() { renderChips(); renderList(); renderProgress(); renderWork(); }

  // ---- comments ----
  function loadComments(name) {
    frappe.db.get_list('Comment', {
      filters: { reference_doctype: 'Translation Segment', reference_name: name, comment_type: 'Comment' },
      fields: ['content', 'comment_by', 'creation'], order_by: 'creation asc', limit: 0,
    }).then(rows => {
      const box = $id('tpCmts'); if (!box) return;
      const form = box.querySelector('#tpCmtForm');
      const list = (rows || []).map(c => `<div class="cmt"><div class="avatar">${esc((c.comment_by || '?')[0].toUpperCase())}</div>
        <div><div class="who"><b>${esc(c.comment_by || '')}</b> · ${frappe.datetime.comment_when ? frappe.datetime.comment_when(c.creation) : ''}</div>
        <div class="txt">${esc((c.content || '').replace(/<[^>]+>/g, ''))}</div></div></div>`).join('')
        || '<div style="color:var(--faint);font-size:13px">No comments yet.</div>';
      box.innerHTML = list; box.appendChild(form);
    });
  }

  // ---- actions ----
  function save(seg, fields) {
    Object.assign(seg, fields);
    renderAll();
    return frappe.db.set_value('Translation Segment', seg.name, fields)
      .catch(e => frappe.msgprint('Save failed: ' + (e.message || e)));
  }
  function accept(seg, txt) { save(seg, { final_text: txt, status: 'Accepted' }); frappe.show_alert({ message: 'Accepted §' + seg.seq, indicator: 'green' }); }
  function move(delta) {
    const vis = visible(); if (!vis.length) return;
    let i = vis.findIndex(s => s.name === S.cur); i = Math.max(0, Math.min(vis.length - 1, (i < 0 ? 0 : i) + delta));
    S.cur = vis[i].name; renderList(); renderWork();
    root.querySelector('.tp-row.active')?.scrollIntoView({ block: 'nearest' });
  }

  root.addEventListener('click', e => {
    const chip = e.target.closest('.tp-chip'); if (chip) { S.filter = chip.dataset.f; renderChips(); renderList(); return; }
    const row = e.target.closest('.tp-row'); if (row) { S.cur = row.dataset.name; renderList(); renderWork(); return; }
    const act = e.target.closest('[data-act]')?.dataset.act; if (!act) return;
    const s = S.segs.find(x => x.name === S.cur); if (!s) return;
    if (act === 'acceptAI') accept(s, s.ai_suggestion);
    else if (act === 'useAlt') accept(s, s.ai_alternative);
    else if (act === 'useDraft') accept(s, s.draft_text);
    else if (act === 'toggleAlt') $id('tpAlt')?.classList.toggle('open');
    else if (act === 'saveFinal') {
      const v = $id('tpFinal').value;
      const st = (v.trim() === (s.ai_suggestion || '').trim() || v.trim() === (s.draft_text || '').trim()) ? 'Accepted' : 'Edited';
      save(s, { final_text: v, status: st }); frappe.show_alert({ message: 'Saved §' + s.seq, indicator: 'green' });
    }
    else if (act === 'prev') move(-1);
    else if (act === 'next') move(1);
  });
  root.addEventListener('submit', e => {
    if (e.target.id === 'tpCmtForm') {
      e.preventDefault(); const inp = $id('tpCmtInput'); const val = inp.value.trim(); if (!val) return;
      const name = S.cur;
      frappe.call({ method: 'frappe.client.insert', args: { doc: { doctype: 'Comment', comment_type: 'Comment', reference_doctype: 'Translation Segment', reference_name: name, content: val } } })
        .then(() => { inp.value = ''; loadComments(name); });
    }
  });
  $id('tpSearch').addEventListener('input', e => { S.q = e.target.value; renderList(); });
  $id('tpBook').addEventListener('change', e => {
    S.project = e.target.value; const p = S.projects.find(x => x.name === S.project); S.glossary = p ? (p.glossary || '') : ''; S.cur = null; loadSegs();
  });
  document.addEventListener('keydown', e => {
    if (!wrapper.contains(document.activeElement) && document.activeElement !== document.body) return;
    if (/input|textarea|select/i.test((document.activeElement || {}).tagName || '')) return;
    if (!page.wrapper.is(':visible')) return;
    if (e.key === 'j' || e.key === 'J') move(1);
    else if (e.key === 'k' || e.key === 'K') move(-1);
    else if (e.key === 'a' || e.key === 'A') { const s = S.segs.find(x => x.name === S.cur); if (s && s.ai_suggestion) accept(s, s.ai_suggestion); }
    else if (e.key === 'e' || e.key === 'E') { e.preventDefault(); $id('tpFinal')?.focus(); }
  });

  // ---- overlays ----
  function overlay(title, bodyHtml) {
    let ov = document.querySelector('#tpOverlay');
    if (!ov) { ov = document.createElement('div'); ov.id = 'tpOverlay'; ov.className = 'tp-overlay'; document.body.appendChild(ov); ov.addEventListener('click', e => { if (e.target === ov) ov.classList.remove('open'); }); }
    if (root.classList.contains('tp-dark')) ov.classList.add('tp-dark'); else ov.classList.remove('tp-dark');
    ov.innerHTML = `<div class="tp-modal"><header><h3>${esc(title)}</h3><button class="tp-btn ghost" id="tpOvClose">Close</button></header><div class="scroll">${bodyHtml}</div></div>`;
    ov.querySelector('#tpOvClose').onclick = () => ov.classList.remove('open');
    ov.classList.add('open');
  }
  $id('tpRead').onclick = () => overlay('Reading view — ' + (S.projects.find(p => p.name === S.project)?.title || ''),
    S.segs.map(s => `<p>${esc(s.final_text || s.ai_suggestion || s.draft_text)}</p>`).join(''));
  $id('tpGloss').onclick = () => overlay('Glossary / style guide', '<pre>' + esc(S.glossary || 'No glossary set for this project.') + '</pre>');
  $id('tpExport').onclick = () => {
    const txt = S.segs.map(s => s.final_text || s.ai_suggestion || s.draft_text || '').join('\n');
    const b = new Blob([txt], { type: 'text/plain;charset=utf-8' }); const a = document.createElement('a');
    a.href = URL.createObjectURL(b); a.download = (S.projects.find(p => p.name === S.project)?.title || 'book') + '_MN.txt'; a.click();
  };
  $id('tpTheme').onclick = () => root.classList.toggle('tp-dark');

  loadProjects();
};
