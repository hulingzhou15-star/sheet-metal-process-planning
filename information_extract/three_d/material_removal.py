"""Audited subset of staged through-cuts. No within-station cutting trajectory.

Only a mutually unique ordinary punch / CAD inner-wire geometric match is used.
Final forming outlines, pre-punches and partition boundaries are never inferred
to be punch events. Solids remain in CAD coordinates and follow panel poses.
"""
import math
from collections import Counter
import numpy as np
from .local_collision import indexed, volume, valid_solid, transformed
from .assets import bounds
from OCP.TopAbs import TopAbs_WIRE, TopAbs_FORWARD
from OCP.TopoDS import TopoDS
from OCP.BRepTools import BRepTools
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire, BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut
from OCP.GC import GC_MakeArcOfCircle
from OCP.gp import gp_Pnt, gp_Vec, gp_Circ, gp_Ax2, gp_Dir


def source_prism(op, registration, z, inverse_flat, vector):
    """Exact LINE/ARC/CIRCLE geometry; reject broken or unsupported chains."""
    matrix = np.asarray(registration['registration_matrix'])
    translation = np.asarray(registration['registration_translation_mm'])
    def point(xy):
        xy = matrix @ np.asarray(xy) + translation
        return gp_Pnt(float(xy[0]), float(xy[1]), float(z))
    segments = sorted(op['segments'], key=lambda s:s['seq'])
    wire = BRepBuilderAPI_MakeWire()
    if len(segments) == 1 and segments[0]['segment_type'] == 'CIRCLE':
        s = segments[0]
        if s['radius'] <= 0: raise ValueError('Invalid radius')
        wire.Add(BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(point([s['center_x'],s['center_y']]),gp_Dir(0,0,1)),s['radius'])).Edge())
    else:
        if not segments: raise ValueError('Missing closed geometry')
        for i,s in enumerate(segments):
            start = np.array([s['start_x'],s['start_y']],dtype=float)
            end = np.array([s['end_x'],s['end_y']],dtype=float)
            nxt = segments[(i+1)%len(segments)]
            if np.linalg.norm(end-[nxt['start_x'],nxt['start_y']]) > 1e-6:
                raise ValueError('Open/discontinuous chain')
            if s['segment_type'] == 'LINE':
                edge = BRepBuilderAPI_MakeEdge(point(start),point(end))
            elif s['segment_type'] == 'ARC':
                center=np.array([s['center_x'],s['center_y']],dtype=float)
                angle=math.radians(s['sweep_angle']); delta=start-center
                def rotate(a): return np.array([[math.cos(a),-math.sin(a)],[math.sin(a),math.cos(a)]])@delta+center
                if not 0 < abs(angle) < 2*math.pi or abs(np.linalg.norm(delta)-s['radius'])>1e-6 or np.linalg.norm(rotate(angle)-end)>1e-6:
                    raise ValueError('Invalid signed arc')
                edge=BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(point(start),point(rotate(angle/2)),point(end)).Value())
            else: raise ValueError('Unsupported segment')
            wire.Add(edge.Edge())
    if not wire.IsDone(): raise ValueError('Wire construction failed')
    face=BRepBuilderAPI_MakeFace(wire.Wire(),True)
    if not face.IsDone(): raise ValueError('Face construction failed')
    formed=transformed(face.Face(),inverse_flat)
    return valid_solid(BRepPrimAPI_MakePrism(formed,gp_Vec(*vector)).Shape())


