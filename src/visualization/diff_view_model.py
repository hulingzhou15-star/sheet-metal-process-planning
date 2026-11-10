"""Objective A/B movement summaries. No new process relations or scores."""
from collections import Counter, defaultdict

from src.review.plan_diff import comparison_diff


def build_diff_view_model(context, assignments_a, assignments_b, catalog):
    diff = comparison_diff(context, assignments_a, assignments_b)
    ranks = context.station_ranks
    groups, transitions = defaultdict(list), Counter()
    for row in diff["changed_operations"]:
        row["station_delta_B_minus_A"] = ranks[row["station_B"]]-ranks[row["station_A"]]
        row["region_group"] = catalog["membership"][row["operation_id"]]
        groups[row["region_group"]].append(row)
        transitions[(row["station_A"], row["station_B"])]+=1
    for row in diff["bend_changes"]:
        row["station_delta_B_minus_A"] = ranks[row["station_B"]]-ranks[row["station_A"]]
        region = catalog["regions"].get(row["region_id"])
        row["region_level"] = region["region_level"] if region else None
    region_rows = [{"region_group": group, "changed_operation_count": len(rows),
                    "operation_type_distribution": dict(sorted(Counter(r["operation_type"] for r in rows).items())),
                    "station_transitions": dict(sorted(Counter(f'{r["station_A"]} → {r["station_B"]}' for r in rows).items())),
                    "operation_ids": [r["operation_id"] for r in rows]} for group, rows in sorted(groups.items())]
    transition_rows = [{"station_A": a, "station_B": b, "count": count} for (a,b),count in sorted(transitions.items(), key=lambda item:(ranks[item[0][0]],ranks[item[0][1]]))]
    earlier = sum(r["station_delta_B_minus_A"] > 0 for r in diff["bend_changes"])
    later = sum(r["station_delta_B_minus_A"] < 0 for r in diff["bend_changes"])
    summary = [f'方案 A 与 B 共 {diff["differing_operation_count"]} 个 Operation 分配不同。']
    summary.extend(f'{r["operation_type"]}：{r["differing_operation_count"]} 个。' for r in diff["operation_type_summary"] if r["differing_operation_count"])
    summary.append(f'{earlier} 个 BEND 在 A 中比 B 更早；{later} 个 BEND 在 A 中比 B 更晚。')
    return {**diff, "changed_ids": [r["operation_id"] for r in diff["changed_operations"]],
            "station_transitions": transition_rows, "region_differences": region_rows,
            "bend_A_earlier_count": earlier, "bend_A_later_count": later, "summary": summary}
