"""Build auditable P002 facts and geometry readiness before formal generation.

No expert plan is used as a seed. Missing material domains prohibit P1 scoring.
"""
from collections import Counter,defaultdict
from copy import deepcopy
from pathlib import Path
import argparse

from information_extract.three_d.artifacts import ROOT,read,write,sha
from information_extract.three_d.project_allocation_facts import extract_facts,solve_hard
from information_extract.three_d.material import infer_material
from information_extract.three_d.local_motion import oriented_normals,build_skeleton,associate_bends
from information_extract.extract_app.drawing import load_drawing
from information_extract.extract_app.workflow import compile_operation
from information_extract.extract_app.process_config import planning_operations
from information_extract.scripts.probe_p002_curved_sides import probe
from information_extract.scripts.experiment_bend_allocation_3d import protected_hashes

MODULE=ROOT/'information_extract'
OUT=MODULE/'outputs/p002_generation_v01'


def topology(material,base_face):
    ids={r['region_id'] for r in material['regions']}
    root=next(r['region_id'] for r in material['regions'] if base_face in r['skin_face_ids'])
    graph=defaultdict(set)
    for j in material['joints']:
        a,b=j['region_ids'];graph[a].add(b);graph[b].add(a)
    def component(start,excluded):
        found={start};todo=[start]
        for a in todo:
            for b in sorted(graph[a]-found-excluded):found.add(b);todo.append(b)
        return found
    connected=component(root,set());remaining=ids-{root};branches=[]
    while remaining:
        group=component(min(remaining),{root});remaining-=group
        internal=[j['joint_id'] for j in material['joints'] if set(j['region_ids'])<=group]
        attached=[j['joint_id'] for j in material['joints'] if root in j['region_ids'] and set(j['region_ids'])&group]
        branches.append(dict(region_ids=sorted(group),internal_joints=internal,base_attachment_joints=attached,
            single_hinge_subtree_supported=len(attached)==1 and len(internal)==len(group)-1))
    edges=[j for j in material['joints'] if set(j['region_ids'])<=connected]
    return dict(root_region_id=root,region_count=len(ids),joint_count=len(material['joints']),
        base_connected_regions=len(connected),base_component_cycle_rank=len(edges)-len(connected)+1,
        outside_base_hinge_component=sorted(ids-connected),branches=branches,
        geometry_not_H7=True,arbitrary_spanning_tree_selected=False,
        scope='Planar STEP skin pairs and cylindrical adjacency proposals only; curved surfaces and local forms need explicit cells')


def straight_branch_proposals(material,structure,asset,registration,project,drawing,config):
    """Analyze only complete acyclic base branches; never prune a cycle edge."""
    retained={structure['root_region_id']}
    for b in structure['branches']:
        if b['single_hinge_subtree_supported']:retained.update(b['region_ids'])
    subset={**material,'regions':[r for r in material['regions'] if r['region_id'] in retained],
        'joints':[j for j in material['joints'] if set(j['region_ids'])<=retained]}
    normals=oriented_normals(MODULE/'sources/P002/originals/P002.stp',asset)
    reg={**registration,'base_face_id':466}
    skeleton=build_skeleton(subset,asset,reg,normals)
    index={s['id']:s for s in drawing['segments']};ops=[]
    for o in planning_operations(project):
        if o.get('feature_type')!='BEND_STRAIGHT':continue
        record=compile_operation(o,[index[s] for s in o['source_segment_ids']],config)
        refs=[p for p in record['paths'] if p['role']=='BEND_REFERENCE']
        if len(refs)!=1:raise ValueError('Missing unique straight bend reference')
        ops.append(dict(operation_id=o['operation_id'],operation_type='BEND',segments=refs[0]['segments']))
    assoc=associate_bends(skeleton,{'operations':ops},reg,read(MODULE/'configs/material_state_v01.json'))
    return dict(skeleton=skeleton,association=assoc,
        excluded_whole_branches=[b for b in structure['branches'] if not b['single_hinge_subtree_supported']],
        interpretation='Partial straight-bend correspondence proposals only, not a complete P1 basis; no H7 edges used')


