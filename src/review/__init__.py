"""M3 v0.3 factual, blinded review preparation; answers remain blank."""
from .config import load_review_config
from .representative_selector import select_representatives
from .plan_diff import comparison_diff, internal_plan_diff, plan_view
from .pair_selector import select_pairs
from .blinding import assign_blind_ids, audit_expert_facing, blind_comparisons, blank_comparison_form

__all__ = ["load_review_config", "select_representatives", "comparison_diff", "internal_plan_diff", "plan_view",
           "select_pairs", "assign_blind_ids", "audit_expert_facing", "blind_comparisons", "blank_comparison_form"]
