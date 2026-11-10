(function(root){
  'use strict';
  const keys=['part_id','asset_id','topology_fingerprint','facts_fingerprint','review_source_fingerprint'];
  const statuses=['UNREVIEWED','SAME_FEATURE','DIFFERENT_FEATURE','UNSURE'];
  const material=['UNSPECIFIED','PUNCH_REMOVES_ALL','RETAINED_MATERIAL_FOLDS','CROSSES_PANELS','OTHER'];
  function empty(data){return {schema_version:1,review_kind:'ordinary_punch_mapping',...Object.fromEntries(keys.map(k=>[k,data[k]])),records:{}};}
  function blank(){return {judgment:'UNREVIEWED',material:'UNSPECIFIED',face_ids:[],note:'',cad_wire_id:null};}
  function validate(raw,data){
    if(!raw||raw.schema_version!==1||raw.review_kind!=='ordinary_punch_mapping'||keys.some(k=>raw[k]!==data[k]))throw Error('审核文件的零件、CAD 或诊断版本不同。');
    if(!raw.records||typeof raw.records!=='object'||Array.isArray(raw.records))throw Error('审核记录格式无效。');
    const ids=new Set(data.operations.map(o=>o.operation_id)),allowedFaces=new Set(data.faces.map(f=>f.face_id));
    for(const [id,r] of Object.entries(raw.records)){
      if(!ids.has(id)||!r||!statuses.includes(r.judgment)||!material.includes(r.material)||typeof r.note!=='string'||r.note.length>20000)throw Error('存在无效动作、审核选项或备注。');
      if(!Array.isArray(r.face_ids)||r.face_ids.length!==new Set(r.face_ids).size||r.face_ids.some(f=>!Number.isInteger(f)||!allowedFaces.has(f)))throw Error('CAD 面编号无效或重复。');
      const op=data.operations.find(o=>o.operation_id===id);
      if(r.cad_wire_id!==null&&!op.candidates.some(c=>c.wire_id===r.cad_wire_id))throw Error('孔边编号与动作诊断不符。');
    }
    return JSON.parse(JSON.stringify(raw));
  }
  function storageKey(data){return 'ie:ordinary-punch-review:v1:'+data.review_source_fingerprint;}
  const api={empty,blank,validate,storageKey};root.MappingReviewCore=api;
  if(typeof module!=='undefined')module.exports=api;
})(globalThis);
