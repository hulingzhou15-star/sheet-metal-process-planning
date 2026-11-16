"""Discrete thick-mesh contact for geometries without a rigid BRep pose."""
import numpy as np

EPS = 1e-7


def triangle_bounds(triangles):
    return triangles.min(axis=1), triangles.max(axis=1)


def segment_triangle(start, end, triangle):
    """Vectorized segment intersection, returning hit and strict interior crossing."""
    a, b, c = triangle.transpose(1, 0, 2)
    e1, e2 = b-a, c-a
    direction = end-start
    cross = np.cross(direction, e2)
    det = np.einsum('ij,ij->i', e1, cross)
    use = np.abs(det) > 1e-12
    inv = np.divide(1., det, out=np.zeros_like(det), where=use)
    v = start-a
    u = np.einsum('ij,ij->i', v, cross)*inv
    cross = np.cross(v, e1)
    w = np.einsum('ij,ij->i', direction, cross)*inv
    t = np.einsum('ij,ij->i', e2, cross)*inv
    hit = use & (u >= -EPS) & (w >= -EPS) & (u+w <= 1+EPS) & (t >= -EPS) & (t <= 1+EPS)
    strict = hit & (u > EPS) & (w > EPS) & (u+w < 1-EPS) & (t > EPS) & (t < 1-EPS)
    return hit, strict, t


def coplanar_hit(a, b):
    """2D separating-axis test restricted to coplanar triangle pairs."""
    normal = np.cross(a[:,1]-a[:,0], a[:,2]-a[:,0])
    length = np.linalg.norm(normal, axis=1)
    normal = np.divide(normal, length[:,None], out=np.zeros_like(normal), where=length[:,None]>EPS)
    plane = np.einsum('nki,ni->nk', b-a[:,0,None], normal)
    keep = (length > EPS) & (np.max(np.abs(plane), axis=1) <= EPS)
    a, b, normal = a[keep], b[keep], normal[keep]
    if len(a) == 0:
        return False
    possible = np.ones(len(a), dtype=bool)
    for t in (a,b):
        edge = t[:,[1,2,0]]-t
        axis = np.cross(edge, normal[:,None])
        pa = np.einsum('nki,nji->nkj', axis, a)
        pb = np.einsum('nki,nji->nkj', axis, b)
        possible &= np.all((pa.min(2) <= pb.max(2)+EPS) & (pb.min(2) <= pa.max(2)+EPS), axis=1)
    return bool(np.any(possible))


def inside_any(points, mesh, bbox):
    lo, hi = bbox
    points = points[np.all((points > lo+EPS) & (points < hi-EPS), axis=1)]
    for point in points:
        votes = 0
        for direction in ((1.,.371,.159), (.263,1.,.487), (.167,.419,1.)):
            end = point + (np.linalg.norm(hi-lo)+1)*np.array(direction)
            size = len(mesh)
            hit, _, t = segment_triangle(np.broadcast_to(point,(size,3)), np.broadcast_to(end,(size,3)), mesh)
            if np.any(hit & (abs(t) < EPS)):
                break
            hits = np.sort(t[hit & (t > EPS)])
            votes += (len(hits) > 0 and (1+np.count_nonzero(np.diff(hits)>EPS))%2 == 1)
        else:
            if votes >= 2:
                return True
    return False


def contact(a, b, ab, bb):
    """Surface crossing/contact plus containment probes. No Boolean volume estimate."""
    alo, ahi = ab
    blo, bhi = bb
    a_box, b_box = (alo.min(0), ahi.max(0)), (blo.min(0), bhi.max(0))
    if np.any(a_box[1] < b_box[0]-EPS) or np.any(b_box[1] < a_box[0]-EPS):
        return 'AABB_SEPARATED'
    ai = np.flatnonzero(np.all((alo <= b_box[1]+EPS) & (ahi >= b_box[0]-EPS),axis=1))
    bi = np.flatnonzero(np.all((blo <= a_box[1]+EPS) & (bhi >= a_box[0]-EPS),axis=1))
    touch = False
    for start in range(0,len(ai),max(1,100_000//max(1,len(bi)))):
        ia = ai[start:start+max(1,100_000//max(1,len(bi)))]
        compatible = np.ones((len(ia),len(bi)),dtype=bool)
        for j in range(3):
            compatible &= (alo[ia,j,None] <= bhi[bi,j]+EPS) & (ahi[ia,j,None] >= blo[bi,j]-EPS)
        x,y = np.nonzero(compatible)
        if not len(x):
            continue
        aa, ba = a[ia[x]], b[bi[y]]
        for src,dst in ((aa,ba),(ba,aa)):
            for k in range(3):
                hit, strict, _ = segment_triangle(src[:,k],src[:,(k+1)%3],dst)
                if strict.any():
                    return 'MESH_CROSSING'
                touch |= bool(hit.any())
        if not touch:
            touch = coplanar_hit(aa,ba)
    if touch:
        return 'MESH_CONTACT_OR_EDGE_CROSSING'
    # A contained disconnected component also has a boundary sample inside.
    # Probe mesh vertices and face centers in the overlapping boxes.
    for source, ids, target, box in ((a,ai,b,b_box),(b,bi,a,a_box)):
        if len(ids):
            points = np.unique(source[ids].reshape(-1,3).round(9),axis=0)
            lo, hi = box
            points = points[np.all((points > lo+EPS) & (points < hi-EPS), axis=1)]
            if len(points) > 32:
                points = points[np.linspace(0, len(points)-1, 32, dtype=int)]
            if inside_any(points,target,box):
                return 'MESH_CONTAINMENT'
    return 'MESH_SEPARATED_AT_SAMPLE'
