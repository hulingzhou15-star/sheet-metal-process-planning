"""SVG vector output, shared by offline UI and auditable static previews."""
from html import escape
import re

from .plan_view_model import operation_focus, operation_status
from .display_transform import display_box, display_matrix

SVG_NS = "http://www.w3.org/2000/svg"


def number(value, decimals=9):
    return f"{value:.{decimals}f}".rstrip("0").rstrip(".") or "0"


def shape_markup(shape, decimals=9):
    n = lambda value: number(value, decimals)
    if shape["kind"] == "circle":
        x,y = shape["center"]
        return f'<circle cx="{n(x)}" cy="{n(y)}" r="{n(shape["radius"])}"/>'
    sx,sy = shape["start"]; ex,ey = shape["end"]
    d = f'M {n(sx)} {n(sy)} '
    if shape["kind"] == "line":
        d += f'L {n(ex)} {n(ey)}'
    elif shape["kind"] == "arc":
        d += f'A {n(shape["radius"])} {n(shape["radius"])} 0 {shape["large_arc_flag"]} {shape["sweep_flag"]} {n(ex)} {n(ey)}'
    elif shape["kind"] == "cubic":
        a,b=shape["control1"];c,e=shape["control2"]
        d += f'C {n(a)} {n(b)} {n(c)} {n(e)} {n(ex)} {n(ey)}'
    else:
        raise ValueError("Unknown SVG shape")
    return f'<path d="{d}"/>'


def target_id(prefix, operation_id):
    # Length-delimited UTF-8 hex is injective, even for unusual operation IDs.
    return "target-"+prefix.encode("utf-8").hex()+"-"+operation_id.encode("utf-8").hex()


def operation_style(row, current_rank, config, *, mode="CURRENT_ONLY", color_mode="BY_CURRENT_STATUS",
                    focus="primary", selected=False):
    svg,colors = config["svg"], config["colors"]
    status = operation_status(row, current_rank, mode)
    opacity = svg["background_opacity"]
    color = colors["background"]
    if status == "full":
        opacity = svg["current_stage_opacity"]
        palette = colors["station_palette"]
        color = palette[row["station_rank"] % len(palette)]
    elif status == "current":
        opacity,color = svg["current_stage_opacity"], colors["current"]
    elif status == "previous":
        opacity,color = svg["previous_stage_opacity"], colors["previous"]
    elif mode == "CUMULATIVE":
        opacity = svg["future_stage_opacity"]
    if color_mode == "BY_OPERATION_TYPE" and status not in ("inactive", "full"):
        color = colors["operation_types"][row["operation_type"]]
    if focus == "unchanged":
        opacity = svg["unchanged_opacity"]
    elif focus == "filtered":
        opacity = svg["filtered_opacity"]
    elif focus == "related":
        opacity = svg["related_bend_opacity"]
    width = svg["normal_stroke_width"]
    if status == "current":
        width=svg["current_stroke_width"]
    if row["operation_type"] == "BEND":
        width = svg["bend_current_stroke_width"] if status == "current" else svg["bend_normal_stroke_width"]
    if selected and focus == "primary":
        color,width,opacity = colors["selected"],svg["selected_stroke_width"],svg["current_stage_opacity"]
    return {"status": status, "focus": focus, "stroke": color, "stroke_width": width, "opacity": opacity}


def operation_title(row, other=None, current_station=None):
    lines = [row["operation_id"], row["operation_type"], f'所属工序：{row["assigned_station"]}']
    if current_station and row["assigned_station"] != current_station:
        lines.append("此序未加工")
    if other:
        lines.extend((f'A Station：{other["station_A"]}', f'B Station：{other["station_B"]}',
                      f'工序差 B − A：{other["station_delta_B_minus_A"]}'))
    if "bend" in row:
        lines.extend(f"{key}：{value if value is not None else '—'}" for key,value in row["bend"].items())
    return "\n".join(lines)


