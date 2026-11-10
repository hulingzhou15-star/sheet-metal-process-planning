"""Auditable internal reports and allow-listed, offline human-facing cards."""
import csv
from html import escape
from html.parser import HTMLParser
from itertools import combinations
import json
from pathlib import Path
import statistics
from collections import Counter
import re

from .blinding import audit_expert_facing, blank_comparison_form
from .config import ANSWER_OPTIONS, QUESTION
from .plan_diff import comparison_diff, internal_plan_diff, plan_view


def distribution(values):
    values = list(values)
    return {"count": len(values), "minimum": min(values) if values else None,
            "median": statistics.median(values) if values else None,
            "mean": statistics.mean(values) if values else None, "maximum": max(values) if values else None}


def render_value(value, decimals=6):
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{decimals}f}".rstrip("0").rstrip(".") or "0"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) or "—"
    if isinstance(value, dict):
        return "; ".join(f"{k}: {v}" for k, v in value.items()) or "—"
    return str(value)


def markdown_table(columns, rows, decimals=6):
    # columns are (machine field, human label); values remain factual.
    result = ["| " + " | ".join(label for key, label in columns) + " |",
              "| " + " | ".join("---" for _ in columns) + " |"]
    result.extend("| " + " | ".join(render_value(row.get(key), decimals).replace("|", "\\|").replace("\n", " ")
                                     for key, label in columns) + " |" for row in rows)
    return "\n".join(result)+"\n\n"


