"""Static SVG and optional Pillow PNG previews of the same view state."""
from html import escape
import math

from .plan_view_model import operation_focus
from .svg_renderer import SVG_NS, operation_style, render_svg
from .display_transform import display_box, display_point, orientation_note
from .outline_adapter import flatten_shape


def preview_svg(catalog, views, station, config, *, title, changed_ids=None, **state):
    p = config["previews"]
    width,height,top = p["panel_width_px"],p["panel_height_px"],p["title_height_px"]
    total = width*len(views)
    axis_note="两侧同一比例与视口" if len(views)==2 else "工程展开坐标"
    rotation=state.get("rotation_degrees",config["display"]["default_rotation_degrees"])
    result = [f'<svg xmlns="{SVG_NS}" viewBox="0 0 {total} {height+top}" width="{total}" height="{height+top}"><rect width="100%" height="100%" fill="{config["colors"]["paper"]}"/>',
              f'<text x="{p["label_padding_x_px"]}" y="{p["svg_title_baseline_px"]}" font-family="Microsoft YaHei,Segoe UI,sans-serif" font-size="{p["title_font_px"]}" fill="{config["colors"]["background"]}">{escape(title)}</text>',
              f'<text x="{p["label_padding_x_px"]}" y="{p["svg_note_baseline_px"]}" font-family="Microsoft YaHei,Segoe UI,sans-serif" font-size="{p["note_font_px"]}" fill="{config["colors"]["background"]}">{orientation_note(rotation)} · {escape(station)} · {axis_note}</text>']
    for index,view in enumerate(views):
        svg=render_svg(catalog,view,station,config,prefix=f"preview-{index}",changed_ids=changed_ids,**state)
        svg=svg.replace('class="process-canvas"',f'class="process-canvas" x="{index*width}" y="{top}" width="{width}" height="{height}"',1)
        result.append(svg)
        label=("方案 "+("A" if index==0 else "B")+" · " if len(views)==2 else "方案 · ")+view["blind_id"]
        result.append(f'<text x="{index*width+p["label_padding_x_px"]}" y="{top+p["panel_label_baseline_px"]}" font-family="Microsoft YaHei,Segoe UI,sans-serif" font-size="{p["note_font_px"]}" fill="{config["colors"]["background"]}">{escape(label)}</text>')
    result.append("</svg>")
    return "".join(result)


