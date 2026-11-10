/* Pure display functions are also exercised without a browser. */
const VisualLogic = {
  displayPoint(point,rotation) {
    if(rotation===0)return [...point];
    if(rotation===270)return [-point[1],point[0]];
    throw new Error('Unsupported display orientation');
  },
  displayBox(box,rotation) {
    const [x,y,w,h]=box, points=[[x,y],[x+w,y],[x,y+h],[x+w,y+h]].map(p=>this.displayPoint(p,rotation));
    const xs=points.map(p=>p[0]),ys=points.map(p=>p[1]);
    return [Math.min(...xs),Math.min(...ys),Math.max(...xs)-Math.min(...xs),Math.max(...ys)-Math.min(...ys)];
  },
  matrix(rotation) {return rotation===270?'matrix(0 1 -1 0 0 0)':'matrix(1 0 0 1 0 0)';},
  fitRegion(box,rotation,base,config) {
    const padding=Math.max(box[2]-box[0],box[3]-box[1],config.svg.minimum_extent)*config.svg.padding_ratio;
    const target=this.displayBox([box[0]-padding,-box[3]-padding,Math.max(box[2]-box[0],config.svg.minimum_extent)+2*padding,Math.max(box[3]-box[1],config.svg.minimum_extent)+2*padding],rotation);
    const ratio=base[2]/base[3],w=Math.max(target[2],target[3]*ratio);
    const center=[target[0]+target[2]/2,target[1]+target[3]/2];
    const width=Math.min(base[2]/config.interaction.minimum_zoom,Math.max(w,base[2]/config.interaction.maximum_zoom));
    return [center[0]-width/2,center[1]-width/ratio/2,width,width/ratio];
  },
  status(row, rank, mode) {
    if (mode === 'FULL_PLAN_BY_STATION') return 'full';
    if (row.station_rank === rank) return 'current';
    if (mode === 'CUMULATIVE' && row.station_rank < rank) return 'previous';
    return 'inactive';
  },
  focus(id, geometry, changed, onlyDiff, region) {
    if (onlyDiff && !changed.has(id)) return 'unchanged';
    if (region === 'ALL' || geometry.membership[id] === region) return 'primary';
    const selected = geometry.regions[region];
    if (selected) {
      if (selected.bend_operation_id === id) return 'primary';
      for (const [rid, r] of Object.entries(geometry.regions)) {
        if (r.bend_operation_id === id && (selected.ancestor_region_ids.includes(rid) || r.ancestor_region_ids.includes(region))) return 'related';
      }
    }
    return 'filtered';
  },
  style(row, rank, config, mode, colorMode, focus, selected) {
    const s=config.svg, c=config.colors, status=this.status(row,rank,mode);
    let opacity=s.background_opacity, color=c.background;
    if (status==='full') {opacity=s.current_stage_opacity; color=c.station_palette[row.station_rank%c.station_palette.length];}
    else if (status==='current') {opacity=s.current_stage_opacity; color=c.current;}
    else if (status==='previous') {opacity=s.previous_stage_opacity; color=c.previous;}
    else if (mode==='CUMULATIVE') opacity=s.future_stage_opacity;
    if (colorMode==='BY_OPERATION_TYPE' && status!=='inactive' && status!=='full') color=c.operation_types[row.operation_type];
    if (focus==='unchanged') opacity=s.unchanged_opacity;
    else if (focus==='filtered') opacity=s.filtered_opacity;
    else if (focus==='related') opacity=s.related_bend_opacity;
    let width=row.operation_type==='BEND' ? (status==='current'?s.bend_current_stroke_width:s.bend_normal_stroke_width) : s.normal_stroke_width;
    if(status==='current' && row.operation_type!=='BEND') width=s.current_stroke_width;
    if (selected && focus==='primary') {color=c.selected; width=s.selected_stroke_width; opacity=s.current_stage_opacity;}
    return {status,focus,stroke:color,stroke_width:width,opacity};
  },
  stageRows(view, station, geometry, changed, onlyDiff, region) {
    const all=Object.values(view.operations).filter(r=>r.assigned_station===station);
    return {all, shown:all.filter(r=>this.focus(r.operation_id,geometry,changed,onlyDiff,region)==='primary')};
  },
  zoom(view, base, factor, limits, anchor) {
    const current=base[2]/view[2];
    const next=Math.max(limits.minimum_zoom,Math.min(limits.maximum_zoom,current*factor));
    const ratio=current/next;
    const center=anchor || [view[0]+view[2]/2,view[1]+view[3]/2];
    return [center[0]-(center[0]-view[0])*ratio,center[1]-(center[1]-view[1])*ratio,view[2]*ratio,view[3]*ratio];
  }
};
if (typeof module !== 'undefined') module.exports=VisualLogic;