def html_table(columns, rows, decimals=6):
    head = "".join(f"<th scope=\"col\">{escape(label)}</th>" for key, label in columns)
    body = "".join("<tr>"+"".join(f"<td>{escape(render_value(row.get(key), decimals))}</td>" for key, label in columns)+"</tr>" for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


LOAD_COLUMNS = (("station_id", "Station"), ("load_A", "A 动作数"), ("load_B", "B 动作数"), ("delta_B_minus_A", "B − A"))
DIFF_COLUMNS = (("operation_id", "Operation"), ("operation_type", "OperationType"), ("station_A", "A Station"), ("station_B", "B Station"))
BEND_COLUMNS = (("operation_id", "BEND"), ("station_A", "A Station"), ("station_B", "B Station"),
                ("affected_area_proxy", "影响面积代理值"), ("affected_depth", "影响深度"), ("region_id", "区域"), ("parent_region_id", "父区域"))
TYPE_COLUMNS = (("operation_type", "OperationType"), ("differing_operation_count", "不同动作数"))

CSS = """
:root{color-scheme:light;font-family:'Segoe UI','Microsoft YaHei',sans-serif;color:#243247;background:#f4f6f9}
*{box-sizing:border-box}body{margin:0}header{background:#fff;border-bottom:1px solid #dbe2ea;padding:28px max(24px,calc((100vw - 1180px)/2))}
header h1{font-size:28px;margin:0 0 10px}header p{max-width:920px;line-height:1.65;margin:6px 0;color:#526077}
main{max-width:1228px;margin:24px auto;padding:0 24px 50px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:18px 0 24px}
a{color:#245f91;text-decoration:none}a:hover{text-decoration:underline}nav a{border:1px solid #cbd5e1;border-radius:5px;background:#fff;padding:7px 10px;font-size:13px}
.pair{background:#fff;border:1px solid #d5dde7;border-radius:10px;margin:14px 0;scroll-margin-top:16px}.pair>summary{cursor:pointer;padding:18px 20px;font-weight:600;line-height:1.7}
.pair>summary span{font-size:13px;font-weight:400;color:#59677b;margin-left:12px}.pair-body{padding:0 20px 20px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:24px}
h2{font-size:20px;margin:22px 0 12px}h3{font-size:16px;margin:8px 0 12px}.table-scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px;white-space:nowrap}
th,td{padding:9px 11px;text-align:left;border-bottom:1px solid #e4e9ef}th{background:#eef2f6;color:#34445c}td:not(:first-child){font-variant-numeric:tabular-nums}
.detail{margin:16px 0;border-top:1px solid #e1e7ef;padding-top:12px}.detail>summary{cursor:pointer;font-weight:600;padding:8px 0}
.form{margin-top:22px;background:#f7f9fc;padding:18px;border-radius:7px}.form legend{font-weight:600;line-height:1.6}.form fieldset{border:0;padding:0;margin:0 0 16px}
.options{display:flex;flex-wrap:wrap;gap:12px 22px;margin-top:14px}.options label{display:flex;align-items:center;gap:7px;font-size:14px}.confidence{display:flex;align-items:center;gap:12px;margin:14px 0}
select,textarea{font:inherit;border:1px solid #c4cedb;border-radius:5px;padding:8px;background:white}textarea{width:100%;display:block;margin-top:8px;min-height:80px}
.links{margin:15px 0 0;line-height:1.7;font-size:14px}.plan-note{color:#657388;font-size:13px}section{margin-bottom:25px}
@media(max-width:760px){header{padding:22px 18px}main{padding:0 12px}.grid{grid-template-columns:1fr;gap:16px}.pair>summary span{display:block;margin-left:0}.pair-body{padding:0 14px 18px}}
@media print{nav,.form{display:none}.pair{break-inside:avoid}body{background:white}main{max-width:none}}
"""


def html_document(title, body):
    return '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'+\
           f'<title>{escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>\n'


def form_html(comparison_id):
    choices = "".join(f'<label><input type="radio" name="answer-{comparison_id}" value="{value}">{value}. {escape(text)}</label>'
                      for value, text in ANSWER_OPTIONS)
    options = '<option value="">未选择</option>'+"".join(f'<option value="{i}">{i}</option>' for i in range(1, 6))
    return f'<div class="form"><fieldset><legend>{escape(QUESTION)}</legend><div class="options">{choices}</div></fieldset>'+\
           f'<label class="confidence">把握程度（1–5）<select name="confidence-{comparison_id}">{options}</select></label>'+\
           f'<label>可选备注<textarea name="reason-note-{comparison_id}" rows="3"></textarea></label></div>'


def public_plan_cards(view, blind_id, decimals):
    load_columns = (("station_id", "Station"), ("load", "动作数"))
    matrix_columns = [("operation_type", "OperationType"), *((s, s) for s in view["station_ids"])]
    matrix = [{"operation_type": row["operation_type"], **row["station_counts"]} for row in view["operation_type_station_matrix"]]
    assignments_columns = (("operation_id", "Operation"), ("operation_type", "OperationType"), ("assigned_station", "Station"))
    bend_columns = (("bend_operation_id", "BEND"), ("assigned_station", "Station"), ("affected_area_proxy", "影响面积代理值"),
                    ("affected_depth", "影响深度"), ("region_id", "区域"), ("parent_region_id", "父区域"))
    parts = [("Station 动作数", load_columns, view["station_loads"]), ("OperationType × Station", matrix_columns, matrix),
             ("Operation 分配", assignments_columns, view["operation_assignments"]), ("BEND 分配", bend_columns, view["bend_assignments"])]
    md = f"# {blind_id}\n\n"+"".join(f"## {title}\n\n"+markdown_table(columns, rows, decimals) for title, columns, rows in parts)
    body = f'<header><h1>{blind_id}</h1><p>工艺方案分配表</p></header><main>'+\
           "".join(f'<section><h2>{escape(title)}</h2>{html_table(columns, rows, decimals)}</section>' for title, columns, rows in parts)+"</main>"
    return md, html_document(blind_id, body)


def public_pair_cards(comparisons, diffs, config):
    decimals = config["presentation"]["numeric_decimal_places"]
    md = "# 方案比较记录\n\n请选择每组方案的判断、把握程度与可选备注。空白记录尚未填写。\n\n"
    nav = "".join(f'<a href="#{row["comparison_id"]}">{row["comparison_id"]}</a>' for row in comparisons)
    body = '<header><h1>方案比较记录</h1>'+f'<p>{len(comparisons)} 组比较。先查看动作数量与类型汇总，再按需展开 Operation 和 BEND 差异。</p>'+\
           '<p>页面选项与备注只用于本次浏览，不会自动保存或提交。可另用空白 CSV 记录判断。</p></header>'+f'<main><nav aria-label="比较目录">{nav}</nav>'
    for index, comparison in enumerate(comparisons):
        cid, a, b = comparison["comparison_id"], comparison["blind_plan_A"], comparison["blind_plan_B"]
        diff = diffs[cid]
        md += f"## {cid}\n\n方案 A：{a}；方案 B：{b}。不同动作：{diff['differing_operation_count']}。\n\n"
        md += "### Station 动作数\n\n"+markdown_table(LOAD_COLUMNS, diff["station_load_comparison"], decimals)
        md += "### OperationType 汇总\n\n"+markdown_table(TYPE_COLUMNS, diff["operation_type_summary"], decimals)
        md += "### 不同 Operation\n\n"+markdown_table(DIFF_COLUMNS, diff["changed_operations"], decimals)
        md += "### BEND 差异\n\n"+markdown_table(BEND_COLUMNS, diff["bend_changes"], decimals)
        md += QUESTION+"\n\n"+"\n".join(f"- [ ] {value}. {text}" for value, text in ANSWER_OPTIONS)+\
              "\n\n把握程度（1/2/3/4/5）：________\n\n可选备注：________\n\n"+\
              f"完整分配：[方案 A](plan_cards/{a}.md)；[方案 B](plan_cards/{b}.md)。\n\n"
        opened = " open" if index < config["presentation"]["initial_open_comparisons"] else ""
        body += f'<details class="pair" id="{cid}"{opened}><summary>{cid}'+\
                f'<span>方案 A：{a} · 方案 B：{b} · 不同动作 {diff["differing_operation_count"]}</span></summary><div class="pair-body">'+\
                '<div class="grid"><section><h3>Station 动作数</h3>'+html_table(LOAD_COLUMNS, diff["station_load_comparison"], decimals)+\
                '</section><section><h3>OperationType 汇总</h3>'+html_table(TYPE_COLUMNS, diff["operation_type_summary"], decimals)+'</section></div>'+\
                f'<details class="detail"><summary>查看不同 Operation（{diff["differing_operation_count"]}）</summary>'+\
                html_table(DIFF_COLUMNS, diff["changed_operations"], decimals)+'</details>'+\
                f'<details class="detail"><summary>查看 BEND 差异（{len(diff["bend_changes"])}）</summary>'+\
                html_table(BEND_COLUMNS, diff["bend_changes"], decimals)+'</details>'+form_html(cid)+\
                f'<p class="links">完整分配：<a href="plan_cards/{a}.html" target="_blank" rel="noopener">方案 A</a> · '+\
                f'<a href="plan_cards/{b}.html" target="_blank" rel="noopener">方案 B</a></p></div></details>'
    body += "</main>"
    return md, html_document("方案比较记录", body)


def internal_diff_card(record, diff, decimals):
    md = f"# {record['internal_id']} / {record['blind_id']}\n\n供内部审计。\n\n"
    md += f"Bucket={record['bucket']}；role={record['selection_role']}；distance={record['distance_to_expert_raw']} / "+\
          f"{render_value(record['distance_to_expert_normalized'], decimals)}；SoftPenalty={render_value(record['total_soft_penalty'], decimals)}。\n\n"
    soft_rows = [{"component": name, **value} for name, value in record["soft_components"].items()]
    md += markdown_table((("component", "Component"), ("raw_component", "Raw"), ("weight", "Weight"), ("weighted_contribution", "Contribution")), soft_rows, decimals)
    md += "## Station load\n\n"+markdown_table((("station_id", "Station"), ("expert_load", "Expert"), ("candidate_load", "Candidate"), ("delta", "Delta")), diff["station_loads"], decimals)
    md += "## OperationType movement\n\n"+markdown_table((("operation_type", "Type"), ("total_operation_count", "Total"),
          ("changed_count", "Changed"), ("unchanged_count", "Unchanged"), ("station_transitions", "Transitions")), diff["operation_type_movement"], decimals)
    fields = (("operation_id", "Operation"), ("operation_type", "Type"), ("expert_station", "Expert Station"), ("candidate_station", "Candidate Station"),
              ("station_rank_delta", "Rank delta"), ("process_group_id", "Process group"), ("required_predecessor_ids", "Required predecessors"), ("precedence_sources", "Precedence participation"))
    md += "## 全部 changed Operation\n\n"+markdown_table(fields, diff["changed_operations"], decimals)
    bend_fields = [(key, key) for key in ("bend_operation_id", "expert_station", "candidate_station", "station_rank_delta", "bend_axis", "affected_depth", "bend_length", "affected_area_proxy", "influence_region_id", "parent_region_id", "region_level")]
    md += "## BEND changed subset\n\n"+markdown_table(bend_fields, diff["bend_changes"], decimals)
    md += "## Precedence-related changed Operation\n\n"+markdown_table(fields, diff["precedence_related_changed_operations"], decimals)
    return md


def safe_write(path, content, *, root):
    path, root = Path(path), Path(root).resolve()
    if not path.resolve().is_relative_to(root) or path.is_symlink() or (path.exists() and path.stat().st_nlink > 1):
        raise ValueError("Review output escapes destination or aliases another file")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def write_csv(path, fields, rows, *, root):
    import io
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, (dict, list, tuple)) else value
                         for key, value in row.items()})
    safe_write(path, "\ufeff"+stream.getvalue(), root=root)


