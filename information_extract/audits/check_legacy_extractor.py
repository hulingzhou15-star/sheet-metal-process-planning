"""Review-only runner: legacy regression tests and the local P001 DXF.

This does not install/copy the legacy implementation, launch its CLI, export
workbooks, or run candidate generation. All audit writes stay beside this file.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LEGACY = Path("C:/Users/hulin/Desktop/新建文件夹/dxf_extractor")
AUDIT = ROOT / "audits"
SOURCE = ROOT / "Plotview 1.dxf"
TEST_FILES = ("test_extractor.py", "test_candidate_coordinates.py", "test_reconstruction.py")
CORE_FILES = (
    "README.md", "requirements.txt", "dxf_loader.py", "extractor.py",
    "entity_parser.py", "transform.py", "candidate_coordinates.py", "validate.py",
    "main.py", "export_excel.py", "manual_coordinate_validation.py",
    "reconstruct.py", "reconstruction_viewer.js", "reconstruction_viewer.html",
)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sys.dont_write_bytecode = True
    AUDIT.mkdir(exist_ok=True)
    scratch = AUDIT / "tmp"
    scratch.mkdir(exist_ok=True)
    tempfile.tempdir = str(scratch)
    sys.path.insert(0, str(AUDIT / "runtime_deps"))
    sys.path.insert(0, str(LEGACY))
    protected = [LEGACY / name for name in CORE_FILES]
    protected += [LEGACY / "tests" / name for name in TEST_FILES]
    protected += [SOURCE]
    before = {str(path): digest(path) for path in protected}

    capture = io.StringIO()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in TEST_FILES:
        suite.addTests(loader.discover(str(LEGACY / "tests"), pattern=name))
    with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
        tests = unittest.TextTestRunner(stream=capture, verbosity=2).run(suite)
    (AUDIT / "legacy_test_log.txt").write_text(capture.getvalue(), encoding="utf-8")

    import ezdxf
    from dxf_loader import load_dxf
    from extractor import Extractor
    from reconstruct import view_linear_from_context

    document, dxf_audit = load_dxf(SOURCE)
    extracted = Extractor(document).extract()
    local = extracted["block_local_coordinates"]
    frame_summary = [{key: frame[key] for key in (
        "block_name", "insert_path", "rotation", "xscale", "yscale",
        "transform_matrix_json",
    )} for frame in local["frames"]]
    linear, _ = view_linear_from_context(extracted)

    # Document the legacy coordinate contract with a controlled scaled circle.
    scaled = ezdxf.new()
    block = scaled.blocks.new("Plotview 1")
    block.add_circle((2, 3), 4)
    scaled.modelspace().add_blockref("Plotview 1", (100, 200),
                                   dxfattribs={"xscale": 2, "yscale": 2})
    scaled_result = Extractor(scaled).extract()
    circle = scaled_result["geometry"][0]
    try:
        view_linear_from_context(scaled_result)
        viewer_rejection = None
    except ValueError as error:
        viewer_rejection = str(error)

    after = {str(path): digest(path) for path in protected}
    summary = {
        "scope": "legacy reuse audit only; no application implementation or candidate generation",
        "python": sys.executable,
        "ezdxf_version": ezdxf.__version__,
        "legacy_root": str(LEGACY),
        "regression": {
            "files": list(TEST_FILES), "tests_run": tests.testsRun,
            "failures": len(tests.failures), "errors": len(tests.errors),
            "skipped": len(tests.skipped), "passed": tests.wasSuccessful(),
        },
        "p001": {
            "source": str(SOURCE), "sha256": before[str(SOURCE)],
            "dxf_version": document.dxfversion, "insunits": document.units,
            "stats": extracted["stats"], "validation": extracted["validation"],
            "dxf_audit": dxf_audit, "issues": extracted["issues"],
            "frames": frame_summary,
            "local_coordinate_checks": {key: local[key] for key in (
                "rows_with_local_coordinates", "rows_without_target_block",
                "inverse_point_checks", "max_wcs_roundtrip_error", "max_local_abs_z",
                "radius_and_sweep_coordinate_system",
            )},
            "viewer_linear_transform": list(linear),
        },
        "scaled_circle_contract_probe": {
            "input_local_radius": 4, "insert_scale": 2,
            "output_local_center": [circle["local_center_x"], circle["local_center_y"]],
            "output_radius_in_wcs": circle["radius"],
            "legacy_viewer_rejects_nonunit_frame": viewer_rejection is not None,
            "viewer_message": viewer_rejection,
            "interpretation": "Local point fields must not be combined with WCS radius/sweep without a frame adapter.",
        },
        "protected_files": before,
        "protected_files_unchanged": before == after,
        "changed_files": [path for path in before if before[path] != after[path]],
        "not_tested": [
            "legacy Excel writer and Node package setup", "real browser interactions",
            "historical special-purpose hole/surface-side/sweep-angle workflows",
            "new multi-selection, bend grouping, bridge absorption, Sheet5 and annotation persistence",
        ],
    }
    (AUDIT / "legacy_check_results.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({
        "regression": summary["regression"], "p001_stats": summary["p001"]["stats"],
        "geometry_validation": summary["p001"]["validation"],
        "insunits": document.units, "frames": frame_summary,
        "scaled_circle_contract_probe": summary["scaled_circle_contract_probe"],
        "protected_files_unchanged": summary["protected_files_unchanged"],
        "result": str(AUDIT / "legacy_check_results.json"),
    }, ensure_ascii=False, indent=2))
    return 0 if tests.wasSuccessful() and before == after and extracted["validation"]["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
