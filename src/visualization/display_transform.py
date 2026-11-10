"""Presentation transforms only; material Geometry and assignments stay intact."""


def display_point(point, rotation_degrees=270):
    """Input is native SVG (x,-y); engineering CCW 270 means SVG CW 90."""
    if rotation_degrees == 0:
        return tuple(point)
    if rotation_degrees == 270:
        return (-point[1],point[0])
    raise ValueError("Supported display orientations are 0 and 270 degrees")


def display_matrix(rotation_degrees=270):
    if rotation_degrees == 0:
        return "matrix(1 0 0 1 0 0)"
    if rotation_degrees == 270:
        return "matrix(0 1 -1 0 0 0)"
    raise ValueError("Supported display orientations are 0 and 270 degrees")


def display_box(view_box, rotation_degrees=270):
    x,y,w,h=view_box
    points=[display_point((a,b),rotation_degrees) for a,b in ((x,y),(x+w,y),(x,y+h),(x+w,y+h))]
    xs,ys=zip(*points)
    return (min(xs),min(ys),max(xs)-min(xs),max(ys)-min(ys))


def orientation_note(rotation_degrees):
    return "270°视角 · 工程 X ↓ / Y →" if rotation_degrees==270 else "原始视角 · 工程 X → / Y ↑"