def validate_public_html(folder, expected_comparisons):
    class SourceParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.tags, self.selects, self.textareas = [], [], []
            self.active_select = None
            self.active_textarea = None

        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            self.tags.append((tag, attrs))
            if tag == "select":
                self.active_select = []
                self.selects.append(self.active_select)
            if tag == "option" and self.active_select is not None:
                self.active_select.append(attrs)
            if tag == "textarea":
                self.active_textarea = []
                self.textareas.append(self.active_textarea)

        def handle_data(self, data):
            if self.active_textarea is not None:
                self.active_textarea.append(data)

        def handle_endtag(self, tag):
            if tag == "select":
                self.active_select = None
            if tag == "textarea":
                self.active_textarea = None

    folder = Path(folder)
    html_files = sorted(folder.rglob("*.html"))
    for path in html_files:
        text = path.read_text(encoding="utf-8")
        if re.search(r"https?://|@import|url\(", text, re.IGNORECASE):
            raise ValueError("Public HTML has a network resource")
    text = (folder / "review_pack.html").read_text(encoding="utf-8")
    parser = SourceParser()
    parser.feed(text)
    radios = [attrs for tag, attrs in parser.tags if tag == "input" and attrs.get("type") == "radio"]
    comparisons = [attrs for tag, attrs in parser.tags if tag == "details" and attrs.get("class") == "pair"]
    blanks = (all("checked" not in attrs for attrs in radios)
              and all(options and options[0].get("value") == "" and not any("selected" in o and o.get("value") for o in options) for options in parser.selects)
              and all(not "".join(parts).strip() for parts in parser.textareas))
    if len(comparisons) != expected_comparisons or len(radios) != expected_comparisons*len(ANSWER_OPTIONS) or not blanks:
        raise ValueError("Public HTML comparison count or blank form structure is invalid")
    if len(parser.selects) != expected_comparisons or len(parser.textareas) != expected_comparisons:
        raise ValueError("Public HTML confidence/note fields are incomplete")
    links = [attrs["href"] for tag, attrs in parser.tags if tag == "a" and attrs.get("href", "").startswith("plan_cards/")]
    if any(not (folder / link).is_file() for link in links):
        raise ValueError("Public HTML full-plan link is missing")
    return {"passed": True, "html_file_count": len(html_files), "comparison_count": len(comparisons),
            "radio_count": len(radios), "all_form_controls_blank": blanks, "confidence_fields": len(parser.selects),
            "note_fields": len(parser.textareas), "network_resources": 0, "full_plan_links_verified": len(links)}


