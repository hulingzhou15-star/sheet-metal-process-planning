"""Resolve source companion manifests after moving DXFs into sources/<part>."""
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1]


def companion_root(source_path):
    source = Path(source_path).resolve()
    if source.is_relative_to(MODULE / 'sources'):
        return MODULE
    # Standalone source fixtures keep their companion files beside the DXF.
    return source.parent
