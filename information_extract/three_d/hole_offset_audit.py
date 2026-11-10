"""Shape-preserving hole-offset diagnostics, independent of removal decisions."""
import numpy as np
from .local_collision import volume, transformed
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
from OCP.BRepCheck import BRepCheck_Analyzer


def centroid(shape):
    prop = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, prop)
    p = prop.CentreOfMass()
    return np.array([p.X(), p.Y(), p.Z()])


def compare_translation(source, target, normal, rotation, tolerance=1e-5):
    """Measure center difference; test exact shape after translation only.

    No rescaling, rotation, quantization to t/2, or automatic association.
    Normal offset is reported independently of in-plane displacement.
    """
    a, b = centroid(source), centroid(target)
    delta = b-a
    n = np.array(normal, dtype=float)
    n /= np.linalg.norm(n)
    normal_delta = float(delta@n)
    planar = delta-normal_delta*n
    matrix = np.eye(4)
    matrix[:3, 3] = delta
    common = BRepAlgoAPI_Common(transformed(source, matrix), target)
    if not common.IsDone() or not BRepCheck_Analyzer(common.Shape()).IsValid():
        raise ValueError('Translation comparison kernel failure')
    va, vb = volume(source), volume(target)
    error = max(0., va+vb-2*volume(common.Shape()))
    flat = np.asarray(rotation)[:3, :3]@delta
    norm = float(np.linalg.norm(planar))
    return {'source_centroid_xyz_mm': a.tolist(), 'cad_centroid_xyz_mm': b.tolist(),
            'cad_minus_source_xyz_mm': delta.tolist(), 'flat_delta_xyz_mm': flat.tolist(),
            'normal_delta_mm': normal_delta, 'in_plane_delta_mm': norm,
            'source_volume_mm3': va, 'cad_wire_volume_mm3': vb,
            'translation_aligned_symmetric_difference_mm3': error,
            'same_shape_after_translation': error <= tolerance and abs(normal_delta) <= tolerance}


def offset_family(distance, thickness, tolerance=1e-5):
    for name, value in [('ZERO',0.), ('HALF_THICKNESS',thickness/2), ('ONE_THICKNESS',thickness)]:
        if abs(distance-value) <= tolerance:
            return name
    return 'OTHER'


def leave_one_out(rows):
    """Predict each hole shift using other congruent holes on the same panel."""
    if len(rows)<2:
        return {'status':'INSUFFICIENT_INDEPENDENT_HOLES','count':len(rows)}
    deltas=np.array([r['flat_delta_xyz_mm'] for r in rows])
    errors=[]
    for i in range(len(rows)):
        prediction=np.mean(np.delete(deltas,i,axis=0),axis=0)
        errors.append(float(np.linalg.norm(deltas[i]-prediction)))
    return {'status':'SAME_PANEL_LEAVE_ONE_OUT_NOT_PHYSICAL_VALIDATION',
            'count':len(rows),'mean_flat_delta_xyz_mm':deltas.mean(axis=0).tolist(),
            'max_prediction_error_mm':max(errors), 'per_hole_error_mm':errors}