def review_statistics(representatives, pairs, plans, distance):
    representative_distances = [{"internal_plan_1": a["internal_id"], "internal_plan_2": b["internal_id"],
                                 "raw": distance.raw(plans[a["internal_id"]]["assignments"], plans[b["internal_id"]]["assignments"]),
                                 "normalized": distance.normalized(plans[a["internal_id"]]["assignments"], plans[b["internal_id"]]["assignments"])}
                                for a, b in combinations(representatives, 2)]
    return {"representative_count": len(representatives), "bucket_counts": dict(Counter(r["bucket"] for r in representatives)),
            "representative_pairwise_distance": distribution(r["normalized"] for r in representative_distances),
            "representative_pairwise_raw_distance": distribution(r["raw"] for r in representative_distances),
            "representative_pairs": representative_distances,
            "representative_soft_penalty": distribution(r["total_soft_penalty"] for r in representatives),
            "representative_op60_load": distribution(r["op60_load"] for r in representatives),
            "comparison_count": len(pairs), "comparison_group_counts": dict(Counter(row["group"] for row in pairs)),
            "comparison_assignment_distance_raw": distribution(row["distance_plan1_plan2_raw"] for row in pairs),
            "comparison_assignment_distance_normalized": distribution(row["distance_plan1_plan2"] for row in pairs),
            "duplicate_unordered_pair_count": len(pairs)-len({tuple(sorted((p["internal_plan_1"], p["internal_plan_2"]))) for p in pairs})}