def audit_cuts(faces, stage, flat, panel_evidence, tolerance=1e-5):
    """Match complete inner-wire volumes, never merely centers or containment."""
    wires=[]; cutters={}; errors=[]
    evidence={e['region_id']:e for e in panel_evidence}
    for region in stage['skeleton']['regions']:
        rid=region['region_id']; ev=evidence[rid]
        face=TopoDS.Face_s(faces.FindKey(region['front_face_id']))
        outer=BRepTools.OuterWire_s(face); wm=indexed(face,TopAbs_WIRE)
        for i in range(1,wm.Extent()+1):
            wire=TopoDS.Wire_s(wm.FindKey(i))
            if wire.IsSame(outer): continue
            key=f'{rid}:W{i}'
            row={'wire_id':key,'region_id':rid,'front_face_id':region['front_face_id'],'wire_index':i,'matches':[]}
            try:
                filled=BRepBuilderAPI_MakeFace(TopoDS.Wire_s(wire.Oriented(TopAbs_FORWARD)),True)
                body=valid_solid(BRepPrimAPI_MakePrism(filled.Face(),gp_Vec(*ev['extrusion_vector_mm'])).Shape())
                cutters[key]=body; row.update(volume_mm3=volume(body),bounds_mm=bounds(body),status='UNASSIGNED_INNER_WIRE_NOT_ASSUMED_CUT')
            except Exception as exc: row.update(status='UNKNOWN_KERNEL_FAILURE',error=str(exc))
            wires.append(row)
    ops=[]; proposals=[]
    panels={p['region_id']:p for p in stage['panels']}
    for op in stage['operations']:
        row={k:op.get(k) for k in ['operation_id','operation_type','geometry_id','region_id','mapping_status']}
        row['candidate_wire_ids']=[]
        if op['operation_type'] not in {'CIRCLE_PUNCH','IRREGULAR_PUNCH'}:
            row['status']=('PRE_PUNCH_REQUIRES_STAGE_GEOMETRY_AND_FORMING_LINK' if op['operation_type']=='PRE_PUNCH' else
                           'TRIM_REQUIRES_BLANK_AND_CUT_REGION' if op['operation_type']=='TRIM' else 'NOT_A_SUPPORTED_REMOVAL_EVENT')
        elif not op['region_id']:
            row['status']='UNRESOLVED_PANEL'
        else:
            rid=op['region_id']
            try:
                body=source_prism(op,stage['display_outline'],panels[rid]['flat_z'],np.linalg.inv(flat[rid]),evidence[rid]['extrusion_vector_mm'])
                vb=volume(body); bb=np.array(bounds(body))
                for w in wires:
                    if w['region_id']!=rid or w['wire_id'] not in cutters: continue
                    if np.max(np.abs(bb-w['bounds_mm']))>tolerance: continue
                    common=BRepAlgoAPI_Common(body,cutters[w['wire_id']])
                    if not common.IsDone(): raise ValueError('Match intersection failed')
                    error=max(0.,vb+w['volume_mm3']-2*volume(common.Shape()))
                    if error <= tolerance:
                        proposals.append({'operation_id':op['operation_id'],'region_id':rid,'wire_id':w['wire_id'],
                                          'symmetric_difference_mm3':error,'source_volume_mm3':vb})
                        row['candidate_wire_ids'].append(w['wire_id']);w['matches'].append(op['operation_id'])
                row['status']='NO_EXACT_INNER_WIRE_MATCH' if not row['candidate_wire_ids'] else 'AMBIGUOUS_MATCH'
            except Exception as exc: row.update(status='UNKNOWN_GEOMETRY',error=str(exc))
        ops.append(row)
    counts=Counter(p['wire_id'] for p in proposals); byop={o['operation_id']:o for o in ops}
    accepted=[p for p in proposals if counts[p['wire_id']]==1 and len(byop[p['operation_id']]['candidate_wire_ids'])==1]
    for p in accepted: byop[p['operation_id']]['status']='EXACT_UNIQUE_GEOMETRIC_SUBSET'
    return {'operations':ops,'inner_wires':wires,'accepted':accepted,'match_tolerance_mm_and_mm3':tolerance,
            'status_counts':dict(Counter(o['status'] for o in ops)),
            'interpretation':'Geometric diagnostic subset; not user-confirmed manufacturing geometry.'},cutters


def active_cuts(accepted, assignments, orders, station_id, phase):
    if phase not in {'entry','during','completed'}: raise ValueError('Unknown stage phase')
    current=orders[station_id]
    return [p for p in accepted if orders[assignments[p['operation_id']]] < current or
            (phase=='completed' and orders[assignments[p['operation_id']]]==current)]


def cut_panels(base, cutters, events):
    """Fresh immutable material state: checker cache must be scoped to this state."""
    result=dict(base); evidence=[]
    for event in sorted(events,key=lambda e:e['wire_id']):
        rid=event['region_id']; before=volume(result[rid]); tool=cutters[event['wire_id']]
        cut=BRepAlgoAPI_Cut(result[rid],tool)
        if not cut.IsDone(): raise ValueError('Cut failed: '+event['wire_id'])
        result[rid]=valid_solid(cut.Shape()); removed=before-volume(result[rid])
        if abs(removed-volume(tool))>1e-5: raise ValueError('Cut is not wholly contained / duplicate removal')
        evidence.append({**event,'before_mm3':before,'after_mm3':volume(result[rid]),'removed_mm3':removed})
    return result,evidence
