"""Reversible planar-skin kinematics; independent input for later real states.

The developed reference is an inverse-rigid-fold construction. It is NOT a
verified blank, neutral-layer unfolding, physical trajectory or forming solve.
"""
from collections import defaultdict
from pathlib import Path
import sys
import math
import numpy as np
from .state import rotation_about_axis, transform_point


def oriented_normals(step_path,asset):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.TopAbs import TopAbs_FACE,TopAbs_REVERSED
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopoDS import TopoDS
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    reader=STEPControl_Reader()
    if reader.ReadFile(str(step_path))!=IFSelect_RetDone or not reader.TransferRoots():raise ValueError('STEP import failed')
    faces=TopTools_IndexedMapOfShape();TopExp.MapShapes_s(reader.OneShape(),TopAbs_FACE,faces)
    if faces.Extent()!=asset['face_count']:raise ValueError('Face index changed')
    result={}
    for f in asset['faces']:
        if 'plane' not in f:continue
        face=TopoDS.Face_s(faces.FindKey(f['face_id']));surface=BRepAdaptor_Surface(face,True)
        props=GProp_GProps();BRepGProp.SurfaceProperties_s(face,props)
        center=np.array([props.CentreOfMass().X(),props.CentreOfMass().Y(),props.CentreOfMass().Z()])
        if not np.allclose(center,f['centroid_mm'],atol=1e-7,rtol=0):raise ValueError('Face index/geometry correspondence changed')
        normal=surface.Plane().Axis().Direction()
        sign=-1 if face.Orientation()==TopAbs_REVERSED else 1
        result[f['face_id']]=(sign*np.array([normal.X(),normal.Y(),normal.Z()])).tolist()
    return result


def build_skeleton(material,asset,registration,normals):
    regions={r['region_id']:dict(r) for r in material['regions']}
    root=next(r for r in regions if registration['base_face_id'] in regions[r]['skin_face_ids'])
    graph=defaultdict(list)
    for joint in material['joints']:
        a,b=joint['region_ids'];graph[a].append((b,joint));graph[b].append((a,joint))
    order=[root];seen={root}
    regions[root].update(parent_region_id=None,front_face_id=registration['base_face_id'],depth=0)
    joint_rows=[]
    for parent in order:
        for child,joint in graph[parent]:
            if child in seen:continue
            seen.add(child);order.append(child)
            evidence=joint['surface_evidence'][0]['contact_skin_faces']
            parent_front=regions[parent]['front_face_id']
            child_contact=evidence[child]
            front=(child_contact if evidence[parent]==parent_front else next(f for f in regions[child]['skin_face_ids'] if f!=child_contact))
            na,nb=np.array(normals[parent_front]),np.array(normals[front]);axis=np.array(joint['axis_direction'])
            angle=math.degrees(math.atan2(float(axis@np.cross(na,nb)),float(na@nb)))
            row={**joint,'parent_region_id':parent,'child_region_id':child,'angle_deg':angle,
                 'reference_surface_normals':{'parent':na.tolist(),'child':nb.tolist()},
                 'status':'GEOMETRIC_KINEMATIC_PROPOSAL'}
            joint_rows.append(row)
            regions[child].update(parent_region_id=parent,front_face_id=front,depth=regions[parent]['depth']+1,joint_id=joint['joint_id'])
    component_joint_count=sum(all(r in seen for r in j['region_ids']) for j in material['joints'])
    if component_joint_count!=len(seen)-1:raise ValueError('Base-connected panel proposal graph has a cycle; no arbitrary motion tree selected')
    skeleton={'root_region_id':root,'region_order':order,'regions':[regions[r] for r in order],
              'unconnected_region_proposal_ids':sorted(set(regions)-seen),
              'unconnected_interpretation':'No supported cylindrical hinge link; does not mean physically disconnected material.',
              'joints':joint_rows,'source':'CAD skin adjacency + oriented surface normals; no operation precedence used',
              'approximation':'Rigid skins with hinges at midplane intersections; bend allowance and bend zones omitted.'}
    flat=poses(skeleton,{j['joint_id']:0 for j in joint_rows})
    for j in joint_rows:
        p=flat[j['parent_region_id']]
        j['developed_axis_point_mm']=transform_point(p,j['hinge_point_mm'])
        j['developed_axis_direction']=(p[:3,:3]@j['axis_direction']).tolist()
        # A physical bend may comprise multiple disconnected cylindrical strips.
        # Preserve each interval as evidence; full extent is only a search aid.
        d=np.array(j['axis_direction']);origin=np.array(j['hinge_point_mm'])
        cylinder_faces=[asset['faces'][e['cylinder_face_id']-1] for e in j['surface_evidence']]
        points=np.array([p for cy in cylinder_faces for e in cy['edge_ids'] for p in asset['edges'][e-1]['points']])
        values=(points-origin)@d
        ends=[origin+v*d for v in (float(min(values)),float(max(values)))]
        j['developed_axis_endpoints_mm']=[transform_point(p,e) for e in ends]
        j['developed_axis_segment_evidence_mm']=[]
        for cy in cylinder_faces:
            samples=np.array([p for e in cy['edge_ids'] for p in asset['edges'][e-1]['points']])
            projected=(samples-origin)@d
            j['developed_axis_segment_evidence_mm'].append({'cylinder_face_id':cy['face_id'],
                    'endpoints':[transform_point(p,origin+value*d) for value in [float(min(projected)),float(max(projected))]]})
    return skeleton