if (typeof document !== 'undefined') (() => {
  'use strict';
  const data=JSON.parse(document.getElementById('visual-data').textContent);
  const geometry=data.geometry, config=data.config, stations=data.station_ids;
  const get=id=>document.getElementById(id);
  let itemIndex=0, stationIndex=0, mode=config.display.default_mode, colorMode=config.display.default_color_mode;
  let onlyDiff=config.display.difference_only_default && data.comparisons.length>0;
  let showRegions=config.display.show_influence_region_default, region='ALL', selected=null;
  let orientation=config.display.default_rotation_degrees;
  let baseBox=VisualLogic.displayBox(geometry.view_box,orientation);
  let viewport=[...baseBox], pan=null, justPanned=false;
  const canvases=[get('canvas-A'),get('canvas-B')].filter(Boolean);
  const active=()=>data.comparisons.length ? data.comparisons[itemIndex] : null;
  const views=()=> active() ? [data.plans[active().blind_plan_A],data.plans[active().blind_plan_B]] : [data.plans[data.single_plan]];
  const changed=()=>new Set(active() ? active().diff.changed_ids : []);
  const el=(tag,text,className)=> {const node=document.createElement(tag); if(text!==undefined) node.textContent=text; if(className) node.className=className; return node;};
  const number=value=>value===null || value===undefined ? '—' : typeof value==='object' ? JSON.stringify(value) : String(value);
  function facts(container, rows) {
    const dl=el('dl');
    for (const [key,value] of rows) {dl.append(el('dt',key),el('dd',number(value)));}
    container.replaceChildren(dl);
  }
  function title(row) {
    const lines=[row.operation_id,row.operation_type,'所属工序：'+row.assigned_station];
    if(row.assigned_station!==stations[stationIndex]) lines.push('此序未加工');
    if(active()) {const pair=views(); lines.push('A Station：'+pair[0].operations[row.operation_id].assigned_station,'B Station：'+pair[1].operations[row.operation_id].assigned_station,'工序差 B − A：'+(pair[1].operations[row.operation_id].station_rank-pair[0].operations[row.operation_id].station_rank));}
    if(row.bend) for(const [key,value] of Object.entries(row.bend)) lines.push(key+'：'+number(value));
    return lines.join('\n');
  }
  function updateViewport() {for(const svg of canvases) svg.setAttribute('viewBox',viewport.join(' '));}
  function draw() {
    const pair=views(), changedIds=changed(), rank=stationIndex;
    canvases.forEach((svg,index)=> {
      const view=pair[index], side=index===0?'A':'B';
      svg.setAttribute('aria-label',side+' 二维展开图 '+stations[stationIndex]);
      get('plan-name-'+side).textContent=(data.comparisons.length?'方案 '+side+' · ':'方案 · ')+view.blind_id;
      const link=get('plan-link-'+side); if(link) link.href='plan_cards/'+view.blind_id+'.html';
      for(const node of svg.querySelectorAll('.operation')) {
        const id=node.dataset.operation,row=view.operations[id];
        const focus=VisualLogic.focus(id,geometry,changedIds,onlyDiff,region);
        const style=VisualLogic.style(row,rank,config,mode,colorMode,focus,id===selected);
        node.dataset.status=style.status; node.dataset.focus=focus;node.dataset.selected=String(id===selected);
        const visible=node.querySelector('.visible-geometry');
        visible.setAttribute('stroke',style.stroke); visible.setAttribute('stroke-width',style.stroke_width); visible.setAttribute('opacity',style.opacity);
        node.querySelector('title').textContent=title(row);
      }
      for(const rect of svg.querySelectorAll('.influence-region')) {
        const rid=rect.dataset.region;
        rect.setAttribute('visibility',showRegions && (region==='ALL' || region===rid) ? 'visible' : 'hidden');
        const opacity=rid===region?config.svg.influence_selected_opacity:config.svg.influence_opacity;
        rect.setAttribute('fill-opacity',opacity); rect.setAttribute('stroke-opacity',opacity);
      }
      const rows=VisualLogic.stageRows(view,stations[stationIndex],geometry,changedIds,onlyDiff,region);
      const counts={}; for(const r of rows.all) counts[r.operation_type]=(counts[r.operation_type]||0)+1;
      get('stage-count-'+side).textContent=stations[stationIndex]+' · 动作总数 '+rows.all.length+' · 列表显示 '+rows.shown.length;
      get('stage-types-'+side).textContent=Object.entries(counts).sort().map(([t,n])=>t+': '+n).join(' · ') || '本序没有动作';
      const list=get('stage-list-'+side); list.replaceChildren();
      rows.shown.sort((a,b)=>a.operation_type.localeCompare(b.operation_type)||a.operation_id.localeCompare(b.operation_id));
      for(const r of rows.shown) {
        const button=el('button',r.operation_id+' · '+r.operation_type+(!r.renderable?' · 无可绘制 geometry':''));
        button.type='button'; button.dataset.selectOperation=r.operation_id;
        button.classList.toggle('selected',selected===r.operation_id); list.append(button);
      }
      if(!rows.shown.length) list.append(el('p','当前筛选下没有动作。','muted'));
    });
    for(const button of get('station-controls').querySelectorAll('[data-station-index]')) {
      const yes=Number(button.dataset.stationIndex)===stationIndex; button.classList.toggle('active',yes); button.setAttribute('aria-pressed',String(yes));
    }
    get('previous-station').disabled=stationIndex===0; get('next-station').disabled=stationIndex===stations.length-1;
    for(const button of get('comparison-nav').querySelectorAll('[data-comparison-index]')) button.classList.toggle('active',Number(button.dataset.comparisonIndex)===itemIndex);
    if(active()) {
      get('comparison-title').textContent=active().comparison_id;
      get('difference-summary').textContent=active().diff.summary.join(' ');
      for(const block of document.querySelectorAll('[data-comparison-details]')) block.hidden=block.dataset.comparisonDetails!==active().comparison_id;
      const debug=get('debug-info'); if(debug && active().debug) facts(debug,Object.entries(active().debug));
    }
    updateLegend(); updateSelection(); updateViewport();
  }
  function updateLegend() {
    const container=get('legend'); container.replaceChildren();
    let entries;
    if(mode==='FULL_PLAN_BY_STATION') entries=stations.map((s,i)=>[s,config.colors.station_palette[i%config.colors.station_palette.length]]);
    else if(colorMode==='BY_OPERATION_TYPE') entries=Object.entries(config.colors.operation_types);
    else entries=[['当前序',config.colors.current],['其余动作',config.colors.background],['选中动作',config.colors.selected]];
    for(const [label,color] of entries) {const span=el('span');const dot=el('i');dot.style.backgroundColor=color;span.append(dot,el('span',label));container.append(span);}
    container.append(el('span',mode==='CUMULATIVE'?'当前序最强 · 前序较淡 · 后续保留结构':'相同坐标与比例'));
    get('orientation-note').textContent=orientation===270?'270°熟悉视角 · 工程 X ↓ / Y →':'原始视角 · 工程 X → / Y ↑';
  }
  function updateSelection() {
    const container=get('operation-detail'), trajectory=get('station-trajectory'); trajectory.replaceChildren();
    if(!selected) {container.replaceChildren(el('p','点击图形或当前工序列表查看动作详情。','muted')); return;}
    const pair=views(),a=pair[0].operations[selected],b=pair[1] && pair[1].operations[selected];
    const rows=[['operation_id',selected],['operation_type',a.operation_type],['所属工序',a.assigned_station],['当前序状态',a.assigned_station===stations[stationIndex]?'正在显示此序加工':'此序未加工']];
    if(b) rows.push(['A Station',a.assigned_station],['B Station',b.assigned_station],['工序差 B − A',b.station_rank-a.station_rank],['移动说明',b.station_rank>a.station_rank?'B 比 A 后移 '+(b.station_rank-a.station_rank)+' 序':b.station_rank<a.station_rank?'B 比 A 前移 '+(a.station_rank-b.station_rank)+' 序':'两方案工序相同']);
    for(const key of ['process_group_id','required_predecessor_ids','cad_parent_operation_id','surface_side','region_group']) rows.push([key,a[key]]);
    if(a.bend) for(const [key,value] of Object.entries(a.bend)) rows.push([key,value]);
    if(!a.renderable) rows.push(['空间位置','无可绘制 geometry；仅显示分配事实']);
    facts(container,rows);
    stations.forEach(s=> {const node=el('span',s,'trajectory-station'); if(a.assigned_station===s) node.append(el('b',b?'A':'所属')); if(b && b.assigned_station===s) node.append(el('b','B')); trajectory.append(node);});
  }
  function selectOperation(id) {
    selected=id; draw();
  }
  function screenPoint(svg,event) {
    const r=svg.getBoundingClientRect(),scale=Math.min(r.width/viewport[2],r.height/viewport[3]);
    return [viewport[0]+(event.clientX-r.left-(r.width-viewport[2]*scale)/2)/scale,
            viewport[1]+(event.clientY-r.top-(r.height-viewport[3]*scale)/2)/scale];
  }
  get('comparison-nav').addEventListener('click',event=> {const b=event.target.closest('[data-comparison-index]'); if(b){itemIndex=Number(b.dataset.comparisonIndex);selected=null;draw();}});
  get('station-controls').addEventListener('click',event=> {const b=event.target.closest('[data-station-index]'); if(b){stationIndex=Number(b.dataset.stationIndex);draw();}});
  get('previous-station').addEventListener('click',()=> {if(stationIndex>0){stationIndex--;draw();}});
  get('next-station').addEventListener('click',()=> {if(stationIndex<stations.length-1){stationIndex++;draw();}});
  get('display-mode').addEventListener('change',event=> {mode=event.target.value;draw();});
  get('color-mode').addEventListener('change',event=> {colorMode=event.target.value;draw();});
  get('difference-only').addEventListener('change',event=> {onlyDiff=event.target.checked;draw();});
  get('show-regions').addEventListener('change',event=> {showRegions=event.target.checked;draw();});
  get('region-filter').addEventListener('change',event=> {region=event.target.value;draw();});
  get('display-orientation').addEventListener('change',event=> {
    orientation=Number(event.target.value);baseBox=VisualLogic.displayBox(geometry.view_box,orientation);viewport=[...baseBox];pan=null;
    for(const svg of canvases){svg.querySelector('.display-scene').setAttribute('transform',VisualLogic.matrix(orientation));svg.dataset.rotation=String(orientation);}
    draw();
  });
  get('zoom-in').addEventListener('click',()=> {viewport=VisualLogic.zoom(viewport,baseBox,config.interaction.zoom_step,config.interaction);updateViewport();});
  get('zoom-out').addEventListener('click',()=> {viewport=VisualLogic.zoom(viewport,baseBox,1/config.interaction.zoom_step,config.interaction);updateViewport();});
  get('reset-view').addEventListener('click',()=> {viewport=[...baseBox];updateViewport();});
  get('fit-region').addEventListener('click',()=> {
    const box=geometry.regions[region] && geometry.regions[region].bbox; if(!box) return;
    viewport=VisualLogic.fitRegion(box,orientation,baseBox,config);updateViewport();
  });
  document.addEventListener('click',event=> {const button=event.target.closest('[data-select-operation]'); if(button) selectOperation(button.dataset.selectOperation);});
  for(const svg of canvases) {
    svg.addEventListener('click',event=> {if(justPanned){justPanned=false;return;}const node=event.target.closest('.operation');if(node) selectOperation(node.dataset.operation);});
    svg.addEventListener('keydown',event=> {if(event.key==='Enter' || event.key===' '){const node=event.target.closest('.operation');if(node){event.preventDefault();selectOperation(node.dataset.operation);}}});
    svg.addEventListener('wheel',event=> {event.preventDefault();viewport=VisualLogic.zoom(viewport,baseBox,event.deltaY<0?config.interaction.wheel_zoom_step:1/config.interaction.wheel_zoom_step,config.interaction,screenPoint(svg,event));updateViewport();},{passive:false});
    svg.addEventListener('pointerdown',event=> {if(event.button!==0)return;justPanned=false;pan={id:event.pointerId,start:[event.clientX,event.clientY],view:[...viewport],svg};});
    svg.addEventListener('pointermove',event=> {if(!pan || pan.id!==event.pointerId)return;const r=svg.getBoundingClientRect(),scale=Math.min(r.width/pan.view[2],r.height/pan.view[3]),dx=event.clientX-pan.start[0],dy=event.clientY-pan.start[1];if(Math.hypot(dx,dy)>config.interaction.pan_threshold_px && !justPanned){justPanned=true;svg.setPointerCapture(event.pointerId);}if(justPanned){viewport=[pan.view[0]-dx/scale,pan.view[1]-dy/scale,pan.view[2],pan.view[3]];updateViewport();}});
    for(const name of ['pointerup','pointercancel']) svg.addEventListener(name,event=> {if(pan && pan.id===event.pointerId)pan=null;});
  }
  draw();
})();
