/* Offline human answers only. Frozen comparisons and A/B orientation stay unchanged. */
const ReviewAnswers = {
  columns: ['comparison_id','plan_A','plan_B','answer','confidence','reason_note'],
  rows(comparisons, drafts={}) {
    return comparisons.map(pair=> {
      const draft=drafts[pair.comparison_id] || {};
      return {comparison_id:pair.comparison_id,plan_A:pair.blind_plan_A,plan_B:pair.blind_plan_B,
        answer:['A','B','C','D','E'].includes(draft.answer)?draft.answer:'',
        confidence:['1','2','3','4','5'].includes(draft.confidence)?draft.confidence:'',
        reason_note:typeof draft.reason_note==='string'?draft.reason_note:''};
    });
  },
  csv(rows) {
    const cell=value=> '"'+String(value ?? '').replace(/"/g,'""')+'"';
    return '\uFEFF'+[this.columns.join(','),...rows.map(row=>this.columns.map(key=>cell(row[key])).join(','))].join('\r\n')+'\r\n';
  },
  signature(data) {
    // Public assignments distinguish batches even if their anonymous IDs are reused.
    return JSON.stringify([data.comparisons.map(p=>[p.comparison_id,p.blind_plan_A,p.blind_plan_B]),
      Object.keys(data.plans).sort().map(id=>[id,Object.keys(data.plans[id].operations).sort()
        .map(op=>[op,data.plans[id].operations[op].assigned_station])]),
      data.comparisons.some(p=>p.debug)?'private':'public']);
  },
  storageKey(signature) {
    let hash=2166136261;
    for(let i=0;i<signature.length;i++)hash=Math.imul(hash^signature.charCodeAt(i),16777619);
    return 'process-review-answers-v1-'+(hash>>>0).toString(16);
  },
  mount(data, doc, env=globalThis) {
    if(!data.comparisons.length)return null;
    const signature=this.signature(data), key=this.storageKey(signature);
    const get=id=>doc.getElementById(id), blocks=[...doc.querySelectorAll('[data-comparison-details]')];
    const forms=new Map(data.comparisons.map(pair=> {
      const block=blocks.find(node=>node.dataset.comparisonDetails===pair.comparison_id);
      const name=suffix=>suffix+'-'+pair.comparison_id;
      return [pair.comparison_id,{block,
        answers:[...block.querySelectorAll('input')].filter(n=>n.getAttribute('name')===name('answer')),
        confidence:[...block.querySelectorAll('select')].find(n=>n.getAttribute('name')===name('confidence')),
        note:[...block.querySelectorAll('textarea')].find(n=>n.getAttribute('name')===name('reason-note'))}];
    }));
    const collect=()=>this.rows(data.comparisons,Object.fromEntries([...forms].map(([id,form])=>[id,{
      answer:(form.answers.find(n=>n.checked)||{}).value || '',
      confidence:form.confidence.value,reason_note:form.note.value}])));
    let storage=null, savedOkay=false, attemptedSave=false, warning='', exportMessage='';
    const update=()=> {
      const rows=collect();
      get('review-progress').textContent='已判断 '+rows.filter(r=>r.answer).length+' / '+rows.length+' 对';
      get('review-save-status').textContent=warning || (savedOkay
        ?'已在当前浏览器本地暂存；完成后请导出 CSV 返回。'
        :attemptedSave?'浏览器未能本地暂存；请及时导出 CSV，关闭页面前务必导出。'
        :'填写时会尝试在当前浏览器本地暂存；完成后请导出 CSV 返回。');
      get('review-export-status').textContent=exportMessage;
      for(const button of get('comparison-nav').querySelectorAll('[data-comparison-index]')) {
        const row=rows[Number(button.dataset.comparisonIndex)];
        button.classList.toggle('review-answered',Boolean(row.answer));
        button.setAttribute('aria-label',row.comparison_id+(row.answer?' · 已判断':' · 未判断'));
      }
    };
    try {
      storage=env.localStorage;
      if(!storage)throw new Error('Storage unavailable');
      const source=storage.getItem(key);
      if(source) {
        const saved=JSON.parse(source);
        if(saved.version===1 && saved.signature===signature && Array.isArray(saved.rows)) {
          for(const pair of data.comparisons) {
            const record=saved.rows.find(row=>row && row.comparison_id===pair.comparison_id
              && row.plan_A===pair.blind_plan_A && row.plan_B===pair.blind_plan_B);
            if(!record)continue;
            const row=this.rows([pair],{[pair.comparison_id]:record})[0], form=forms.get(pair.comparison_id);
            for(const radio of form.answers)radio.checked=radio.value===row.answer;
            form.confidence.value=row.confidence;form.note.value=row.reason_note;
          }
        } else warning='暂存记录与本包不一致，未恢复；请在当前页面填写并导出 CSV。';
      }
      savedOkay=Boolean(source);
    } catch(error) {
      warning='未能读取本地暂存；本页仍可填写和导出 CSV。关闭页面前务必导出。';
    }
    const save=()=> {
      warning='';exportMessage='';attemptedSave=true;
      try {
        if(!storage)throw new Error('Storage unavailable');
        storage.setItem(key,JSON.stringify({version:1,signature,rows:collect()}));savedOkay=true;
      } catch(error) {savedOkay=false;}
      update();
    };
    const edited=event=> {
      const block=event.target.closest('[data-comparison-details]');
      if(block && forms.has(block.dataset.comparisonDetails) && ['INPUT','SELECT','TEXTAREA'].includes(event.target.tagName.toUpperCase()))save();
    };
    doc.addEventListener('input',edited);doc.addEventListener('change',edited);
    get('review-focus-form').addEventListener('click',()=> {
      const form=[...forms.values()].find(f=>!f.block.hidden);
      if(form){form.answers[0].closest('.form').scrollIntoView({behavior:'smooth',block:'center'});form.answers[0].focus({preventScroll:true});}
    });
    get('review-export-csv').addEventListener('click',()=> {
      save();
      let url=null;
      try {
        const blob=new env.Blob([this.csv(collect())],{type:'text/csv;charset=utf-8'});
        url=env.URL.createObjectURL(blob);
        const link=doc.createElement('a');link.href=url;link.download='comparison_pairs_completed.csv';
        doc.body.append(link);link.click();link.remove();
        env.setTimeout(()=>env.URL.revokeObjectURL(url),1000);
        exportMessage='已发起 CSV 下载；请确认下载文件，返回 comparison_pairs_completed.csv。';
      } catch(error) {
        if(url)env.URL.revokeObjectURL(url);
        exportMessage='CSV 下载失败，请保持页面打开后重试。';
      }
      update();
    });
    update();
    return {collect,save,storageKey:key};
  }
};
if(typeof module!=='undefined')module.exports=ReviewAnswers;
else if(typeof document!=='undefined') {
  const reviewData=document.getElementById('visual-data');
  if(reviewData)ReviewAnswers.mount(JSON.parse(reviewData.textContent),document);
}
