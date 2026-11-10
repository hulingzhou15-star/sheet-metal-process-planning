"""Readable internal sampling report. Expert-facing blinding is a later step."""
import csv
import json


def write_sampling_report(folder, context, pool, selected, contrasts):
    summary, changes = [], []
    for row in contrasts["comparisons"]:
        a, b = row["focus_operation_ids"]
        same, different = (row[k]["assignments"] for k in ("same_station_plan", "different_station_plan"))
        summary.append({"contrast_id": row["contrast_id"], "operation_A": a, "operation_B": b,
                        "same_station": same[a], "different_station_A": different[a], "different_station_B": different[b],
                        "changed_operation_count": len(row["all_changes"]), "answer": "", "reason_note": ""})
        for change in row["all_changes"]:
            op = change["operation_id"]
            changes.append({"contrast_id": row["contrast_id"], "operation_id": op,
                            "operation_type": change["operation_type"],
                            "same_role_station": same[op], "different_role_station": different[op]})
    for name, rows, fields in (
            ("local_comparison_summary.csv", summary, ("contrast_id", "operation_A", "operation_B", "same_station", "different_station_A", "different_station_B", "changed_operation_count", "answer", "reason_note")),
            ("local_operation_changes.csv", changes, ("contrast_id", "operation_id", "operation_type", "same_role_station", "different_role_station"))):
        with (folder/name).open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader(); writer.writerows(rows)
    lines = ["# 无 reference 候选与局部对照：首批结果", "", "这是内部采样与审计材料；尚未生成新的盲化专家评审页。", "",
             f"候选池 {len(pool)} 个，筛选 {len(selected)} 个。全部独立复核 H1–H7；不读取专家分配、专家负载或专家距离。",
             "筛选采用候选间最远优先，P1–P3 和方向仅输出诊断，没有以专家最低分为目标。", "",
             f"局部类型 pair 共 {contrasts['eligible_type_pair_count']} 对，按距离排名分层采样 {contrasts['sampled_pair_count']} 对，得到 {contrasts['comparison_count']} 组不重复分配比较。",
             "同一完整方案对可覆盖多个 focus pair，去重后保留所有来源。same/different 是内部构造角色，没有自动偏好标签。", "",
             "| 对照 | 关注 Operation | 同序 Station | 分序 Stations | 改变 Operation 数 |",
             "| --- | --- | --- | --- | ---: |"]
    for row in summary:
        lines.append(f"| {row['contrast_id']} | {row['operation_A']} / {row['operation_B']} | {row['same_station']} | {row['different_station_A']} / {row['different_station_B']} | {row['changed_operation_count']} |")
    lines += ["", "逐个动作变化见 [local_operation_changes.csv](local_operation_changes.csv)，完整分配和求解证据见 [local_contrasts.json](local_contrasts.json)。",
              "", "- [候选生成记录](generation_report.json)", "- [完整候选池](candidate_pool.jsonl)",
              "- [选中候选](selected_candidates.jsonl)", "- [专家信息隔离验证](expert_isolation.json)",
              "- [当前图覆盖](graph_coverage.json)"]
    for name, label in (("historical_graph_coverage.json", "历史问题方案图覆盖"),
                        ("historical_supervision_audit.json", "24 对监督审计")):
        path = folder/name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            lines.append(f"- [{label}]({name})")
            if name.startswith("historical_graph"):
                for plan_id, row in data["plans"].items():
                    lines.append(f"  - {plan_id}：临近类型 pair 同序 {row['same_station_pair_count']} 对，缺少 Graph2 边 {row['same_station_pairs_missing_graph2']} 对。pair 数不是已证实工艺冲突数。")
            else:
                lines.append(f"  - 24 对中 A/B 共 {data['explicit_preference_count']} 对，其中双方均满足新版硬约束 {data['both_v06_feasible_explicit_preference_count']} 对；D 不转换成胜负标签。")
    lines += ["", "这些材料完成生成与采样，不代表已训练模型或工艺质量已得到验证。当前局部对照来自一个基准候选，后续可扩展多个基准，不能据此宣称覆盖全部临近偏好。", "",
              "后续完整验证记录：[artifact_verification.json](artifact_verification.json)、[test_results.json](test_results.json)、[frozen_integrity.json](frozen_integrity.json)。"]
    (folder/"SAMPLING_REPORT.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