def write_review_pack(folder, context, plans, representatives, selection_audit, pairs, pair_audit, mapping, config, *, project_root):
    folder, project_root = Path(folder).resolve(), Path(project_root).resolve()
    if not folder.is_relative_to((project_root / "outputs").resolve()):
        raise ValueError("Review packs must be written below project outputs/")
    internal, public = folder / "internal", folder / "expert_facing"
    form_path = public / "comparison_pairs.csv"
    if form_path.exists():
        with form_path.open(encoding="utf-8-sig", newline="") as handle:
            if any(row.get(key) for row in csv.DictReader(handle) for key in ("answer", "confidence", "reason_note")):
                raise ValueError("Existing comparison form contains human entries; refuse to erase them")
    decimals = config["presentation"]["numeric_decimal_places"]
    baseline = plans["EXPERT_REFERENCE"]["assignments"]
    internal_diffs = {r["internal_id"]: internal_plan_diff(context, baseline, plans[r["internal_id"]]["assignments"]) for r in representatives}
    public_diffs = {row["comparison_id"]: comparison_diff(context, plans[row["internal_plan_A"]]["assignments"], plans[row["internal_plan_B"]]["assignments"]) for row in pairs}
    safe_write(internal / "blind_plan_mapping.json", json.dumps(mapping, ensure_ascii=False, indent=2)+"\n", root=folder)
    safe_write(internal / "representative_selection.json", json.dumps({"representatives": representatives, "audit": selection_audit}, ensure_ascii=False, indent=2)+"\n", root=folder)
    selection_columns = (("internal_id", "Internal ID"), ("blind_id", "Blind ID"), ("bucket", "Bucket"), ("selection_role", "Role"),
                         ("distance_to_expert_raw", "Expert distance"), ("total_soft_penalty", "SoftPenalty"), ("op60_load", "OP60 load"))
    selection_md = "# Representative selection（内部）\n\nR2 的 median/range 按移除 LOW_SOFT 后的剩余集合计算。\n\n"+markdown_table(selection_columns, representatives, decimals)
    safe_write(internal / "representative_selection_report.md", selection_md, root=folder)
    load_rows, type_rows, change_rows, bend_rows = [], [], [], []
    for record in representatives:
        diff = internal_diffs[record["internal_id"]]
        safe_write(internal / "diff_cards" / f"{record['internal_id']}.md", internal_diff_card(record, diff, decimals), root=folder)
        safe_write(internal / "diff_cards" / f"{record['internal_id']}.json", json.dumps({"representative": record, "diff": diff}, ensure_ascii=False, indent=2)+"\n", root=folder)
        for name, target in (("station_loads", load_rows), ("operation_type_movement", type_rows), ("changed_operations", change_rows), ("bend_changes", bend_rows)):
            target.extend({"internal_id": record["internal_id"], "blind_id": record["blind_id"], **row} for row in diff[name])
    for filename, rows, fields in (
        ("station_load_summary.csv", load_rows, ["internal_id", "blind_id", "station_id", "expert_load", "candidate_load", "delta"]),
        ("operation_type_movement_summary.csv", type_rows, ["internal_id", "blind_id", "operation_type", "total_operation_count", "changed_count", "unchanged_count", "station_transitions"]),
        ("changed_operations.csv", change_rows, ["internal_id", "blind_id", "operation_id", "operation_type", "expert_station", "candidate_station", "station_rank_delta", "process_group_id", "required_predecessor_ids", "precedence_sources", "EXPLICIT_PREDECESSOR", "PROCESS_GROUP", "BEND_REGION_PRECEDENCE"]),
        ("bend_changed_operations.csv", bend_rows, ["internal_id", "blind_id", "bend_operation_id", "expert_station", "candidate_station", "station_rank_delta", "bend_axis", "affected_depth", "bend_length", "affected_area_proxy", "influence_region_id", "parent_region_id", "region_level"]),
    ):
        write_csv(internal / filename, fields, rows, root=folder)
    pair_fields = ["comparison_id", "group", "subgroup", "internal_plan_1", "internal_plan_2", "internal_plan_left", "internal_plan_right",
                   "internal_plan_A", "internal_plan_B", "blind_plan_A", "blind_plan_B", "distance_plan1_plan2_raw", "distance_plan1_plan2",
                   "plan1_expert_distance", "plan2_expert_distance", "plan1_soft", "plan2_soft", "soft_absolute_difference", "expert_distance_absolute_difference", "selection_threshold"]
    write_csv(internal / "comparison_pairs_internal.csv", pair_fields, pairs, root=folder)
    safe_write(internal / "comparison_pairs_internal.json", json.dumps({"pairs": pairs, "audit": pair_audit}, ensure_ascii=False, indent=2)+"\n", root=folder)
    pair_columns = (("comparison_id", "Comparison"), ("group", "Group"), ("subgroup", "Design"), ("internal_plan_1", "Plan 1"), ("internal_plan_2", "Plan 2"),
                    ("distance_plan1_plan2_raw", "Assignment distance"), ("expert_distance_absolute_difference", "Expert-distance gap"),
                    ("soft_absolute_difference", "Soft gap"), ("selection_threshold", "Threshold"))
    pair_md = "# Comparison pair design（内部）\n\nA/B/C/D 各 6；全局排除重复 unordered pair。Group C 允许一个 Plan 多次参与。\n\n"+markdown_table(pair_columns, pairs, decimals)+\
              "## Group D threshold audit\n\n```json\n"+json.dumps(pair_audit["challenge_design"], ensure_ascii=False, indent=2)+"\n```\n"
    safe_write(internal / "comparison_pair_design.md", pair_md, root=folder)
    for blind_id, internal_id in mapping["blind_to_internal"].items():
        view = plan_view(context, plans[internal_id]["assignments"])
        md, html = public_plan_cards(view, blind_id, decimals)
        safe_write(public / "plan_cards" / f"{blind_id}.md", md, root=folder)
        safe_write(public / "plan_cards" / f"{blind_id}.html", html, root=folder)
        write_csv(public / "plan_cards" / f"{blind_id}.csv", ["operation_id", "operation_type", "assigned_station"], view["operation_assignments"], root=folder)
    md, html = public_pair_cards(pairs, public_diffs, config)
    safe_write(public / "review_pack.md", md, root=folder)
    safe_write(public / "review_pack.html", html, root=folder)
    forms = blank_comparison_form(pairs)
    write_csv(form_path, ["comparison_id", "plan_A", "plan_B", "answer", "confidence", "reason_note"], forms, root=folder)
    leakage = audit_expert_facing(public, plans)
    if not leakage["passed"]:
        raise ValueError(f"Blind leakage detected: {leakage['findings']}")
    html_validation = validate_public_html(public, len(pairs))
    return {"internal_diffs": internal_diffs, "public_diffs": public_diffs, "blinding_audit": leakage,
            "html_source_validation": html_validation,
            "blank_form_count": len(forms), "all_answers_blank": all(not row[key] for row in forms for key in ("answer", "confidence", "reason_note")),
            "expert_facing_html": str(public / "review_pack.html"), "expert_facing_form": str(form_path)}


