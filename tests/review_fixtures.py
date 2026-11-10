from functools import lru_cache
from pathlib import Path

from scripts.build_human_review_pack import build_review_data, read_source_batch
from src.constraints import load_constraint_context
from src.review import load_review_config

ROOT = Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def batch():
    context = load_constraint_context(ROOT / "data/parts/P001/钣金加工数据表_P001.xlsx")
    config = load_review_config()
    candidates, expert, distance = read_source_batch(context, ROOT / "outputs/m3_candidates", config)
    return context, candidates, expert, distance, config


@lru_cache(maxsize=1)
def review_data():
    context, candidates, expert, distance, config = batch()
    return build_review_data(context, candidates, expert, distance, config)
