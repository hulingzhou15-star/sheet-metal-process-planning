from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ezdxf
from extract_app.drawing import load_drawing
from extract_app.model import ProjectStore, coverage, new_project, validate_project

ROOT = Path(__file__).resolve().parents[1]
CONFIG = json.loads((ROOT / "configs/app.json").read_text(encoding="utf-8"))


def circle(sid="c", radius=1):
    return {"id": sid, "segment_type": "CIRCLE", "center_x": 0., "center_y": 0.,
            "radius": radius, "bbox": [-radius, -radius, radius, radius], "source_group": sid}


def operation(uid="u", oid="O001", sources=None):
    return {"uid": uid, "operation_id": oid, "name": "测试圆孔", "operation_type": "CIRCLE_PUNCH",
            "surface_side": "NOT_APPLICABLE", "cad_parent_operation_id": None,
            "required_predecessor_ids": [], "process_group_id": None,
            "source_segment_ids": sources if sources is not None else ["c"],
            "geometry_mode": "source", "geometry_options": {}, "region": None}


class DrawingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = hashlib.sha256((ROOT / "sources/P001/Plotview 1.dxf").read_bytes()).hexdigest()
        cls.drawing = load_drawing(ROOT / "sources/P001/Plotview 1.dxf", CONFIG)

    def test_real_source_counts_and_fingerprint(self):
        self.assertEqual(len(self.drawing["segments"]), 2253)
        self.assertEqual(self.drawing["source"]["sha256"], self.before)
        self.assertEqual(hashlib.sha256((ROOT / "sources/P001/Plotview 1.dxf").read_bytes()).hexdigest(), self.before)
        self.assertEqual(len({s["id"] for s in self.drawing["segments"]}), 2253)
        self.assertEqual(self.drawing["issues"], [])

    def test_source_instance_and_signed_curves_available(self):
        arcs = [s for s in self.drawing["segments"] if s["segment_type"] == "ARC"]
        self.assertEqual(len(arcs), 280)
        self.assertTrue(all(s["source_handle"] and s["instance_path"] for s in arcs))
        self.assertTrue(all(len(s["bbox"]) == 4 for s in arcs))

    def test_scaled_circle_uses_material_radius(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = ezdxf.new()
            block = doc.blocks.new("Plotview 1")
            block.add_circle((2, 3), 4)
            doc.modelspace().add_blockref("Plotview 1", (100, 200), dxfattribs={"xscale": 2, "yscale": 2})
            source = Path(directory) / "test.dxf"
            doc.saveas(source)
            s = load_drawing(source, CONFIG)["segments"][0]
            self.assertAlmostEqual(s["center_x"], 2)
            self.assertAlmostEqual(s["center_y"], 3)
            self.assertAlmostEqual(s["radius"], 4)

    def test_mirrored_arc_recovers_material_sweep(self):
        with tempfile.TemporaryDirectory() as directory:
            doc = ezdxf.new()
            block = doc.blocks.new("Plotview 1")
            block.add_arc((0, 0), 2, 0, 270)
            doc.modelspace().add_blockref("Plotview 1", (100, 200), dxfattribs={"xscale": -1})
            source = Path(directory) / "test.dxf"
            doc.saveas(source)
            s = load_drawing(source, CONFIG)["segments"][0]
            self.assertAlmostEqual(s["sweep_angle"], 270)
            self.assertAlmostEqual(s["radius"], 2)
            self.assertAlmostEqual(s["start_x"], 2)


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.drawing = {"source": {"sha256": "test"}, "segments": [circle()], "issues": [], "config": CONFIG}
        self.project = new_project(self.drawing)
        self.project["operations"] = [operation()]

    def test_draft_and_source_fingerprint(self):
        report = validate_project(self.project, self.drawing)
        self.assertFalse(report["ready"])
        self.assertEqual(report["statistics"]["bound"], 1)
        invalid = deepcopy(self.project)
        invalid["source_sha256"] = "another"
        with self.assertRaisesRegex(ValueError, "指纹"):
            validate_project(invalid, self.drawing)

    def test_save_reload_revision_conflict_and_rename(self):
        child = operation("child", "O002")
        child["cad_parent_operation_id"] = "O001"
        child["required_predecessor_ids"] = ["O001"]
        self.project["operations"].append(child)
        self.project["fixed_rows"] = [{"operation_id": "O001", "fixed_station": "OP10"}]
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(directory, self.drawing, "P001")
            saved = store.save(self.project, 0)["project"]
            self.assertEqual(store.load(), saved)
            with self.assertRaisesRegex(ValueError, "冲突"):
                store.save(self.project, 0)
            saved["operations"][0]["operation_id"] = "O009"
            renamed = store.save(saved, 1)["project"]
            self.assertEqual(renamed["operations"][1]["cad_parent_operation_id"], "O009")
            self.assertEqual(renamed["operations"][1]["required_predecessor_ids"], ["O009"])
            self.assertEqual(renamed["fixed_rows"][0]["operation_id"], "O009")
            self.assertEqual(len(list((Path(directory) / "projects/P001/revisions").glob("*.json"))), 2)

    def test_cycle_and_duplicate_identifier(self):
        a, b = operation(), operation("second", "O002")
        a["cad_parent_operation_id"], b["cad_parent_operation_id"] = "O002", "O001"
        self.project["operations"] = [a, b]
        codes = {e["code"] for e in validate_project(self.project, self.drawing)["errors"]}
        self.assertIn("CAD_CYCLE", codes)
        b["operation_id"] = "O001"
        codes = {e["code"] for e in validate_project(self.project, self.drawing)["errors"]}
        self.assertIn("OPERATION_ID", codes)

    def test_other_part_cannot_replace_active_project(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(directory, self.drawing, "P001")
            old = store.save(self.project, 0)["project"]
            changed = deepcopy(old)
            changed["part_id"] = "P002"
            with self.assertRaisesRegex(ValueError, "零件编号"):
                store.save(changed, 1)
            self.assertEqual(store.load(), old)

    def test_history_write_failure_does_not_commit_project(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(directory, self.drawing, "P001")
            old = store.save(self.project, 0)["project"]
            changed = deepcopy(old)
            changed["operations"][0]["name"] = "新名称"
            original_write = Path.write_text
            def fail_history(path, *args, **kwargs):
                if path.parent.name == "revisions":
                    raise OSError("模拟修订存储失败")
                return original_write(path, *args, **kwargs)
            with patch.object(Path, "write_text", fail_history):
                with self.assertRaisesRegex(OSError, "修订存储失败"):
                    store.save(changed, 1)
            self.assertEqual(store.load(), old)
            self.assertFalse(list(store.directory.glob("*.tmp")))

    def test_changed_bend_sources_invalidate_unchanged_region_confirmation(self):
        self.project["operations"][0]["region"] = {"confirmed": True}
        with tempfile.TemporaryDirectory() as directory:
            store = ProjectStore(directory, self.drawing, "P001")
            old = store.save(self.project, 0)["project"]
            old["operations"][0]["source_segment_ids"] = []
            saved = store.save(old, 1)["project"]
            self.assertFalse(saved["operations"][0]["region"]["confirmed"])

    def test_no_geometry_reason_and_unhandled_not_silently_ignored(self):
        self.project["operations"][0].update(geometry_mode="none", source_segment_ids=[], no_geometry_reason="")
        report = validate_project(self.project, self.drawing)
        self.assertEqual(report["statistics"]["unhandled"], 1)
        self.assertIn("NO_GEOMETRY_REASON", {e["code"] for e in report["errors"]})

    def test_bridge_absorption_recomputed_and_protects_bound(self):
        line = {"id": "internal", "segment_type": "LINE", "start_x": 1, "start_y": 1, "end_x": 2, "end_y": 1}
        cross = {"id": "cross", "segment_type": "LINE", "start_x": 1, "start_y": 1, "end_x": 8, "end_y": 1}
        self.drawing["segments"] += [line, cross]
        bridge = operation("bridge", "B001", [])
        bridge.update(operation_type="BRIDGE", absorb_interior=True)
        self.project["operations"].append(bridge)
        geometries = {"bridge": [{"start_x": -2, "start_y": -2, "end_x": 3, "end_y": 3}]}
        statuses, stats, _ = coverage(self.project, self.drawing, geometries)
        self.assertEqual(statuses["c"], "bound")
        self.assertEqual(statuses["internal"], "absorbed")
        self.assertEqual(statuses["cross"], "unhandled")
        self.project["operations"].pop()
        self.assertEqual(coverage(self.project, self.drawing, {})[0]["internal"], "unhandled")

    def test_expected_count_not_force_generated(self):
        self.project["expected_operation_count"] = 200
        report = validate_project(self.project, self.drawing)
        self.assertIn("EXPECTED_COUNT", {e["code"] for e in report["errors"]})
        self.assertEqual(len(self.project["operations"]), 1)


if __name__ == "__main__":
    unittest.main()