def poses(skeleton,fractions):
    if set(fractions)!={j['joint_id'] for j in skeleton['joints']}:raise ValueError('All joint fractions required; unknown joints cannot be frozen silently')
    if any(not math.isfinite(f) or not 0<=f<=1 for f in fractions.values()):raise ValueError('Joint fractions outside [0,1]')
    by_child={j['child_region_id']:j for j in skeleton['joints']}
    result={skeleton['root_region_id']:np.eye(4)}
    for region in skeleton['region_order'][1:]:
        j=by_child[region]
        # All fractions have already been evaluated at the same process point.
        result[region]=result[j['parent_region_id']]@rotation_about_axis(j['hinge_point_mm'],j['axis_direction'],(fractions[j['joint_id']]-1)*j['angle_deg'])
    return result


def associate_bends(skeleton,facts,registration,config):
    cfg=config['local_association'];sources=[]
    for op in facts['operations']:
        if op['operation_type']!='BEND' or len(op['segments'])!=1:continue
        s=op['segments'][0]
        if s['segment_type'] not in {'LINE','BEND_LINE'}:continue
        m=np.array(registration['matrix']);t=np.array(registration['translation_mm'])
        points=np.array([m@np.array([s[f'{e}_x'],s[f'{e}_y']])+t for e in ['start','end']])
        sources.append((op['operation_id'],points))
    candidates=[]
    for j in skeleton['joints']:
        endpoints=np.array(j['developed_axis_endpoints_mm'])[:,:2];delta=endpoints[1]-endpoints[0];length=np.linalg.norm(delta)
        if length==0:continue
        axis=delta/length
        for op,points in sources:
            other=points[1]-points[0];otherlength=np.linalg.norm(other)
            if abs(length-otherlength)>cfg['length_tolerance_mm'] or otherlength==0:continue
            if abs(abs(float(axis@(other/otherlength)))-1)>config['joint_detection']['axis_tolerance']:continue
            # Endpoint error includes offset along axis and across axis, with
            # either endpoint ordering. No height projection of a formed panel.
            error=min(np.max(np.linalg.norm(endpoints-points,axis=1)),np.max(np.linalg.norm(endpoints-points[::-1],axis=1)))
            if error<=cfg['axis_offset_search_mm']:
                candidates.append({'operation_id':op,'joint_id':j['joint_id'],'endpoint_residual_mm':float(error),
                                   'recorded_endpoints_xy_mm':points.tolist(),'developed_endpoints_xy_mm':endpoints.tolist()})
    by_op=defaultdict(list);by_joint=defaultdict(list)
    for c in candidates:by_op[c['operation_id']].append(c);by_joint[c['joint_id']].append(c)
    for group in list(by_op.values())+list(by_joint.values()):group.sort(key=lambda c:c['endpoint_residual_mm'])
    accepted=[]
    for c in candidates:
        opchoices,jchoices=by_op[c['operation_id']],by_joint[c['joint_id']]
        margin=cfg['minimum_first_vs_second_margin_mm']
        if c is opchoices[0] and c is jchoices[0] and all(len(g)==1 or g[1]['endpoint_residual_mm']-c['endpoint_residual_mm']>=margin for g in (opchoices,jchoices)):
            accepted.append({**c,'status':'UNIQUE_GEOMETRIC_PROPOSAL_NOT_CONFIRMED',
                             'quantitative_match':c['endpoint_residual_mm']<=cfg['quantitative_match_tolerance_mm']})
    return {'matches':sorted(accepted,key=lambda c:c['operation_id']),'all_candidate_count':len(candidates),
            'unmatched_bend_operation_ids':[op for op,_ in sources if op not in {c['operation_id'] for c in accepted}],
            'unmatched_joint_ids':[j['joint_id'] for j in skeleton['joints'] if j['joint_id'] not in {c['joint_id'] for c in accepted}],
            'interpretation':'Mutual unique geometric proposals, not user-confirmed labels. Residuals expose missing bend compensation.'}


def local_schedule(skeleton,joint_to_operation,assignment,station_orders,station_id,tau):
    """Compute local joints from a plan, with a common within-station parameter.

    This evaluates the supplied assignment, not its global manufacturing
    feasibility. No unspecified joint is implicitly treated as stationary.
    """
    if station_id not in station_orders or not math.isfinite(tau) or not 0<=tau<=1:raise ValueError('Unknown station or invalid process parameter')
    if set(joint_to_operation)!={j['joint_id'] for j in skeleton['joints']}:raise ValueError('Incomplete joint-operation correspondence')
    current=station_orders[station_id];fractions={}
    for joint,operation in joint_to_operation.items():
        if operation not in assignment or assignment[operation] not in station_orders:raise ValueError('Local operation assignment missing or station invalid')
        order=station_orders[assignment[operation]]
        fractions[joint]=1.0 if order<current else (float(tau) if order==current else 0.0)
    return {'station_id':station_id,'tau':float(tau),'fractions':fractions,
            'transforms':{k:v.tolist() for k,v in poses(skeleton,fractions).items()},
            'trajectory':'SCHEMATIC_LINEAR_ANGLE_NOT_MEASURED','full_plan_checked':False}
