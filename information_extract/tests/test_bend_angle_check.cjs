const fs=require('node:fs'),assert=require('node:assert/strict');
const M=require('../three_d/web/motion_core.js'),C=require('../three_d/web/core.js');
const data=JSON.parse(fs.readFileSync('information_extract/outputs/stage_review_v01/P001/stage_data.json','utf8'));
const audit=JSON.parse(fs.readFileSync('information_extract/outputs/bend_angle_check/P001/audit.json','utf8'));
const rotate=(m,v)=>[0,1,2].map(i=>m[4*i]*v[0]+m[4*i+1]*v[1]+m[4*i+2]*v[2]);
let maxError=0;
for(const test of audit.kernel_cases){
 const fractions=Object.fromEntries(data.skeleton.joints.map(j=>[j.joint_id,test.fraction]));
 const transforms=M.poses(data.skeleton,fractions),j=data.skeleton.joints.find(j=>j.joint_id===test.joint_id);
 const a=rotate(transforms[j.parent_region_id],test.parent_normal),b=rotate(transforms[j.child_region_id],test.child_normal),axis=rotate(transforms[j.parent_region_id],j.axis_direction);
 const angle=Math.atan2(C.dot(axis,C.cross(a,b)),C.dot(a,b))*180/Math.PI;
 maxError=Math.max(maxError,Math.abs(angle-test.expected_deg));assert.ok(Math.abs(angle-test.expected_deg)<audit.config.normal_rotation_check_tolerance_deg);
}
assert.equal(audit.kernel_cases.length,30);
console.log(JSON.stringify({passed:true,browser_kernel_angle_checks:30,max_error_deg:maxError}));
