// Client Script: "Translation Project Book View" (DocType: Translation Project, view: Form)
// Book-level editorial dashboard: progress bar, a clickable Original/Draft/Final
// table, accept-all-unchanged, a reading view, and export. Source of truth for
// the Client Script record installed in ERP.

function _tp_fetch(frm){
  return frappe.db.get_list('Translation Segment',{
    filters:{project:frm.doc.name},
    fields:['name','seq','status','source_text','draft_text','ai_suggestion','final_text'],
    limit:0, order_by:'seq asc'
  });
}

function _tp_render(frm){
  var w=frm.fields_dict.book_view_html;if(!w||!w.$wrapper)return;
  w.$wrapper.html("<div class='text-muted'>Loading…</div>");
  _tp_fetch(frm).then(function(segs){
    var col={Pending:'#b0b0b0',Suggested:'#f0a500',Accepted:'#2e9e5b',Edited:'#008eaa',Rejected:'#d9534f'};
    var counts={Pending:0,Suggested:0,Accepted:0,Edited:0,Rejected:0};
    segs.forEach(function(s){counts[s.status]=(counts[s.status]||0)+1;});
    var total=segs.length||1,done=(counts.Accepted||0)+(counts.Edited||0),pct=Math.round(done*100/total);
    var legend=Object.keys(counts).map(function(k){return `<span style='margin-right:12px;white-space:nowrap'><span style='display:inline-block;width:10px;height:10px;background:${col[k]};border-radius:2px;margin-right:4px'></span>${k}: ${counts[k]}</span>`;}).join('');
    var bar=`<div style='margin-bottom:10px'><b>${done} / ${segs.length} finalized (${pct}%)</b><div style='height:10px;background:#eee;border-radius:5px;overflow:hidden;margin:4px 0'><div style='height:10px;width:${pct}%;background:#2e9e5b'></div></div><div style='font-size:12px'>${legend}</div></div>`;
    var rows=segs.map(function(s){var fin=s.final_text||s.ai_suggestion||s.draft_text||'';return `<tr data-name='${s.name}' style='cursor:pointer;border-bottom:1px solid #eee'><td style='padding:5px;color:#888;vertical-align:top'>${s.seq}</td><td style='padding:5px;vertical-align:top'>${frappe.utils.escape_html(s.source_text||'')}</td><td style='padding:5px;vertical-align:top;color:#777'>${frappe.utils.escape_html(s.draft_text||'')}</td><td style='padding:5px;vertical-align:top;border-left:3px solid ${col[s.status]||'#ccc'}'>${frappe.utils.escape_html(fin)}</td></tr>`;}).join('');
    var table=`<div style='max-height:600px;overflow:auto;border:1px solid #eee;border-radius:6px'><table style='width:100%;border-collapse:collapse;font-size:13px;table-layout:fixed'><thead><tr style='background:#f7f9fa'><th style='padding:6px;text-align:left;width:36px'>#</th><th style='padding:6px;text-align:left'>Original (EN)</th><th style='padding:6px;text-align:left'>Translator draft</th><th style='padding:6px;text-align:left'>Final / best</th></tr></thead><tbody>${rows}</tbody></table></div>`;
    w.$wrapper.html(bar+table);
    w.$wrapper.find('tr[data-name]').on('click',function(){frappe.set_route('Form','Translation Segment',$(this).attr('data-name'));});
  });
}

function _tp_accept_unchanged(frm){
  _tp_fetch(frm).then(function(segs){
    var todo=segs.filter(function(s){return s.status==='Suggested'&&(s.ai_suggestion||'').trim()!==''&&(s.ai_suggestion||'').trim()===(s.draft_text||'').trim();});
    if(!todo.length){frappe.msgprint('No unchanged suggestions to accept.');return;}
    frappe.confirm('Accept '+todo.length+' unchanged suggestion(s) as Final?',function(){
      Promise.all(todo.map(function(s){return frappe.db.set_value('Translation Segment',s.name,{final_text:s.ai_suggestion||s.draft_text,status:'Accepted'});})).then(function(){frappe.show_alert('Accepted '+todo.length);_tp_render(frm);});
    });
  });
}

function _tp_reading(frm){
  _tp_fetch(frm).then(function(segs){
    var html=segs.map(function(s){return `<p style='margin:0 0 12px'>${frappe.utils.escape_html(s.final_text||s.ai_suggestion||s.draft_text||'')}</p>`;}).join('');
    var d=new frappe.ui.Dialog({title:'Reading view',size:'large'});
    d.$body.html(`<div style='font-family:Georgia,serif;font-size:15px;line-height:1.85;max-height:70vh;overflow:auto;padding:6px'>${html}</div>`);
    d.show();
  });
}

function _tp_export(frm){
  _tp_fetch(frm).then(function(segs){
    var txt=segs.map(function(s){return s.final_text||s.ai_suggestion||s.draft_text||'';}).join('\n');
    var blob=new Blob([txt],{type:'text/plain;charset=utf-8'});
    var a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=(frm.doc.title||'book')+'_MN.txt';a.click();
  });
}

frappe.ui.form.on('Translation Project',{
  refresh:function(frm){
    if(frm.is_new())return;
    frm.add_custom_button('↻ Refresh view',function(){_tp_render(frm);});
    frm.add_custom_button('✔ Accept all unchanged',function(){_tp_accept_unchanged(frm);});
    frm.add_custom_button('📖 Reading view',function(){_tp_reading(frm);});
    frm.add_custom_button('⬇ Export final',function(){_tp_export(frm);});
    _tp_render(frm);
  }
});
