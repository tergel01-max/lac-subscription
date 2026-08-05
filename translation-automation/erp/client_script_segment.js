// Client Script: "Translation Segment Review UX" (DocType: Translation Segment, view: Form)
// Reviewer workspace for one sentence: word-diff of draft vs AI, one-click
// accept buttons, and auto-marking manual edits as "Edited".
// This is the source of truth; it is installed in ERP as a Client Script record.

function _esc(s){return frappe.utils.escape_html(s||'');}

// word-level LCS diff -> red strikethrough = removed, green = added
function _worddiff(oldStr,newStr){
  var o=(oldStr||'').split(/\s+/).filter(Boolean);
  var n=(newStr||'').split(/\s+/).filter(Boolean);
  var m=o.length,k=n.length;
  var dp=[];for(var a=0;a<=m;a++){dp.push(new Array(k+1).fill(0));}
  for(var i=m-1;i>=0;i--){for(var j=k-1;j>=0;j--){dp[i][j]=o[i]===n[j]?dp[i+1][j+1]+1:Math.max(dp[i+1][j],dp[i][j+1]);}}
  var i2=0,j2=0,out=[];
  while(i2<m&&j2<k){
    if(o[i2]===n[j2]){out.push(_esc(o[i2]));i2++;j2++;}
    else if(dp[i2+1][j2]>=dp[i2][j2+1]){out.push("<span style='background:#ffd6d6;text-decoration:line-through'>"+_esc(o[i2])+"</span>");i2++;}
    else{out.push("<span style='background:#c9f7c9'>"+_esc(n[j2])+"</span>");j2++;}
  }
  while(i2<m){out.push("<span style='background:#ffd6d6;text-decoration:line-through'>"+_esc(o[i2])+"</span>");i2++;}
  while(j2<k){out.push("<span style='background:#c9f7c9'>"+_esc(n[j2])+"</span>");j2++;}
  return out.join(' ');
}

function _render_diff(frm){
  var f=frm.fields_dict.changes_html;if(!f||!f.$wrapper)return;
  if(!frm.doc.ai_suggestion){f.$wrapper.html("<div class='text-muted'>No AI suggestion yet.</div>");return;}
  var body=_worddiff(frm.doc.draft_text,frm.doc.ai_suggestion);
  var same=(frm.doc.draft_text||'').trim()===(frm.doc.ai_suggestion||'').trim();
  var head=same?"<div style='color:#888;margin-bottom:4px'>No changes — AI kept the draft.</div>":"<div style='margin-bottom:4px'><span style='background:#ffd6d6;text-decoration:line-through'>removed</span> &nbsp; <span style='background:#c9f7c9'>added</span></div>";
  f.$wrapper.html(head+"<div style='line-height:1.9;font-size:1.05em'>"+body+"</div>");
}

function _accept(frm,val){frm.set_value('final_text',val);frm.set_value('status','Accepted');frm.save();}

frappe.ui.form.on('Translation Segment',{
  refresh:function(frm){
    _render_diff(frm);
    if(frm.doc.ai_suggestion){frm.add_custom_button('✔ Accept AI',function(){_accept(frm,frm.doc.ai_suggestion);}).addClass('btn-primary');}
    if(frm.doc.ai_alternative){frm.add_custom_button('Use alternative',function(){_accept(frm,frm.doc.ai_alternative);});}
    if(frm.doc.draft_text){frm.add_custom_button('Use draft',function(){_accept(frm,frm.doc.draft_text);});}
    if(frm.doc.project){frm.add_custom_button('← Back to book',function(){frappe.set_route('Form','Translation Project',frm.doc.project);});}
  },
  ai_suggestion:function(frm){_render_diff(frm);},
  draft_text:function(frm){_render_diff(frm);},
  final_text:function(frm){
    var f=(frm.doc.final_text||'').trim();
    if(f && f!==(frm.doc.ai_suggestion||'').trim() && f!==(frm.doc.draft_text||'').trim() && f!==(frm.doc.ai_alternative||'').trim() && frm.doc.status!=='Accepted'){
      frm.set_value('status','Edited');
    }
  }
});
