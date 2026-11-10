(function(root){'use strict';
 const M=MaterialMotion;
 function fractions(model,assignment,orders,station,tau){if(!(station in orders)||!Number.isFinite(tau)||tau<0||tau>1)throw Error('Invalid station progress');const out={};for(const b of model.bends){const order=orders[assignment[b.operation_id]];if(!Number.isFinite(order))throw Error('Missing bend assignment');out[b.joint_id]=order<orders[station]?1:order===orders[station]?tau:0;}return out;}
 function stripPoint(b,p,fraction){
  const a=b.start_flat_mm,d=b.forward_flat,e=b.axis_flat,v=p.map((x,i)=>x-a[i]),along=v.reduce((s,x,i)=>s+x*e[i],0),s=v.reduce((sum,x,i)=>sum+x*d[i],0),u=Math.max(0,Math.min(1,s/b.width_mm)),depth=-v[2],alpha=b.target_angle_deg*Math.PI/180*fraction*u,h=b.geometric_offset_from_front_mm;
  const sinc=Math.abs(alpha)<1e-5?1-alpha*alpha/6:Math.sin(alpha)/alpha,cosc=Math.abs(alpha)<1e-5?alpha/2-alpha**3/24:(1-Math.cos(alpha))/alpha;
  const dy=b.width_mm*u*sinc,dz=b.width_mm*u*cosc,sn=Math.sin(alpha),cs=Math.cos(alpha);
  return a.map((x,i)=>x+along*e[i]+dy*d[i]+(i===2?dz-h:0)+(h-depth)*(-sn*d[i]+(i===2?cs:0)));
 }
 function transforms(model,fr){if(Object.keys(fr).length!==model.bends.length||model.bends.some(b=>!Number.isFinite(fr[b.joint_id])||fr[b.joint_id]<0||fr[b.joint_id]>1))throw Error('All fractions required');const ts={[model.root_region_id]:M.identity()};for(const b of model.bends){const d=b.forward_flat,axis=[d[1],-d[0],0],mat=M.rotation([0,0,0],axis,b.target_angle_deg*fr[b.joint_id]),end=b.start_flat_mm.map((x,i)=>x+d[i]*b.width_mm),now=stripPoint(b,end,fr[b.joint_id]),rot=M.point(mat,end);for(let i=0;i<3;i++)mat[i*4+3]=now[i]-rot[i];ts[b.child_region_id]=M.multiply(ts[b.parent_region_id],mat);}return ts;}
 function point(component,p,model,bends,fr,ts){if(component.kind==='panel')return M.point(ts[component.region_id],p);const b=bends.get(component.joint_id);return M.point(ts[b.parent_region_id],stripPoint(b,p,fr[b.joint_id]));}
 const api={fractions,stripPoint,transforms,point};root.BandMaterial=api;if(typeof module!=='undefined')module.exports=api;
})(globalThis);
