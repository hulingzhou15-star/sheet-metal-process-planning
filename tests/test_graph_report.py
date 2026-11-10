from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from src.graph import build_part_graph, build_plan_overlay
from src.graph.report import build_statistics, graph_export, write_graph_outputs
from tests.graph_fixtures import part


class GraphReportTests(unittest.TestCase):
    def setUp(self):
        self.static = build_part_graph(part())
        self.overlay = build_plan_overlay(self.static, {"A": "OP90", "B": "OP10"})

    def test_report_json_serializable_and_feature_masks_recorded(self):
        stats = build_statistics(self.static, self.overlay)
        json.dumps(stats, allow_nan=False)
        self.assertEqual(stats["geometry_features_v1"]["has_geometry"]["max"], 0)
        self.assertEqual(stats["geometry_features_v1"]["geometry_area_norm"]["applicable_count"], 0)

    def test_graph0_export_does_not_expose_edges_or_plan_labels_in_static_vectors(self):
        exported = graph_export(self.static, self.overlay)
        self.assertEqual(exported["graphs"][0]["edges"], [])
        self.assertEqual(exported["graphs"][0]["plan_edge_features"], [])
        self.assertTrue(all(len(v) == 26 for v in exported["static_vectors"]))
        self.assertTrue(all(len(v) == 10 for v in exported["plan_vectors"]))

    def test_derived_write_and_readback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = write_graph_outputs(self.static, self.overlay, root / "outputs/m2", project_root=root)
            self.assertEqual(len(paths), 4)
            restored = json.loads(paths["static_part_graph.json"].read_text(encoding="utf-8"))
            self.assertEqual(restored["operation_ids"], ["A", "B"])
            self.assertNotIn("assigned_station", paths["static_part_graph.json"].read_text())

    def test_write_outside_outputs_and_raw_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for target in (root / "data/parts/P001", root / "data/processed", root / "src"):
                with self.assertRaises(ValueError):
                    write_graph_outputs(self.static, self.overlay, target, project_root=root)

    def test_hardlink_to_raw_rejected_without_modification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / "data/raw/source.xlsx"
            raw.parent.mkdir(parents=True)
            raw.write_bytes(b"original")
            output = root / "outputs/m2"
            output.mkdir(parents=True)
            (output / "static_part_graph.json").hardlink_to(raw)
            with self.assertRaises(ValueError):
                write_graph_outputs(self.static, self.overlay, output, project_root=root)
            self.assertEqual(raw.read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