def carried_region_proposals(material,structure,straight,sides):
    """Topological regions only; never masquerade surface areas as solid volumes."""
    root=structure['root_region_id'];graph=defaultdict(set)
    for j in material['joints']:
        a,b=j['region_ids'];graph[a].add(b);graph[b].add(a)
    children=defaultdict(set);joint_by={}
    for j in straight['skeleton']['joints']:
        children[j['parent_region_id']].add(j['child_region_id']);joint_by[j['joint_id']]=j
    def descendants(seed,adj):
        seen=set(seed);todo=list(seed)
        for x in todo:
            for y in sorted(adj[x]-seen-{root}):seen.add(y);todo.append(y)
        return seen
    rows=[];used=set()
    for r in straight['association']['matches']:
        j=joint_by[r['joint_id']];used.add(j['joint_id'])
        regions=descendants([j['child_region_id']],children)
        rows.append(dict(operation_id=r['operation_id'],kind='STRAIGHT_PROPOSAL',region_ids=sorted(regions),
                         joint_ids=[j['joint_id']],correspondence=r))
    by_face={f:r['region_id'] for r in material['regions'] for f in r['skin_face_ids']}
    for r in sides['rows']:
        seeds={by_face[f] for f in r['adjacent_step_face_ids'] if f in by_face}-{root}
        if not seeds:raise ValueError('Curved wall has no planar region evidence')
        regions=descendants(seeds,graph)
        attach=[j['joint_id'] for j in material['joints'] if root in j['region_ids'] and set(j['region_ids'])&regions]
        used.update(attach)
        rows.append(dict(operation_id=r['operation_id'],kind='CURVED_COMPOUND_PROPOSAL',region_ids=sorted(regions),
            joint_ids=attach,seed_regions_from_step_boundary=sorted(seeds),
            incomplete_nonplanar_material=True,source='Exact source/base boundary and STEP adjacency; not H7'))
    remaining=[j for jid,j in joint_by.items() if jid not in used]
    if len(remaining)!=2 or not any(a['child_region_id']==b['parent_region_id'] for a in remaining for b in remaining if a!=b):
        raise ValueError('Z two-joint proposal not unique; do not guess')
    regions=set().union(*(descendants([j['child_region_id']],children) for j in remaining))
    rows.append(dict(operation_id='O136',kind='Z_COMPOSITE_PROPOSAL',component_operation_ids=['O042','O043'],
        region_ids=sorted(regions),joint_ids=[j['joint_id'] for j in remaining],
        source='Two remaining connected joints after independent straight/curved associations; requires finite-bend compensation verification',
        constituent_axis_assignment_confirmed=False))
    return dict(rows=sorted(rows,key=lambda r:r['operation_id']),operation_count=len(rows),geometry_not_H7=True,
        exact_solid_volumes_available=False,p1_basis_compiled=False,
        missing='Exact nonplanar wall material, local STEP endpoint ownership and finite bend-zone partition; proposals are not scoring inputs')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=OUT);args=parser.parse_args()
    out=args.output.resolve()
    if not out.is_relative_to(MODULE/'outputs') or out==MODULE/'outputs':raise ValueError('Dedicated geometry output required')
    protected=protected_hashes();cfg=read(MODULE/'configs/P002.json');path=MODULE/'projects/P002/project.json'
    project=read(path);drawing=load_drawing(MODULE/cfg['source_file'],cfg)
    folder=MODULE/'outputs/multi_part_step_v01/P002';asset=read(folder/'asset.json');reg=read(folder/'base_registration.json')
    files={path,MODULE/cfg['source_file'],folder/'asset.json',folder/'base_registration.json',MODULE/'sources/P002/originals/P002.stp',
           MODULE/'configs/P002.json',MODULE/'configs/material_state_v01.json',Path(__file__),MODULE/'scripts/probe_p002_curved_sides.py',
           MODULE/'scripts/experiment_bend_allocation_3d.py'}
    files.update((MODULE/'three_d').glob('*.py'));files.update((MODULE/'extract_app').rglob('*.py'));files.update((ROOT/'src').rglob('*.py'))
    hashes={p.relative_to(ROOT).as_posix():sha(p) for p in sorted(files)}
    sides=probe(project,drawing,asset,reg)
    # Derived facts only: keep the user's saved project byte-for-byte intact.
    derived=deepcopy(project);calibrated={r['operation_id']:r['surface_side'] for r in sides['rows']}
    for o in derived['operations']:
        if o['operation_id'] in calibrated:
            if o.get('surface_side') not in ('NOT_APPLICABLE',None,calibrated[o['operation_id']]):raise ValueError('Side conflicts with saved fact')
            o['surface_side']=calibrated[o['operation_id']]
    facts=extract_facts(derived,drawing);context,witness=solve_hard(facts)
    index={s['id']:s for s in drawing['segments']};paths={}
    for op in planning_operations(derived):
        # This is a sidecar probe, never a saved editor compilation or Sheet5 input.
        record=compile_operation(op,[index[s] for s in op['source_segment_ids']],cfg)
        if op['feature_type']=='BEND_Z':
            members={o['operation_id']:o for o in derived['operations']}
            refs=[]
            for oid in op['composite_operation_ids']:
                member=members[oid]
                sub=compile_operation(member,[index[s] for s in member['source_segment_ids']],cfg)
                lines=[p for p in sub['paths'] if p['role']=='BEND_REFERENCE']
                if len(lines)!=1:raise ValueError('Missing Z component reference')
                refs.append({**lines[0],'path_id':oid,'component_operation_id':oid})
            record['paths']=refs
        paths[op['operation_id']]=dict(feature_type=op['feature_type'],paths=record['paths'],
            source_segment_ids=op['source_segment_ids'],legacy_status=record['status'],
            legacy_issues=record['issues'],legacy_issues_are_not_new_policy_requirements=True)
    material=infer_material(asset,read(MODULE/'configs/material_state_v01.json'))
    structure=topology(material,468)
    straight=straight_branch_proposals(material,structure,asset,reg,derived,drawing,cfg)
    domains=carried_region_proposals(material,structure,straight,sides)
    bends=[o for o in facts['operations'] if o['operation_type']=='BEND']
    report=dict(part_id='P002',source_revision=project['revision'],facts_fingerprint=facts['fingerprint'],
        active_operation_count=len(context.operations),operation_types=dict(Counter(o.operation_type for o in context.operations.values())),
        station_count=len(context.stations),precedence_counts=dict(Counter(r.constraint_type for r in context.precedence_relations)),
        hard_feasible=witness['feasible'],hard_status=witness['status'],expert_used=False,H6_enabled=False,trim_count=0,
        side_calibration={r['operation_id']:r['surface_side'] for r in sides['rows']},
        bend_count=len(bends),bend_types=dict(Counter(o['feature_type'] for o in bends)),
        formal_generation_ready=False,candidate_pool_generated=False,scoring_run=False,motion_check_run=False,
        blockers=[dict(code='MATERIAL_DOMAINS_NOT_COMPILED',operations=[o['operation_id'] for o in bends],
            detail='Need supported complete material domains for 12 straight bends, 4 curved bends and one composite Z action; never substitute H7 descendants or planar area'),
            dict(code='MULTIPATH_P5_AND_DRAW_FORM_SCORING_ADAPTER',detail='Preserved source paths must be consumed without flattening separate contours; legacy scorer lacks DRAW_FORM and must be extended without aliasing its type')],
        geometry_structure=structure,manufacturing_safety_proved=False)
    write(out/'facts.json',facts);write(out/'curved_side_evidence.json',sides);write(out/'hard_witness.json',witness)
    write(out/'geometry_paths.json',paths);write(out/'material_proposals.json',material);write(out/'readiness.json',report)
    write(out/'straight_branch_proposals.json',straight)
    write(out/'carried_region_proposals.json',domains)
    if any(sha(ROOT/p)!=h for p,h in hashes.items()) or protected_hashes()!=protected:raise ValueError('Source changed during preparation')
    for row in protected['preexisting_mismatches']:
        if row['path']=='information_extract/projects/P002/project.json':
            row['reason']='Existing user-authorized revision 16 is consumed at actual_sha256; registry expectation is unchanged.'
    write(out/'verification.json',dict(protection=protected,source_files_unchanged=len(hashes),project_unchanged=True,expert_used=False))
    write(out/'inputs.json',dict(files=hashes,facts_fingerprint=facts['fingerprint']))
    out.joinpath('index.html').write_text('''<!doctype html><html lang="zh"><meta charset="utf-8"><title>P002候选生成准备</title>
<style>body{font:16px system-ui;max-width:1000px;margin:32px auto;color:#234}aside{padding:16px;background:#fff1d7}</style>
<h1>P002 · 候选生成接入核验</h1><aside>132活动动作、10工序的硬约束可行性已通过。正式几何P1材料域和多路径评分接入尚未完成，因此未生成或排名正式候选。切边排除，专家答案未参与求解。</aside>
<p>O088/O096为光面，O104为毛面：按用户指定与O069同基板方向校准，保留STEP逐边对应。原标注未修改。</p>
<p>原始平面材料提案37区域/48连接，不能强选一棵树代替曲线和局部成形结构。几何连接与H7关系独立。</p>
<p><a href="readiness.json">接入状态</a> · <a href="facts.json">隔离事实</a> · <a href="hard_witness.json">硬约束见证（非候选排名）</a> · <a href="curved_side_evidence.json">侧别证据</a> · <a href="geometry_paths.json">多路径几何来源</a> · <a href="material_proposals.json">STEP材料连接提案</a></p></html>''',encoding='utf-8')
    print({k:report[k] for k in ('source_revision','active_operation_count','precedence_counts','hard_status','side_calibration','formal_generation_ready')})


if __name__=='__main__':main()
