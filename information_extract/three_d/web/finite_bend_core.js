(function(root){
 'use strict';
 const sinc=x=>Math.abs(x)<1e-7?1-x*x/6+x*x*x*x/120:Math.sin(x)/x;
 function point(z,depth=0,u=1){const {start,phi,angle,length,h}=z,a=angle*u,d=[Math.cos(phi),Math.sin(phi)],n=[-Math.sin(phi),Math.cos(phi)],nn=[-Math.sin(phi+a),Math.cos(phi+a)],dy=length*u*sinc(a),dz=length*u*a/2*sinc(a/2)**2;return [0,1].map(i=>start[i]-h*n[i]+dy*d[i]+dz*n[i]+(h-depth)*nn[i]);}
 function frames(model,fractions){
  if(Object.keys(fractions).length!==model.bends.length||model.bends.some(b=>!Number.isFinite(fractions[b.operation_id])||fractions[b.operation_id]<0||fractions[b.operation_id]>1))throw Error('All finite local fractions in [0,1] required');
  let start=[...model.root_start_top_yz_mm],phi=0;const panels=[],zones=[];
  model.bends.forEach((b,i)=>{const angle=b.target_angle_deg*Math.PI/180*fractions[b.operation_id],z={start:[...start],phi,angle,length:b.developed_width_mm,h:b.neutral_offset_from_top_mm};zones.push(z);start=point(z);phi+=angle;panels.push({start_top_yz_mm:[...start],phi});if(i===0)start=start.map((p,j)=>p+model.middle_straight_length_mm*[Math.cos(phi),Math.sin(phi)][j]);});return {panels,zones};
 }
 function mapper(model,fractions){const a=frames(model,fractions),b=frames(model,Object.fromEntries(model.bends.map(b=>[b.operation_id,1])));return {frames:a,map:(index,p)=>{if(!index)return [...p];const c=a.panels[index-1],f=b.panels[index-1],angle=c.phi-f.phi,co=Math.cos(angle),si=Math.sin(angle),y=p[1]-f.start_top_yz_mm[0],z=p[2]-f.start_top_yz_mm[1];return [p[0],c.start_top_yz_mm[0]+co*y-si*z,c.start_top_yz_mm[1]+si*y+co*z];}};}
 const api={point,frames,mapper};root.FiniteBend=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
