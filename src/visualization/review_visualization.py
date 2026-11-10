"""Reuse v0.3 cards/forms, add a shared geometry-first offline SVG workspace."""
from html import escape
from html.parser import HTMLParser
import json
from pathlib import Path
import re

from src.review.blinding import leakage_findings
from src.review.report import (BEND_COLUMNS, DIFF_COLUMNS, LOAD_COLUMNS, TYPE_COLUMNS,
                               form_html, html_document, html_table)

from .svg_renderer import render_svg
from .display_transform import orientation_note

UI_SCRIPT = Path(__file__).with_name("review_ui.js")
ANSWER_SCRIPT = Path(__file__).with_name("review_answers.js")


def public_payload(catalog, plans, comparisons, config, *, single_plan=None):
    # Never include catalog audit, candidate records, source maps or selection metadata.
    return {"geometry": {key: catalog[key] for key in ("view_box", "bbox", "operations", "regions", "membership")},
            "plans": plans, "comparisons": [{key: row[key] for key in ("comparison_id", "blind_plan_A", "blind_plan_B", "diff")} for row in comparisons],
            "station_ids": next(iter(plans.values()))["station_ids"],
            "config":{key:value for key,value in config.items() if key not in ("outline","previews")}, "single_plan": single_plan}


def visualization_css(config):
    c=config["layout"]
    variables={"canvas-height":c["canvas_height_px"],"panel-gap":c["panel_gap_px"],"page-padding":c["page_padding_px"],
               "small-font":c["summary_font_px"],"base-font":c["base_font_px"],"heading-font":c["heading_font_px"],
               "panel-font":c["panel_font_px"],"list-height":c["list_height_px"],"gap":c["control_gap_px"],
               "small-gap":c["small_gap_px"],"pad-y":c["control_padding_y_px"],"pad-x":c["control_padding_x_px"],
               "panel-pad":c["panel_padding_px"],"detail-pad":c["detail_padding_px"],"radius":c["corner_radius_px"],
               "border-width":c["border_width_px"],"legend-dot":c["legend_dot_px"],
               "label-min":c["metadata_label_min_px"],"label-max":c["metadata_label_max_px"]}
    root=":root{"+"".join(f"--{name}:{value}px;" for name,value in variables.items())
    root+="".join(f"--ui-{name}:{value};" for name,value in config["colors"]["ui"].items())
    root+="".join(f"--{name}:{config['colors'][name]};" for name in ("current","selected","paper"))
    root+=f"--shape-hover:{config['colors']['hover']};--hover-stroke:{config['svg']['hover_stroke_width']}px;--hover-opacity:{config['svg']['hover_opacity']};"
    root+=f"--disabled:{c['disabled_opacity']};--leading:{c['line_height']};}}"
    css="""
body{font-size:var(--base-font);color:var(--ui-text);background:var(--ui-page)}[hidden]{display:none!important}header{padding:var(--page-padding)}header h1{font-size:var(--heading-font)}main{margin:0 auto;padding:var(--page-padding)}
button,select{font:inherit}button{cursor:pointer;border:var(--border-width) solid var(--ui-border);border-radius:var(--radius);background:var(--paper);color:var(--ui-text);padding:var(--pad-y) var(--pad-x)}button:hover{background:var(--ui-hover)}button:disabled{opacity:var(--disabled);cursor:default}button.active{background:var(--current);color:var(--paper);border-color:var(--current)}button.selected{border-color:var(--selected);background:var(--ui-selection_fill)}
.comparison-nav,.controls,.station-controls{display:flex;gap:var(--gap);flex-wrap:wrap;align-items:center;margin:var(--pad-x) 0}.comparison-nav button{font-size:var(--small-font)}.toolbar{background:var(--paper);border:var(--border-width) solid var(--ui-border);padding:var(--panel-pad);border-radius:var(--radius)}.controls label{display:inline-flex;align-items:center;gap:var(--gap)}.canvas-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:var(--panel-gap);margin-top:var(--panel-gap)}.canvas-grid.single{grid-template-columns:1fr}.canvas-panel{min-width:0;background:var(--paper);border:var(--border-width) solid var(--ui-border);border-radius:var(--radius);padding:var(--panel-pad)}.canvas-panel h2{font-size:var(--panel-font);margin:0 0 var(--gap)}.process-canvas{display:block;width:100%;height:var(--canvas-height);border:var(--border-width) solid var(--ui-border);background:var(--paper);touch-action:none;cursor:grab}.operation:focus{outline:none}.stage-count{font-weight:600;margin-top:var(--pad-x)}.stage-types{font-size:var(--small-font);line-height:var(--leading);color:var(--ui-muted)}.stage-list{display:flex;flex-wrap:wrap;align-content:flex-start;gap:var(--small-gap);max-height:var(--list-height);overflow:auto;margin-top:var(--gap)}.stage-list button{font-size:var(--small-font)}.legend{display:flex;gap:var(--panel-pad);flex-wrap:wrap;font-size:var(--small-font);margin:var(--panel-pad) 0;color:var(--ui-muted)}.legend>span{display:inline-flex;gap:var(--small-gap);align-items:center}.legend i{width:var(--legend-dot);height:var(--legend-dot);display:inline-block}.metadata{background:var(--paper);border:var(--border-width) solid var(--ui-border);padding:var(--detail-pad);margin:var(--detail-pad) 0;border-radius:var(--radius)}dl{display:grid;grid-template-columns:minmax(var(--label-min),var(--label-max)) 1fr;gap:var(--small-gap) var(--detail-pad);word-break:break-word;font-size:var(--small-font)}dt{color:var(--ui-muted)}dd{margin:0}.trajectory{display:flex;gap:var(--gap);flex-wrap:wrap;margin-top:var(--panel-pad)}.trajectory-station{border:var(--border-width) solid var(--ui-border);padding:var(--pad-y) var(--pad-x);border-radius:var(--radius)}.trajectory b{display:inline-block;margin-left:var(--small-gap);background:var(--ui-hover);padding:0 var(--small-gap)}.muted{color:var(--ui-muted)}.difference-summary{line-height:var(--leading);margin:var(--panel-pad) 0}.comparison-details{background:var(--paper);border:var(--border-width) solid var(--ui-border);border-radius:var(--radius);padding:var(--detail-pad)}.comparison-details>details{margin:var(--pad-x) 0}.comparison-details summary{cursor:pointer;font-weight:600;padding:var(--small-gap) 0}.form{margin-top:var(--page-padding)}.plan-directory{margin:var(--detail-pad) 0;line-height:var(--leading)}.debug{border:var(--border-width) dashed var(--selected);padding:var(--panel-pad);background:var(--ui-debug_fill)}
"""
    css+='''
.operation[data-focus="primary"][data-selected="false"]:is(:hover,:focus-visible)>.visible-geometry{stroke:var(--shape-hover);stroke-width:var(--hover-stroke);opacity:var(--hover-opacity)}
.operation[data-selected="true"]:is(:hover,:focus-visible)>.visible-geometry{stroke:var(--selected)}
.review-actions{background:var(--paper);border:var(--border-width) solid var(--ui-border);border-radius:var(--radius);padding:var(--panel-pad);margin:var(--panel-pad) 0}.review-actions p{margin:var(--small-gap) 0;font-size:var(--small-font)}.review-actions strong{margin-right:var(--gap)}.comparison-nav .review-answered::after{content:' ✓';color:var(--current)}.comparison-nav .review-answered.active::after{color:var(--paper)}
'''
    return root+css+f"main{{max-width:{c['max_width_px']}px}}@media(max-width:{c['mobile_breakpoint_px']}px){{.canvas-grid{{grid-template-columns:1fr}}dl{{grid-template-columns:1fr}}}}"