def aggregate_report_markdown(report):
    records, stats = report["representatives"], report["statistics"]
    md = "# Human Review Pack v0.3（内部审计）\n\n本报告供研究者审计；对外材料位于 expert_facing。未生成或填写任何偏好答案。\n\n"
    columns = (("internal_id", "Internal ID"), ("blind_id", "Blind ID"), ("bucket", "Bucket"), ("selection_role", "Role"),
               ("distance_to_expert_raw", "Distance"), ("total_soft_penalty", "SoftPenalty"), ("op60_load", "OP60 load"))
    md += markdown_table(columns, records)
    md += f"每 bucket 代表数：{stats['bucket_counts']}。comparison group 数：{stats['comparison_group_counts']}。\n\n"
    md += "## Distribution\n\n"
    rows = [{"name": key, **stats[key]} for key in ("representative_pairwise_distance", "representative_pairwise_raw_distance", "representative_soft_penalty", "representative_op60_load", "comparison_assignment_distance_raw", "comparison_assignment_distance_normalized")]
    md += markdown_table((("name", "Metric"), ("count", "Count"), ("minimum", "Min"), ("median", "Median"), ("mean", "Mean"), ("maximum", "Max")), rows)
    md += "## Group D challenge pairs\n\n"+markdown_table((("comparison_id", "Comparison"), ("subgroup", "Kind"), ("internal_plan_1", "Plan 1"), ("internal_plan_2", "Plan 2"),
          ("distance_plan1_plan2_raw", "Assignment distance"), ("expert_distance_absolute_difference", "Expert-distance gap"), ("soft_absolute_difference", "Soft gap"), ("selection_threshold", "Threshold")),
          [row for row in report["comparison_pairs"] if row["group"] == "D"])
    md += f"重复 unordered pair：{stats['duplicate_unordered_pair_count']}。盲化检查通过：{report['blinding_audit']['passed']}，扫描 {report['blinding_audit']['file_count']} 个文件。\n\n"
    md += f"Frozen integrity：{report['frozen_integrity']['all_unchanged']}，{report['frozen_integrity']['checked_file_count']} 个文件。\n\n"
    md += "## 人工比较工作量\n\n"+f"每 pair 不同 Operation 最小/中位/最大：{report['comparison_readability']['differing_operation_count']}。\n\n"
    md += "默认卡片先展示 Station 与类型汇总，Operation/BEND 明细折叠；完整 Plan 分开查阅。所有选择、confidence 和备注仍为空，页面无后端与外部资源。\n"
    if report.get("html_validation"):
        md += "\n## HTML 验证范围\n\nHTML 结构、空白表单、离线资源及完整方案链接自动检查通过。\n\n"
        visual = report["html_validation"]["visual_preview"]
        if not visual["completed"]:
            md += f"浏览器视觉预览未完成：{visual['reason']}\n"
    return md
