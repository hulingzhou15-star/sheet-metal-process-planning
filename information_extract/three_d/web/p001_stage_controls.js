 const phaseLabels={entry:'进入态',motion:'共同加工过程',completed:'完成态'};
 const stageAnalyses=new Map();
 const overlapKey='OVERLAP',unknownKey='UNKNOWN_KERNEL_FAILURE';
 const sampleRange=$('sample');
 let drawnPlan,drawnStation,drawnSample,playButton,playEnd;
 const element=(tag,text)=>Object.assign(document.createElement(tag),{textContent:text});
 const assignedPlan=()=>data.plans.find(p=>p.plan_id===$('plan').value);
 const activeReport=()=>data.replays[$('plan').value];
 const diagnostic=(counts)=>'重叠 '+(counts[overlapKey]||0)+' 对次 · 未判定 '+(counts[unknownKey]||0)+' 对次';
 const phaseOption=$('phase').querySelector('[value=motion]');
 const removalOperations=new Set(data.removal_coverage.map(o=>o.operation_id));
 const actionList=[...features.values()];

 function planAnalysis(plan){
  if(stageAnalyses.has(plan.plan_id))return stageAnalyses.get(plan.plan_id);
  const stations=new Map(data.stations.map(s=>[s.station_id,{samples:[],features:[],bendCount:0,overlap:0,unknown:0}]));
  const issues=[];
  const runs=data.replays[plan.plan_id].runs;
  runs.forEach((run,index)=>{
   const station=stations.get(run.station_id);
   station.samples.push(index);
   station.overlap+=run.counts[overlapKey]||0;
   station.unknown+=run.counts[unknownKey]||0;
   for(const issue of run.issues)issues.push({index,issue});
  });
  for(const action of actionList){
   const station=stations.get(plan.assignments[action.operation_id]);
   if(!station)continue;
   station.features.push(action);
   if(action.display_kind==='BEND_BAND')station.bendCount++;
  }
  const value={stations,issues};
  stageAnalyses.set(plan.plan_id,value);
  return value;
 }

 function calculate(){
  const plan=assignedPlan(),runs=activeReport().runs,station=planAnalysis(plan).stations.get($('station').value);
  const currentPick=Number(sampleRange.value),sampleIndex=station.samples.includes(currentPick)&&runs[currentPick].phase===$('phase').value
   ?currentPick:station.samples.find(i=>runs[i].phase===$('phase').value)??station.samples[0];
  const run=runs[sampleIndex],{station_id:sid,phase,tau}=run;
  const fr=B.fractions(model,plan.assignments,orders,sid,tau),ts=B.transforms(model,fr);
  sampleRange.max=runs.length-1;
  sampleRange.value=sampleIndex;
  $('phase').value=phase;
  phaseOption.disabled=station.samples.length<=2;
  current={plan,plan_id:plan.plan_id,station_id:sid,phase,tau,fr,ts,variant:variants.get(run.material_id),run,sampleIndex};
  updateHighlights();
 }

 function seekSample(index){
  const run=activeReport().runs[index];
  if(!run)return;
  sampleRange.max=activeReport().runs.length-1;
  sampleRange.value=index;
  $('station').value=run.station_id;
  $('phase').value=run.phase;
  interact3();
  render();
 }

 function stop(){
  playing=false;
  if(timer){if(!timer.win.closed)timer.win.cancelAnimationFrame(timer.id);timer=null;}
  $('play').textContent='播放本序';
  $('playAll').textContent='播放整案';
  $('play').setAttribute('aria-pressed','false');
  $('playAll').setAttribute('aria-pressed','false');
  playButton=null;
 }

 function scheduleTick(){
  if(timer&&!timer.win.closed)timer.win.cancelAnimationFrame(timer.id);
  const win=popouts[3]?.win||popouts[2]?.win||window;
  timer={win,id:win.requestAnimationFrame(tick)};
 }

 function tick(){
  timer=null;
  if(!playing)return;
  const now=performance.now();
  if(now-playStarted>=58){
   const runs=activeReport().runs,next=current.sampleIndex+1;
   const last=Math.min(next+Number($('speed').value)-1,playEnd);
   let index=next;
   while(index<last&&runs[index].phase==='motion')index++;
   seekSample(index);
   if(index===playEnd){stop();finishInteraction3();return;}
   playStarted=now;
  }
  scheduleTick();
 }

 function startPlayback(id){
  if(playing&&playButton===id){stop();return;}
  stop();
  const runs=activeReport().runs,station=planAnalysis(current.plan).stations.get(current.station_id);
  const start=id==='play'?station.samples[0]:0;
  playEnd=id==='play'?station.samples.at(-1):runs.length-1;
  seekSample(start);
  playing=true;
  playButton=id;
  playStarted=performance.now();
  $(id).textContent='暂停';
  $(id).setAttribute('aria-pressed','true');
  scheduleTick();
 }

 function goTo(index){stop();seekSample(index);}

 $('play').onclick=()=>startPlayback('play');
 $('playAll').onclick=()=>startPlayback('playAll');
 $('prevSample').onclick=()=>goTo(current.sampleIndex-1);
 $('nextSample').onclick=()=>goTo(current.sampleIndex+1);
 sampleRange.oninput=()=>goTo(Number(sampleRange.value));
 $('plan').onchange=()=>goTo(0);
 $('station').onchange=()=>goTo(planAnalysis(assignedPlan()).stations.get($('station').value).samples[0]);
 $('phase').onchange=()=>{
  const phase=$('phase').value,runs=activeReport().runs;
  const index=planAnalysis(assignedPlan()).stations.get($('station').value).samples.find(i=>runs[i].phase===phase);
  if(index!==undefined)goTo(index);
 };
 function seekFeatureEndpoint(phase){
  const station=current.plan.assignments[$('operation').value];
  if(!station)return;
  const samples=planAnalysis(current.plan).stations.get(station).samples;
  $('comparison').value='stage';
  focused=true;
  goTo(phase==='entry'?samples[0]:samples.at(-1));
 }
 for(const [id,phase] of [['beforeFeature','entry'],['afterFeature','completed']])$(id).onclick=()=>seekFeatureEndpoint(phase);
 for(const [id,phase] of [['cflat','entry'],['cformed','completed']])$(id).onclick=()=>{
  $('operation').value='O193';
  seekFeatureEndpoint(phase);
 };

 function focusIssue(index,issue){
  $('comparison').value='stage';
  focused=false;
  selectedFeature=false;
  goTo(index);
  const points=[];
  for(const hit of issue.component_overlaps){
   const bounds=hit.intersection_world_aabb_mm;
   if(bounds)points.push(bounds.slice(0,3),bounds.slice(3,6));
  }
  if(points.length){fit3(points,8);return;}
  for(const component of geometry.components){
   if(!issue.region_ids.includes(component.region_id))continue;
   const {lo,hi}=renderer.info(component.geometry_id,data.geometries);
   for(const x of [lo[0],hi[0]])for(const y of [lo[1],hi[1]])for(const z of [lo[2],hi[2]])points.push(B.point(component,[x,y,z],model,bends,current.fr,current.ts));
  }
  if(points.length)fit3(points,8);
 }

 function issueRow(index,issue,withPosition){
  const run=activeReport().runs[index],row=element('div','');
  row.className='issue-row';
  const unknown=issue.status===unknownKey,label=element('strong',unknown?'内核未判定':'正体积重叠');
  label.className=unknown?'unknown':'issue';
  row.append(label);
  if(withPosition)row.append(element('span',run.station_id+' · '+phaseLabels[run.phase]+' '+(100*run.tau).toFixed(1)+'%'));
  row.append(element('span',issue.region_ids.join(' / ')));
  if(issue.intersection_volume_mm3>0)row.append(element('span','已测交体积 '+issue.intersection_volume_mm3.toPrecision(5)+' mm³'));
  const action=element('button','定位板区');
  action.type='button';
  action.onclick=()=>focusIssue(index,issue);
  row.append(action);
  return row;
 }

 function refreshPlan(){
  const report=activeReport(),analysis=planAnalysis(current.plan);
  const score=current.plan.score;
  const scoreText=score?' · P5 '+score.p5.value.toFixed(3)+' · 次级目标 '+score.secondary.toFixed(3):'';
  $('planStats').textContent='候选 '+($('plan').selectedIndex+1)+' / '+data.plans.length+' · '+report.runs.length+' 个实际采样 · '+diagnostic(report.counts)+scoreText;
  $('rawReport').href=report.href;
  const summary=$('summaryReport');
  summary.hidden=!data.summary_href;
  if(data.summary_href)summary.href=data.summary_href;
  const material=$('materialReport');
  material.hidden=!report.material_href;
  if(report.material_href)material.href=report.material_href;
  const rows=[];
  for(const [station,row] of analysis.stations){
   const tr=element('tr','');
   tr.dataset.station=station;
   const cell=element('td',''),jump=element('button',station);
   jump.type='button';
   jump.onclick=()=>goTo(row.samples[0]);
   cell.append(jump);
   tr.append(cell,...[row.features.length,row.bendCount,row.samples.length,row.overlap,row.unknown].map(n=>element('td',n)));
   rows.push(tr);
  }
  $('stationRows').replaceChildren(...rows);
  $('planIssueSummary').textContent='整案干涉与未判定记录 · '+analysis.issues.length+' 条';
  if($('planIssuesPanel').open)refreshPlanIssues();
  else $('planIssues').replaceChildren();
 }

 function refreshPlanIssues(){
  const issues=planAnalysis(current.plan).issues;
  const rows=issues.map(({index,issue})=>issueRow(index,issue,true));
  $('planIssues').replaceChildren(...(rows.length?rows:[element('p','所检查的刚性板区对在已报告采样中无重叠和未判定记录。')]));
 }
 $('planIssuesPanel').ontoggle=()=>{if(current&&$('planIssuesPanel').open)refreshPlanIssues();};

 function describeStage(){
  const {plan_id:pid,station_id:sid,phase,tau,run,sampleIndex}=current;
  const report=activeReport(),analysis=planAnalysis(current.plan),mode=phaseLabels[phase];
  const progress=(100*tau).toFixed(1)+'%';
  const cut=current.variant.executed_removal_ids,formed=current.variant.executed_forming_ids;
  $('displayVersion').textContent=data.status_caption+' · 共同折弯进度 · '+report.step_count+' 分段';
  $('progress').textContent=(sampleIndex+1)+' / '+report.runs.length+' · '+progress;
  $('prevSample').disabled=sampleIndex===0;
  $('nextSample').disabled=sampleIndex===report.runs.length-1;
  $('state').textContent=pid+' · '+sid+' '+mode+' · 已去料 '+cut.length+'/'+removalOperations.size+' · 已局部成形 '+formed.length+'/'+localFeatures.size+' · '+diagnostic(run.counts);
  const active=analysis.stations.get(sid).features;
  const concurrent=active.some(o=>localFeatures.has(o.operation_id)||removalOperations.has(o.operation_id));
  $('trajectoryNotice').hidden=phase!=='motion'||!concurrent;
  const oid=$('operation').value,op=features.get(oid),local=localFeatures.get(oid),station=current.plan.assignments[oid];
  const comparison=$('comparison').value;
  let selectedText=oid+' · '+(labels[op?.operation_type]||op?.operation_type||'')+' · '+(station||'无工序');
  if(comparison==='whole-step')selectedText='右侧为完整 STEP 成品，左侧保留 '+sid+' 的展开材料。';
  else if(comparison==='step')selectedText+=' · STEP 局部成品对照';
  else if(local)selectedText+=' · '+(formed.includes(oid)?'已显示 STEP 局部成形端态':'进入态材料，尚未切换到 STEP 局部成形端态');
  else if(removalOperations.has(oid))selectedText+=' · '+(cut.includes(oid)?'已去料':'尚未去料');
  else if(op?.display_kind==='BEND_BAND')selectedText+=' · 有限宽度折弯带';
  else selectedText+=' · 仅定位';
  const dims=data.flange_dimensions.find(r=>r.operation_id===oid);
  if(dims&&comparison!=='whole-step')selectedText+=' · 突出板面 '+dims.protrusion_beyond_exit_skin_mm.toFixed(2)+' mm，含板厚总高 '+dims.total_plate_and_lip_height_mm.toFixed(2)+' mm，内/外半径 '+dims.inner_radius_mm.toFixed(2)+'/'+dims.outer_radius_mm.toFixed(2)+' mm';
  $('selectedInfo').textContent=selectedText;
  $('comparison').querySelector('[value=step]').disabled=!data.step_comparison[oid];
  $('beforeFeature').disabled=$('afterFeature').disabled=!station;
  if(drawnPlan!==pid){
   refreshPlan();
   $('coverage').textContent=(data.historic?'历史局部模型覆盖冲孔、预孔、折桥和翻孔。':'已建模的凸米和刻印与折桥、翻孔一起按 STEP 局部成品切换端态。')+'\n折弯带使用有限宽度几何回放。刚性板区体积检查仅覆盖已采样状态，折弯带未纳入全对检查。\n同序加工共享进度。冲切轨迹和连续局部变形未建模。连续路径、工具和装夹仍需独立验证。';
  }
  if(drawnPlan!==pid||drawnStation!==sid){
   $('featureSummary').textContent=sid+' 本序 '+active.length+' 个动作';
   $('featureList').replaceChildren(...active.map(o=>{
    const button=element('button',o.operation_id+' · '+(labels[o.operation_type]||o.operation_type)+' · '+o.source_feature_ref);
    button.className='feature-button';
    button.type='button';
    button.onclick=()=>{$('operation').value=o.operation_id;$('operation').onchange();};
    return button;
   }));
   for(const row of $('stationRows').children)row.dataset.active=String(row.dataset.station===sid);
  }
  if(drawnPlan!==pid||drawnSample!==sampleIndex){
   const rows=run.issues.map(issue=>issueRow(sampleIndex,issue,false));
   $('sampleIssues').replaceChildren(...(rows.length?rows:[element('p',run.pair_count+' 个刚性板区对在此采样无重叠和未判定记录。')]));
  }
  drawnPlan=pid;drawnStation=sid;drawnSample=sampleIndex;
  for(const popup of Object.values(popouts)){
   if(popup&&!popup.win.closed)popup.win.document.getElementById('popupStatus').textContent=pid+' · '+sid+' · '+mode+' · '+progress;
  }
 }

