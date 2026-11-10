"""Reference-free facts, candidate exploration and training comparison sampling."""
from .input import InferenceContext, InferenceFacts, load_inference_context, validate_facts
from .generation import generate_candidates, select_diverse
from .contrasts import sample_local_contrasts

__all__ = ["InferenceContext", "InferenceFacts", "load_inference_context", "validate_facts",
           "generate_candidates", "select_diverse", "sample_local_contrasts"]
