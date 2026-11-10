"""Explicit allow-list projections and byte-level disclosure checks."""
from copy import deepcopy
from pathlib import Path
import random
import re


def assign_blind_ids(internal_ids, seed):
    ids = list(internal_ids)
    if len(ids) != len(set(ids)):
        raise ValueError("Blind mapping requires unique internal IDs")
    ids.sort()
    random.Random(seed).shuffle(ids)
    blind_to_internal = {f"PLAN_A{i:02d}": internal for i, internal in enumerate(ids, 1)}
    return {"blind_to_internal": blind_to_internal,
            "internal_to_blind": {internal: blind for blind, internal in blind_to_internal.items()}, "plan_blinding_seed": seed}


def blind_comparisons(pairs, mapping, seed, *, shuffle_order=True):
    ordered = deepcopy(pairs)
    rng = random.Random(seed)
    if shuffle_order:
        rng.shuffle(ordered)
    result = []
    for index, pair in enumerate(ordered, 1):
        left, right = pair["internal_plan_1"], pair["internal_plan_2"]
        a, b = (right, left) if rng.getrandbits(1) else (left, right)
        result.append({**pair, "comparison_id": f"CMP_{index:03d}", "internal_plan_left": left,
                       "internal_plan_right": right, "internal_plan_A": a, "internal_plan_B": b,
                       "blind_plan_A": mapping["internal_to_blind"][a], "blind_plan_B": mapping["internal_to_blind"][b]})
    return result


def blank_comparison_form(comparisons):
    return [{"comparison_id": row["comparison_id"], "plan_A": row["blind_plan_A"], "plan_B": row["blind_plan_B"],
             "answer": "", "confidence": "", "reason_note": ""} for row in comparisons]


LEAK_PATTERNS = (
    r"NEAR_", r"MEDIUM_", r"FAR_", r"EXPERT_REFERENCE", r"SoftPenalty", r"soft[_\s-]*(?:penalty|score)",
    r"(?<![A-Za-z0-9_])P[123](?![A-Za-z0-9_])", r"expert", r"reference", r"generator[_\s-]*seed", r"solver",
    r"LOW_SOFT", r"TYPICAL", r"HIGH_OP60_LOAD", r"DIVERSITY", r"bucket", r"selection_role", r"distance_to_expert",
    r"(?:\.\./|\b)internal(?:/|\b)",
)


def leakage_findings(text, internal_ids=()):
    findings = []
    for expression in (*LEAK_PATTERNS, *(re.escape(identifier) for identifier in internal_ids)):
        match = re.search(expression, text, re.IGNORECASE)
        if match:
            findings.append({"pattern": expression, "matched_text": match.group(0), "offset": match.start()})
    return findings


def audit_expert_facing(folder, internal_ids=()):
    folder = Path(folder)
    files, findings = [], []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in (".md", ".csv", ".html"):
            files.append(path.relative_to(folder).as_posix())
            findings.extend({"file": path.relative_to(folder).as_posix(), **finding}
                            for finding in leakage_findings(path.read_text(encoding="utf-8-sig"), internal_ids))
    return {"passed": not findings, "file_count": len(files), "checked_files": files, "findings": findings,
            "prior_token_matching": "P1/P2/P3 as standalone tokens; OP10/OP20/OP30 are permitted Station IDs"}
