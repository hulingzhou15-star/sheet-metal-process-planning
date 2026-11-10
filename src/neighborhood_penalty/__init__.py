"""Versioned neighborhood-first heuristic; no expert input or automatic labels."""
from .config import load_config
from .catalogue import build_catalogue, evaluate_p5
from .scoring import Scorer, BoundError
from .generation import establish_primary, generate_candidates, select_candidates
