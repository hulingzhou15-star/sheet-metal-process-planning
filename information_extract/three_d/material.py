"""Geometric plate-skin pairs and bend adjacency, independent of CAD action tree.

Pairs remain proposals: trimmed projected bounds and thickness are evidence,
not proof of a complete material decomposition. Thin side faces are excluded.
"""
from collections import defaultdict
import numpy as np


def plane_projection(face):
    n=np.asarray(face['plane']['normal'],dtype=float)
    drop=int(np.argmax(np.abs(n)))
    keep=[i for i in range(3) if i!=drop]
    box=face['bounds_mm']
    return keep,np.array([box[i] for i in keep]),np.array([box[i+3] for i in keep])


def overlap(a,b):
    keep,alo,ahi=plane_projection(a)
    box=b['bounds_mm'];blo=np.array([box[i] for i in keep]);bhi=np.array([box[i+3] for i in keep])
    numerator=float(np.prod(np.maximum(0,np.minimum(ahi,bhi)-np.maximum(alo,blo))))
    denominator=min(float(np.prod(ahi-alo)),float(np.prod(bhi-blo)))
    return numerator/denominator if denominator>0 else 0


def plane_distance(a,b):
    return abs(float(np.dot(np.array(a['plane']['normal']),np.array(a['plane']['point_mm'])-b['plane']['point_mm'])))


def infer_material(asset,config):
    cfg=config['plane_pairing']
    planes=[f for f in asset['faces'] if f.get('plane') and f['area_mm2']>=cfg['minimum_panel_area_mm2']]
    largest=max(planes,key=lambda f:f['area_mm2'])
    candidates=[]
    for a in planes:
        for b in planes:
            if a['face_id']>=b['face_id']:
                continue
            if abs(abs(np.dot(a['plane']['normal'],b['plane']['normal']))-1)>cfg['parallel_tolerance']:
                continue
            ratio=min(a['area_mm2'],b['area_mm2'])/max(a['area_mm2'],b['area_mm2'])
            if ratio<cfg['minimum_area_ratio']:
                continue
            projected=overlap(a,b)
            distance=plane_distance(a,b)
            if projected>=cfg['minimum_projected_overlap'] and distance>cfg['distance_tolerance_mm']:
                candidates.append({'a':a['face_id'],'b':b['face_id'],'distance_mm':distance,'area_ratio':ratio,'projected_overlap':projected})
    base_candidates=[c for c in candidates if largest['face_id'] in (c['a'],c['b'])]
    if not base_candidates:
        raise ValueError('No opposite skin proposal for largest plane')
    base_pair=min(base_candidates,key=lambda c:c['distance_mm'])
    thickness=base_pair['distance_mm']
    choices=defaultdict(list)
    for c in candidates:
        if abs(c['distance_mm']-thickness)<=cfg['distance_tolerance_mm']:
            choices[c['a']].append(c);choices[c['b']].append(c)
    regions=[];face_to_region={};ambiguous=[]
    for c in sorted(candidates,key=lambda c:(c['a'],c['b'])):
        if abs(c['distance_mm']-thickness)>cfg['distance_tolerance_mm']:
            continue
        if len(choices[c['a']])!=1 or len(choices[c['b']])!=1:
            ambiguous.append(c);continue
        a,b=[asset['faces'][i-1] for i in (c['a'],c['b'])]
        n=np.array(a['plane']['normal']);p=(np.array(a['plane']['point_mm'])+b['plane']['point_mm'])/2
        row={'region_id':f'P{c["a"]}_{c["b"]}','skin_face_ids':[c['a'],c['b']],
             'midplane':{'point_mm':p.tolist(),'normal':n.tolist()},
             'pair_evidence':c,'status':'GEOMETRIC_PANEL_PROPOSAL'}
        regions.append(row)
        for i in row['skin_face_ids']:face_to_region[i]=row['region_id']
    supported=[c for c in candidates if abs(c['distance_mm']-thickness)<=cfg['distance_tolerance_mm']
               and min(asset['faces'][c['a']-1]['area_mm2'],asset['faces'][c['b']-1]['area_mm2'])
               >=largest['area_mm2']*cfg['thickness_support_area_fraction']]
    if len(supported)<3:
        raise ValueError('Insufficient large-plane evidence for thickness estimate')
    joints=[];jcfg=config['joint_detection'];index={r['region_id']:r for r in regions}
    for f in asset['faces']:
        cy=f.get('cylinder')
        if not cy:continue
        contacts=[n for n in f['neighbor_face_ids'] if n in face_to_region]
        for i,a in enumerate(contacts):
            for b in contacts[i+1:]:
                ra,rb=face_to_region[a],face_to_region[b]
                if ra==rb:continue
                na,nb=[np.array(index[r]['midplane']['normal']) for r in (ra,rb)]
                d=np.array(cy['direction'])
                if abs(d@na)>jcfg['axis_tolerance'] or abs(d@nb)>jcfg['axis_tolerance']:continue
                angle=float(np.degrees(np.arccos(np.clip(abs(na@nb),-1,1))))
                if angle<jcfg['minimum_angle_deg']:continue
                # Intersection of the two midplanes defines this rigid-skin
                # approximation's hinge, not a measured neutral/bend axis.
                axis=np.cross(na,nb);axis/=np.linalg.norm(axis)
                pivot=int(np.argmax(abs(axis)))
                if axis[pivot]<0:axis=-axis
                point=np.array(cy['point_mm'])
                equations=np.array([na,nb,axis])
                rhs=np.array([na@np.array(index[ra]['midplane']['point_mm']),nb@np.array(index[rb]['midplane']['point_mm']),axis@point])
                intersection=np.linalg.solve(equations,rhs)
                pair=sorted([ra,rb])
                merged=next((j for j in joints if j['region_ids']==pair
                             and np.linalg.norm(np.cross(np.array(j['hinge_point_mm'])-intersection,axis))<=jcfg['axis_merge_tolerance_mm']),None)
                contact={'cylinder_face_id':f['face_id'],'contact_skin_faces':{ra:a,rb:b},'outer_or_inner_radius_mm':cy['radius_mm']}
                if merged:merged['surface_evidence'].append(contact)
                else:joints.append({'joint_id':f'J{f["face_id"]}','region_ids':pair,'hinge_point_mm':intersection.tolist(),
                                    'axis_direction':axis.tolist(),'surface_evidence':[contact],
                                    'unsigned_plane_angle_deg':angle,'axis_model':'MIDPLANE_INTERSECTION_APPROXIMATION'})
    return {'regions':regions,'joints':joints,'face_to_region':face_to_region,
            'thickness_estimate':{'value_mm':thickness,'status':'GEOMETRIC_ESTIMATE_NOT_STOCK_SPEC',
                                  'large_plane_support':supported},
            'ambiguous_pair_proposals':ambiguous,'unpaired_plane_count':sum(f['face_id'] not in face_to_region for f in planes),
            'scope':'Planar skins only; no complete solid partition, bend-zone mesh or initial cut topology.'}