def preview_png(catalog, views, station, config, *, title, changed_ids=None, **state):
    """Raster convenience only; authoritative geometry remains exact SVG."""
    from PIL import Image, ImageDraw, ImageFont
    from pathlib import Path
    p=config["previews"]; scale=p["raster_scale"]
    width,height,top= int(p["panel_width_px"]),int(p["panel_height_px"]),int(p["title_height_px"])
    image=Image.new("RGB",(int(width*len(views)*scale),int((height+top)*scale)),config["colors"]["paper"])
    draw=ImageDraw.Draw(image)
    axis_note="两侧同一比例与视口" if len(views)==2 else "工程展开坐标"
    rotation=state.get("rotation_degrees",config["display"]["default_rotation_degrees"])
    font_path=Path("C:/Windows/Fonts/msyh.ttc")
    font=ImageFont.truetype(str(font_path),int(p["title_font_px"]*scale)) if font_path.exists() else ImageFont.load_default()
    draw.text((p["label_padding_x_px"]*scale,p["raster_title_top_px"]*scale),title,fill=config["colors"]["background"],font=font)
    note=ImageFont.truetype(str(font_path),int(p["note_font_px"]*scale)) if font_path.exists() else ImageFont.load_default()
    draw.text((p["label_padding_x_px"]*scale,p["raster_note_top_px"]*scale),f'{orientation_note(rotation)} · {station} · {axis_note}',fill=config["colors"]["background"],font=note)
    vx,vy,vw,vh=display_box(catalog["view_box"],rotation)
    factor=min(width/vw,height/vh)
    for index,view in enumerate(views):
        ox=index*width+(width-vw*factor)/2; oy=top+(height-vh*factor)/2
        label=("方案 "+("A" if index==0 else "B")+" · " if len(views)==2 else "方案 · ")+view["blind_id"]
        draw.text(((index*width+p["label_padding_x_px"])*scale,(top+p["panel_label_top_px"])*scale),label,fill=config["colors"]["background"],font=note)
        pixel=lambda xy: ((ox+(xy[0]-vx)*factor)*scale,(oy+(xy[1]-vy)*factor)*scale)
        point=lambda xy:pixel(display_point(xy,rotation))
        if catalog.get("display_outline"):
            p_outline=config["outline"]
            fill=tuple(int(config["colors"]["outline_fill"][i:i+2],16) for i in (1,3,5))+(int(p_outline["fill_opacity"]*255),)
            stroke=tuple(int(config["colors"]["outline_stroke"][i:i+2],16) for i in (1,3,5))+(int(p_outline["stroke_opacity"]*255),)
            overlay=Image.new("RGBA",image.size,(0,0,0,0));od=ImageDraw.Draw(overlay)
            for loop in catalog["display_outline"]["loops"]:
                polygon=[point(v) for shape in loop["shapes"] for v in flatten_shape(shape,p_outline["containment_flatten_tolerance"])[:-1]]
                od.polygon(polygon,fill=fill);od.line(polygon+[polygon[0]],fill=stroke,width=max(1,round(p_outline["stroke_width"]*scale)),joint="curve")
            image.paste(overlay,mask=overlay.getchannel("A"));draw=ImageDraw.Draw(image)
        if state.get("show_influence"):
            region_filter=state.get("region_filter","ALL")
            for rid,r in catalog["regions"].items():
                if not r["bbox"] or region_filter not in ("ALL",rid): continue
                x0,y0,x1,y1=r["bbox"]
                layer=Image.new("RGBA",image.size,(0,0,0,0)); ld=ImageDraw.Draw(layer)
                opacity=config["svg"]["influence_selected_opacity"] if rid==region_filter else config["svg"]["influence_opacity"]
                color=tuple(int(config["colors"]["influence"][i:i+2],16) for i in (1,3,5))+(int(opacity*255),)
                corners=[point(v) for v in ((x0,-y1),(x0,-y0),(x1,-y1),(x1,-y0))]
                ld.rectangle((min(v[0] for v in corners),min(v[1] for v in corners),max(v[0] for v in corners),max(v[1] for v in corners)),fill=color)
                image.paste(layer,mask=layer.getchannel("A")); draw=ImageDraw.Draw(image)
        for op,g in catalog["operations"].items():
            if not g["renderable"]: continue
            row=view["operations"][op]
            focus=operation_focus(op,catalog,changed_ids=changed_ids,difference_only=state.get("difference_only",False),region_filter=state.get("region_filter","ALL"))
            style=operation_style(row,view["station_ranks"][station],config,mode=state.get("mode","CURRENT_ONLY"),color_mode=state.get("color_mode","BY_CURRENT_STATUS"),focus=focus,selected=op==state.get("selected_operation"))
            rgb=tuple(int(style["stroke"][i:i+2],16) for i in (1,3,5))
            color=tuple(round(channel*style["opacity"]+255*(1-style["opacity"])) for channel in rgb)
            stroke=max(1,round(style["stroke_width"]*scale))
            for shape in g["shapes"]:
                if shape["kind"]=="line": draw.line((*point(shape["start"]),*point(shape["end"])),fill=color,width=stroke)
                else:
                    cx,cy=shape["center"];radius=shape["radius"];center=point((cx,cy));r=radius*factor*scale
                    bounds=(center[0]-r,center[1]-r,center[0]+r,center[1]+r)
                    if shape["kind"]=="circle": draw.ellipse(bounds,outline=color,width=stroke)
                    else:
                        start=math.degrees(shape["start_angle"]);sweep=math.degrees(shape["sweep"]);offset=90 if rotation==270 else 0
                        a,b=(offset-(start+sweep),offset-start) if sweep>0 else (offset-start,offset-(start+sweep))
                        draw.arc(bounds,start=a,end=b,fill=color,width=stroke)
    return image.resize((width*len(views),height+top),Image.Resampling.LANCZOS)