def outline_markup(catalog, config):
    asset=catalog.get("display_outline")
    if not asset:return ""
    p=config["outline"];colors=config["colors"];precision=config["svg"]["numeric_decimals"]
    pieces=['<g class="part-outline-layer" pointer-events="none" aria-hidden="true">']
    for loop in asset["loops"]:
        shapes=loop["shapes"];start=shapes[0]["start"]
        commands=[f'M {number(start[0],precision)} {number(start[1],precision)}']
        for shape in shapes:
            # Source endpoint topology is already closed; retain its original curves.
            markup=shape_markup(shape,precision)
            d=re.search(r'd="([^"]+)"',markup).group(1)
            commands.append(re.sub(r'^M\s+\S+\s+\S+\s+','',d))
        commands.append("Z")
        pieces.append(f'<path class="part-outline" d="{" ".join(commands)}" fill="{colors["outline_fill"]}" fill-opacity="{p["fill_opacity"]}" stroke="{colors["outline_stroke"]}" stroke-width="{p["stroke_width"]}" stroke-opacity="{p["stroke_opacity"]}" vector-effect="non-scaling-stroke"/>')
    pieces.append("</g>")
    return "".join(pieces)


def render_svg(catalog, view, station_id, config, *, prefix="view", mode="CURRENT_ONLY",
               color_mode="BY_CURRENT_STATUS", changed_ids=None, difference_only=False,
               region_filter="ALL", show_influence=False, selected_operation=None, comparison_rows=None,rotation_degrees=None):
    if station_id not in view["station_ranks"]:
        raise ValueError("Unknown Station")
    rank = view["station_ranks"][station_id]
    precision = config["svg"]["numeric_decimals"]
    rotation=config["display"]["default_rotation_degrees"] if rotation_degrees is None else rotation_degrees
    box = " ".join(number(v,precision) for v in display_box(catalog["view_box"],rotation))
    pieces = [f'<svg xmlns="{SVG_NS}" class="process-canvas" data-rotation="{rotation}" viewBox="{box}" preserveAspectRatio="xMidYMid meet" role="img" aria-label="二维展开图 {escape(station_id)}">',
              f'<g class="display-scene" transform="{display_matrix(rotation)}">',outline_markup(catalog,config)]
    for rid,r in sorted(catalog["regions"].items()):
        if not r["bbox"]:
            continue
        x0,y0,x1,y1 = r["bbox"]
        if x1<=x0 or y1<=y0:
            continue
        opacity = config["svg"]["influence_selected_opacity"] if rid == region_filter else config["svg"]["influence_opacity"]
        visible = show_influence and (region_filter == "ALL" or rid == region_filter)
        pieces.append(f'<rect class="influence-region" data-region="{escape(rid)}" x="{number(x0,precision)}" y="{number(-y1,precision)}" width="{number(x1-x0,precision)}" height="{number(y1-y0,precision)}" fill="{config["colors"]["influence"]}" fill-opacity="{opacity}" stroke="{config["colors"]["influence"]}" stroke-opacity="{opacity}" stroke-width="{config["svg"]["influence_stroke_width"]}" vector-effect="non-scaling-stroke" pointer-events="none" visibility="{"visible" if visible else "hidden"}"/>')
    comparison_rows = comparison_rows or {}
    for op,geometry in catalog["operations"].items():
        if not geometry["renderable"]:
            continue
        row = view["operations"][op]
        focus = operation_focus(op,catalog,changed_ids=changed_ids,difference_only=difference_only,region_filter=region_filter)
        style = operation_style(row,rank,config,mode=mode,color_mode=color_mode,focus=focus,selected=op==selected_operation)
        title = escape(operation_title(row,comparison_rows.get(op),station_id))
        shapes = "".join(shape_markup(shape,precision) for shape in geometry["shapes"])
        shapes = re.sub(r"<(path|circle) ",r'<\1 vector-effect="non-scaling-stroke" ',shapes)
        pieces.append(f'<g id="{target_id(prefix,op)}" class="operation" data-operation="{escape(op)}" data-selected="{"true" if op==selected_operation else "false"}" data-status="{style["status"]}" data-focus="{focus}" tabindex="0" role="button" aria-label="{escape(op)} {escape(row["operation_type"])}"><title>{title}</title><g class="visible-geometry" fill="none" stroke="{style["stroke"]}" stroke-width="{style["stroke_width"]}" opacity="{style["opacity"]}" pointer-events="none">{shapes}</g><g class="hit-geometry" fill="none" stroke="transparent" stroke-width="{config["svg"]["hit_stroke_width"]}" pointer-events="stroke">{shapes}</g></g>')
    pieces.append("</g></svg>")
    return "".join(pieces)