def pair_detail_block(row):
    diff=row["diff"]
    blocks=[("OperationType 汇总",TYPE_COLUMNS,diff["operation_type_summary"]),
            ("Station load",LOAD_COLUMNS,diff["station_load_comparison"]),
            (f'分配不同的 Operation（{diff["differing_operation_count"]}）',(*DIFF_COLUMNS,("station_delta_B_minus_A","工序差 B − A")),diff["changed_operations"]),
            (f'BEND 专项（{len(diff["bend_changes"])}）',(*BEND_COLUMNS,("station_delta_B_minus_A","工序差 B − A")),diff["bend_changes"]),
            ("区域差异",(("region_group","区域"),("changed_operation_count","不同动作数"),("operation_type_distribution","类型分布"),("station_transitions","A → B 分配变化")),diff["region_differences"]),
            ("工序转移",(("station_A","A Station"),("station_B","B Station"),("count","动作数")),diff["station_transitions"])]
    return f'<section class="comparison-details" data-comparison-details="{escape(row["comparison_id"])}" hidden>'+"".join(f'<details><summary>{escape(title)}</summary>{html_table(columns,rows)}</details>' for title,columns,rows in blocks)+form_html(row["comparison_id"])+"</section>"


def render_visual_page(catalog, plans, comparisons, config, *, single_plan=None, debug_by_blind=None):
    payload=public_payload(catalog,plans,comparisons,config,single_plan=single_plan)
    is_pair=bool(comparisons)
    if bool(single_plan)==is_pair:
        raise ValueError("Choose comparison pack or one single Plan")
    if debug_by_blind is not None:
        for row in payload["comparisons"]:
            row["debug"]={"A":debug_by_blind[row["blind_plan_A"]],"B":debug_by_blind[row["blind_plan_B"]]}
    first=comparisons[0] if is_pair else None
    current=payload["station_ids"][0]
    shown=[plans[first["blind_plan_A"]],plans[first["blind_plan_B"]]] if is_pair else [plans[single_plan]]
    title="方案比较记录" if is_pair else "方案工序视图 · "+single_plan
    if debug_by_blind is not None: title+=" · 内部审阅"
    body=f'<header><h1>{escape(title)}</h1><p>二维展开图 · 按工序查看加工位置 · <span id="orientation-note">{orientation_note(config["display"]["default_rotation_degrees"])}</span></p><p>点击图形或动作列表查看详情。缩放和平移同步。</p>'
    if is_pair:
        body+='<p>请直接在本页填写判断、把握程度和备注，完成后点击“导出评审 CSV”，将下载的文件返回。页面尝试在当前浏览器本地暂存；不会改写随包的空白 CSV。</p>'
    body+='</header><main>'
    body+='<nav class="comparison-nav" id="comparison-nav" aria-label="比较导航">'+"".join(f'<button type="button" data-comparison-index="{i}">{escape(row["comparison_id"])}</button>' for i,row in enumerate(comparisons))+"</nav>"
    if is_pair:
        body+='<section class="review-actions" aria-label="评审结果"><div class="controls"><strong id="review-progress" aria-live="polite">已判断 0 对</strong><button type="button" id="review-focus-form">填写本对判断</button><button type="button" id="review-export-csv">导出评审 CSV</button></div><p id="review-save-status" role="status">正在检查本地暂存…</p><p id="review-export-status" role="status"></p></section>'
    body+=f'<h2 id="comparison-title">{escape(first["comparison_id"] if first else single_plan)}</h2>'
    body+='<div class="toolbar"><div class="station-controls" id="station-controls"><button type="button" id="previous-station">上一序</button>'+"".join(f'<button type="button" data-station-index="{i}" aria-pressed="{"true" if i==0 else "false"}" class="{"active" if i==0 else ""}">{escape(s)}</button>' for i,s in enumerate(payload["station_ids"]))+'<button type="button" id="next-station">下一序</button></div><div class="controls">'
    labels={"CURRENT_ONLY":"当前序 · CURRENT_ONLY","CUMULATIVE":"累计完成 · CUMULATIVE","FULL_PLAN_BY_STATION":"全方案按工序 · FULL_PLAN_BY_STATION"}
    body+='<label>显示<select id="display-mode">'+"".join(f'<option value="{mode}"{" selected" if mode==config["display"]["default_mode"] else ""}>{label}</option>' for mode,label in labels.items())+"</select></label>"
    body+='<label>颜色<select id="color-mode">'+"".join(f'<option value="{mode}"{" selected" if mode==config["display"]["default_color_mode"] else ""}>{label}</option>' for mode,label in (("BY_CURRENT_STATUS","加工状态"),("BY_OPERATION_TYPE","OperationType")))+"</select></label>"
    body+='<label>视角<select id="display-orientation"'+('' if config["display"]["enable_orientation_toggle"] else ' disabled')+'>'+"".join(f'<option value="{rotation}"{" selected" if rotation==config["display"]["default_rotation_degrees"] else ""}>{label}</option>' for rotation,label in ((270,"熟悉视角 · 270°"),(0,"原始视角")))+"</select></label>"
    body+=f'<label><input id="difference-only" type="checkbox"{" checked" if config["display"]["difference_only_default"] and is_pair else ""}{"" if is_pair else " disabled"}>只看 A/B 分配不同的 Operation</label>'
    body+=f'<label><input id="show-regions" type="checkbox"{" checked" if config["display"]["show_influence_region_default"] else ""}>显示 Bend influence region</label>'
    groups=["ALL",*sorted(catalog["regions"]),"OUTSIDE_REGION","NO_GEOMETRY"]
    if "UNRESOLVED_REGION" in catalog["membership"].values(): groups.append("UNRESOLVED_REGION")
    body+='<label>区域<select id="region-filter"'+('' if config["interaction"]["enable_region_filter"] else ' disabled')+'>'+"".join(f'<option value="{escape(g)}">{escape(g)}</option>' for g in groups)+"</select></label>"
    body+='<button type="button" id="zoom-out">− 缩小</button><button type="button" id="zoom-in">＋ 放大</button><button type="button" id="reset-view">全图</button><button type="button" id="fit-region">聚焦所选区域</button></div></div><div class="legend" id="legend"></div>'
    body+='<div class="canvas-grid'+('' if is_pair else ' single')+'">'
    for side,view in zip(("A","B"),shown):
        svg=render_svg(catalog,view,current,config,prefix=f"canvas-{side}",mode=config["display"]["default_mode"],color_mode=config["display"]["default_color_mode"],changed_ids=set(first["diff"]["changed_ids"]) if first else None,difference_only=config["display"]["difference_only_default"] and is_pair,show_influence=config["display"]["show_influence_region_default"])
        svg=svg.replace('class="process-canvas"',f'class="process-canvas" id="canvas-{side}"',1)
        body+=f'<section class="canvas-panel"><h2 id="plan-name-{side}">方案 {side if is_pair else ""} · {view["blind_id"]}</h2>{svg}<div class="stage-count" id="stage-count-{side}"></div><div class="stage-types" id="stage-types-{side}"></div><div class="stage-list" id="stage-list-{side}"></div>'
        if is_pair: body+=f'<p class="links"><a id="plan-link-{side}" href="plan_cards/{view["blind_id"]}.html" target="_blank" rel="noopener">单独查看此方案</a></p>'
        body+="</section>"
    body+='</div><p class="difference-summary" id="difference-summary"></p><section class="metadata"><h3>选中动作</h3><div id="operation-detail"></div><div class="trajectory" id="station-trajectory"></div></section>'
    if debug_by_blind is not None:
        body+='<section class="debug" id="debug-info"></section>'
        if not is_pair: body+=html_table((("field","Field"),("value","Value")),[{"field":k,"value":v} for k,v in debug_by_blind[single_plan].items()])
    if is_pair:
        body+="".join(pair_detail_block(row) for row in comparisons)
        body+='<details class="plan-directory"><summary>全部单方案视图</summary>'+" · ".join(f'<a href="plan_cards/{blind}.html">{blind}</a>' for blind in sorted(plans))+"</details>"
    else:
        view=shown[0]
        body+='<details><summary>Station load</summary>'+html_table((("station_id","Station"),("load","动作数")),view["station_loads"])+"</details>"
        matrix=[{"operation_type":r["operation_type"],**r["station_counts"]} for r in view["operation_type_station_matrix"]]
        body+='<details><summary>OperationType × Station</summary>'+html_table([("operation_type","OperationType"),*((s,s) for s in view["station_ids"])],matrix)+"</details>"
    body+='<noscript>本页交互需要启用本地 JavaScript。当前可查看初始工序二维图；静态预览另行提供。</noscript></main>'
    encoded=json.dumps(payload,ensure_ascii=False,separators=(",",":"),allow_nan=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")
    body+='<script id="visual-data" type="application/json">'+encoded+'</script><script>'+UI_SCRIPT.read_text(encoding="utf-8")+"</script>"
    if is_pair:
        body+='<script>'+ANSWER_SCRIPT.read_text(encoding="utf-8")+'</script>'
    return html_document(title,body).replace("</head>","<style>"+visualization_css(config)+"</style></head>")


def audit_visual_pack(folder, internal_ids, expected_comparisons, expected_plans):
    folder=Path(folder); files=[]; findings=[]
    class Parser(HTMLParser):
        def __init__(self):
            super().__init__(); self.tags=[]; self.textareas=[]; self.active_note=None; self.payload=[]; self.in_payload=False
        def handle_starttag(self,tag,attrs):
            attrs=dict(attrs); self.tags.append((tag,attrs))
            if tag=="textarea": self.active_note=[]; self.textareas.append(self.active_note)
            if tag=="script" and attrs.get("id")=="visual-data": self.in_payload=True
        def handle_endtag(self,tag):
            if tag=="textarea": self.active_note=None
            if tag=="script": self.in_payload=False
        def handle_data(self,text):
            if self.active_note is not None: self.active_note.append(text)
            if self.in_payload: self.payload.append(text)
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".html",".svg",".json",".csv",".md",".js",".css"): continue
        source=path.read_text(encoding="utf-8-sig"); files.append(path.relative_to(folder).as_posix())
        findings.extend({"file":files[-1],**row} for row in leakage_findings(source,internal_ids))
        for match in re.finditer(r"\b(?:NEAR|MEDIUM|FAR)\b",source,re.IGNORECASE): findings.append({"file":files[-1],"matched_text":match.group(0)})
        if path.suffix==".html":
            parser=Parser();parser.feed(source)
            ids=[attrs["id"] for tag,attrs in parser.tags if "id" in attrs]
            if len(ids)!=len(set(ids)): raise ValueError("Duplicate click/HTML target ID")
            for tag,attrs in parser.tags:
                for key in ("src","href"):
                    value=attrs.get(key,"")
                    if re.match(r"^(?:https?:|//|data:|file:|javascript:)",value,re.IGNORECASE): raise ValueError("Unexpected external or executable resource")
                    if value and not value.startswith("#") and not (path.parent/value).is_file(): raise ValueError("Broken local link")
                if tag=="input" and attrs.get("type")=="radio" and "checked" in attrs: raise ValueError("An answer was prefilled")
                if tag=="option" and "selected" in attrs and attrs.get("value") in ("1","2","3","4","5"): raise ValueError("Confidence was prefilled")
            if any("".join(note).strip() for note in parser.textareas): raise ValueError("A note was prefilled")
            clean=re.sub(r'https?://www\.w3\.org/2000/svg','',source)
            if re.search(r"https?://|@import|fetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon",clean,re.IGNORECASE): raise ValueError("Network dependency found")
            if path.name=="review_pack.html":
                data=json.loads("".join(parser.payload))
                if len(data["comparisons"])!=expected_comparisons or len(data["plans"])!=expected_plans: raise ValueError("Incomplete visual pack")
                if len([a for t,a in parser.tags if t=="input" and a.get("type")=="radio"])!=expected_comparisons*5: raise ValueError("Missing blank comparison forms")
                boxes=[a["viewbox"] for t,a in parser.tags if t=="svg"]
                if len(boxes)!=2 or boxes[0]!=boxes[1]: raise ValueError("A/B viewBox differs")
    return {"passed":not findings,"file_count":len(files),"checked_files":files,"findings":findings,
            "comparisons":expected_comparisons,"plans":expected_plans,"network_resources":0,"all_answers_blank":True}
