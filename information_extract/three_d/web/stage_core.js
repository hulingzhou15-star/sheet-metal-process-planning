(function(root){
 'use strict';
 function evaluate(data,planId,stationId){
  const plan=data.plans.find(p=>p.plan_id===planId),station=data.stations.find(s=>s.station_id===stationId);
  if(!plan||!station)throw Error('Unknown plan or station');
  const orders=Object.fromEntries(data.stations.map(s=>[s.station_id,s.order]));
  const active=[],current=[],future=[],unmapped=[];
  for(const op of data.operations){
   const assigned=plan.assignments[op.operation_id];if(!(assigned in orders))throw Error('Missing operation assignment');
   if(orders[assigned]<=station.order){active.push(op.operation_id);if(!op.region_id)unmapped.push(op.operation_id);}
   else future.push(op.operation_id);
   if(assigned===stationId)current.push(op.operation_id);
  }
  const motion=root.MaterialMotion.schedule(data.skeleton,plan.assignments,orders,stationId,1);
  return {plan_id:planId,station_id:stationId,active,current,future,unmapped,...motion};
 }
 function paths3(op,state){
  if(!state.active.includes(op.operation_id)||!op.region_id)return [];
  return op.formed_paths.map(path=>path.map(p=>root.MaterialMotion.point(state.transforms[op.region_id],p)));
 }
 function paths2(op,state){return state.active.includes(op.operation_id)?op.flat_paths:[];}
 const api={evaluate,paths3,paths2};if(typeof module!=='undefined')module.exports=api;root.StageReviewCore=api;
})(globalThis);
