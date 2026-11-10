"""HTTP integration tests on ephemeral ports and module-confined temp projects."""
import base64
from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
for path in (ROOT, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
from extract_app.model import ProjectStore
from extract_app.server import make_handler
from extract_app.workflow import annotation_project


class ApiTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / "outputs" / "test_tmp"
        runtime.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="api_tests_", dir=runtime))
        (self.root / "web").mkdir()
        (self.root / "web" / "index.html").write_text("<!doctype html><p>test</p>", encoding="utf-8")
        self.drawing = {
            "source": {"sha256": "c" * 64},
            "config": {"geometry_tolerance": 1e-6, "parallel_tolerance": 1e-6,
                       "max_request_bytes": 1024 * 1024,
                       "solver": {"max_time_seconds": 5, "random_seed": 12, "num_search_workers": 1}},
            "segments": [{"id": "circle_1", "segment_type": "CIRCLE",
                          "center_x": 1.123456789, "center_y": 2., "radius": 0.5,
                          "bbox": [0.623456789, 1.5, 1.623456789, 2.5], "source_group": "source_1"}],
            "issues": [],
        }
        self.store = ProjectStore(self.root, self.drawing, "P_TEST")
        handler = make_handler(self.root, self.drawing, self.store)
        handler.log_message = lambda *args: None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        assert self.root.resolve().is_relative_to((ROOT / "outputs" / "test_tmp").resolve())
        shutil.rmtree(self.root)

    def request(self, method, route, data=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=10)
        body = json.dumps(data, ensure_ascii=False).encode("utf-8") if data is not None else None
        try:
            connection.request(method, route, body, {"Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            content = response.read()
            value = json.loads(content) if "application/json" in response.getheader("Content-Type", "") else content
            return response.status, value
        finally:
            connection.close()

    def operation(self):
        return {"uid": "uid_1", "operation_id": "O001", "name": "测试圆孔",
                "operation_type": "CIRCLE_PUNCH", "surface_side": "NOT_APPLICABLE",
                "cad_parent_operation_id": None, "required_predecessor_ids": [],
                "process_group_id": None, "source_segment_ids": ["circle_1"],
                "geometry_mode": "source", "geometry_options": {}, "region": None}

    def project(self, ready=False):
        project = self.store.load()
        project["operations"] = [self.operation()]
        if ready:
            project.update(inventory_confirmed=True, sheet4_confirmed=True,
                           station_rows=[{"part_id": "P_TEST", "station_id": "OP10", "order": 10,
                                          "allowed_operation_types": "CIRCLE_PUNCH"}])
        return project

    def test_bootstrap_and_static_entry(self):
        status, data = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(data["drawing"]["source"]["sha256"], "c" * 64)
        self.assertEqual(data["project"]["revision"], 0)
        self.assertIn("MARKING", data["operation_types"])
        self.assertEqual(data['field_defaults']['bend_angle_deg'], 90)
        status, content = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"<!doctype html>", content)

    def test_save_conflict_preserves_latest_canonical_and_revisions(self):
        project = self.project()
        status, saved = self.request("POST", "/api/save", {"project": project, "expected_revision": 0})
        self.assertEqual(status, 200)
        self.assertEqual(saved["project"]["revision"], 1)
        project["operations"][0]["name"] = "过期覆盖"
        status, rejected = self.request("POST", "/api/save", {"project": project, "expected_revision": 0})
        self.assertEqual(status, 400)
        self.assertIn("版本冲突", rejected["error"])
        status, loaded = self.request("GET", "/api/project")
        self.assertEqual(loaded["project"]["operations"][0]["name"], "测试圆孔")
        self.assertEqual(len(list((self.store.directory / "revisions").glob("*.json"))), 1)

    def test_source_mismatch_is_rejected_without_saving(self):
        project = self.project()
        project["source_sha256"] = "d" * 64
        status, value = self.request("POST", "/api/save", {"project": project, "expected_revision": 0})
        self.assertEqual(status, 400)
        self.assertIn("指纹", value["error"])
        self.assertFalse(self.store.path.exists())

    def test_changed_supplement_manifest_blocks_stale_save(self):
        import hashlib
        path = self.root / 'supplement.json'
        path.write_text('original', encoding='utf-8')
        self.drawing['source_dependencies'] = [{'file_name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
        path.write_text('changed', encoding='utf-8')
        status, value = self.request('POST', '/api/save', {'project': self.project(), 'expected_revision': 0})
        self.assertEqual(status, 400)
        self.assertIn('配准清单已变化', value['error'])
        self.assertFalse(self.store.path.exists())

    def test_preview_has_precise_geometry_without_mutating_saved_project(self):
        project = self.project()
        status, value = self.request("POST", "/api/preview", {"project": project, "operation": self.operation()})
        self.assertEqual(status, 200)
        self.assertEqual(value["issues"], [])
        self.assertEqual(value["geometry"][0]["center_x"], 1.123456789)
        self.assertEqual(value["geometry"][0]["radius"], 0.5)
        self.assertEqual(value["statuses"]["circle_1"], "bound")
        self.assertFalse(self.store.path.exists())

    def test_unfinished_formal_export_saves_downloadable_diagnostic_draft(self):
        status, value = self.request("POST", "/api/export", {"project": self.project(), "mode": "validated"})
        self.assertEqual(status, 200)
        self.assertFalse(value["ready"])
        self.assertEqual(value["mode"], "draft")
        self.assertEqual(value["report"]["data_contract"]["status"], "NOT_CHECKED")
        self.assertEqual(len(value["files"]), 4)
        self.assertIn('operation_metadata.json', [item['name'] for item in value['files']])
        for record in value["files"]:
            self.assertTrue(record["url"].startswith("/outputs/"))
            status, content = self.request("GET", record["url"])
            self.assertEqual(status, 200)
            self.assertTrue(content)
        status, content = self.request("GET", value["files"][0]["url"])
        book = openpyxl.load_workbook(BytesIO(content), read_only=True)
        try:
            self.assertEqual(book.sheetnames, ["Sheet1", "Sheet2", "Sheet3", "Sheet4", "Sheet5"])
            self.assertNotIn("expert_station", next(book["Sheet1"].values))
        finally:
            book.close()

    def test_completed_facts_formal_export_passes_existing_loader_and_hard_model(self):
        status, value = self.request("POST", "/api/export", {"project": self.project(ready=True), "mode": "validated"})
        self.assertEqual(status, 200)
        self.assertTrue(value["ready"], value["report"])
        self.assertEqual(value["report"]["data_contract"]["status"], "PASSED")
        self.assertTrue(value["report"]["hard_feasibility"]["feasible"])

    def test_static_traversal_and_foreign_origin_are_rejected(self):
        for route in ("/outputs/../../DEVELOPMENT_PLAN.md", "/%2e%2e/projects/P_TEST/project.json",
                      "/outputs/%2e%2e/%2e%2e/Plotview%201.dxf"):
            status, value = self.request("GET", route)
            self.assertEqual(status, 400, route)
            self.assertIn("路径", value["error"])
        status, value = self.request("POST", "/api/save", {"project": self.project(), "expected_revision": 0},
                                     headers={"Origin": "https://foreign.example"})
        self.assertEqual(status, 400)
        self.assertFalse(self.store.path.exists())

    def test_import_station_returns_exact_sheet3_4_and_sha(self):
        book = openpyxl.Workbook()
        book.active.title = "Sheet3"
        book.active.append(["part_id", "station_id", "order", "allowed_operation_types"])
        book.active.append(["P_TEST", "OP10", 10, "CIRCLE_PUNCH"])
        book.create_sheet("Sheet4").append(["operation_id", "fixed_station"])
        stream = BytesIO()
        book.save(stream)
        book.close()
        status, value = self.request("POST", "/api/import_workbook",
                                     {"filename": "manual.xlsx", "base64": base64.b64encode(stream.getvalue()).decode("ascii")})
        self.assertEqual(status, 200)
        self.assertEqual(value["station_rows"][0]["allowed_operation_types"], "CIRCLE_PUNCH")
        self.assertEqual(value["fixed_rows"], [])
        self.assertTrue(value["sheet4_confirmed"])
        self.assertEqual(len(value["station_import_source"]["sha256"]), 64)
        self.assertFalse(self.store.path.exists())

    def test_import_operation_csv_api_preserves_explicit_parent(self):
        content = "operation_id,name,cad_parent_operation_id\nO002,child,O001\nO001,parent,\n".encode("utf-8")
        status, value = self.request("POST", "/api/import_operations",
                                     {"filename": "tree.csv", "base64": base64.b64encode(content).decode("ascii")})
        self.assertEqual(status, 200)
        self.assertEqual(value["operations"][0]["cad_parent_operation_id"], "O001")
        self.assertEqual(value["operations"][1]["required_predecessor_ids"], [])
        self.assertEqual(value["operations"][0]["source_segment_ids"], [])

    def test_bootstrap_exposes_refined_types_without_overwriting_project(self):
        status, value = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.assertEqual(value["project"]["workflow_version"], 2)
        keys = {t["key"] for t in value["feature_types"]}
        self.assertTrue({"DRAW_FORM", "BEND_CURVED", "PRE_PUNCH_NONCIRCULAR", "FLANGING_NONCIRCULAR"} <= keys)
        self.assertFalse(self.store.path.exists())

    def test_staged_preview_and_save_do_not_compile_geometry(self):
        p = annotation_project(self.project())
        status, value = self.request("POST", "/api/preview", {"project": p, "operation": p["operations"][0]})
        self.assertEqual(status, 200)
        self.assertEqual(value["geometry"], [])
        status, value = self.request("POST", "/api/save", {"project": p, "expected_revision": 0})
        self.assertEqual(status, 200)
        self.assertNotIn("geometry_batch", value["project"])
        self.assertEqual(value["report"]["geometries"], {})

    def test_compile_endpoint_is_explicit_and_saved_batch_becomes_stale_on_edit(self):
        p = annotation_project(self.project(ready=True))
        status, result = self.request("POST", "/api/compile_geometry", {"project": p})
        self.assertEqual(status, 200)
        self.assertTrue(result["report"]["geometry_complete"])
        self.assertFalse(self.store.path.exists())
        saved = result["project"]
        status, value = self.request("POST", "/api/save", {"project": saved, "expected_revision": 0})
        self.assertEqual(status, 200)
        self.assertEqual(value["report"]["geometry_state"], "CURRENT")
        changed = value["project"]
        changed["operations"][0]["name"] = "edited"
        status, value = self.request("POST", "/api/validate", {"project": changed})
        self.assertEqual(value["report"]["geometry_state"], "NOT_COMPILED_OR_STALE")
        self.assertEqual(value["geometries"], {})

    def test_staged_export_requires_compilation_then_produces_review_asset(self):
        p = annotation_project(self.project(ready=True))
        status, value = self.request("POST", "/api/export", {"project": p, "mode": "draft"})
        self.assertEqual(status, 400)
        self.assertIn("整理几何", value["error"])
        _, compiled = self.request("POST", "/api/compile_geometry", {"project": p})
        status, value = self.request("POST", "/api/export", {"project": compiled["project"], "mode": "validated"})
        self.assertEqual(status, 200)
        self.assertTrue(value["ready"], value["report"])
        names = {f["name"] for f in value["files"]}
        self.assertTrue({"geometry_review.json", "annotation_project.json"} <= names)

    def test_multi_contours_survive_review_export_without_formal_claim(self):
        self.drawing["segments"].append({**self.drawing["segments"][0], "id": "outer_circle", "radius": 1.})
        p = annotation_project(self.project(ready=True))
        p["operations"][0].update(feature_type="DRAW_FORM", operation_type="DRAW_FORM", geometry_shape="MULTI_CONTOUR",
                                     source_segment_ids=["circle_1", "outer_circle"])
        _, compiled = self.request("POST", "/api/compile_geometry", {"project": p})
        status, value = self.request("POST", "/api/export", {"project": compiled["project"], "mode": "validated"})
        self.assertEqual(status, 200)
        self.assertFalse(value["ready"])
        self.assertEqual(value["report"]["hard_feasibility"]["status"], "NOT_CHECKED")
        review_url = next(f["url"] for f in value["files"] if f["name"] == "geometry_review.json")
        status, review = self.request("GET", review_url)
        if isinstance(review, bytes):
            review = json.loads(review)
        self.assertEqual(status, 200)
        self.assertEqual(len(review["records"]["uid_1"]["paths"]), 2)

    def paired_project(self):
        self.drawing["segments"].append({**self.drawing["segments"][0], "id": "pre_circle", "radius": .3})
        p = annotation_project(self.project(ready=True))
        flange = {**self.operation(), "uid": "flange", "operation_id": "O008", "operation_type": "FLANGING",
                  "feature_type": "FLANGING_CIRCULAR", "geometry_shape": "CIRCULAR"}
        pre = {**self.operation(), "uid": "pre", "operation_id": "O009", "operation_type": "PRE_PUNCH",
               "feature_type": "PRE_PUNCH_CIRCULAR", "geometry_shape": "CIRCULAR",
               "source_segment_ids": ["pre_circle"], "cad_parent_operation_id": "O008"}
        p["operations"] = [flange, pre]
        p["station_rows"] = [{"part_id": "P_TEST", "station_id": "OP10", "order": 10, "allowed_operation_types": "PRE_PUNCH"},
                             {"part_id": "P_TEST", "station_id": "OP20", "order": 20, "allowed_operation_types": "FLANGING"}]
        return p

    def test_pairing_preview_is_metadata_only_and_does_not_save(self):
        p = self.paired_project()
        status, result = self.request("POST", "/api/pairing", {"project": p})
        self.assertEqual(status, 200)
        self.assertEqual({a["process_group_id"] for a in result["assignments"]}, {"PG_O008"})
        self.assertEqual(result["statuses"]["pre"]["partner_operation_ids"], ["O008"])
        self.assertTrue(all(op["process_group_id"] is None for op in p["operations"]))
        self.assertFalse(self.store.path.exists())

    def test_save_automatically_persists_pair_group_but_preserves_cad_and_predecessors(self):
        p = self.paired_project()
        for operation in p["operations"]:
            operation["process_group_id"] = "PG001"
        status, saved = self.request("POST", "/api/save", {"project": p, "expected_revision": 0})
        self.assertEqual(status, 200)
        self.assertTrue(all(op["process_group_id"] == "PG_O008" for op in saved["project"]["operations"]))
        self.assertEqual(saved["project"]["operations"][1]["cad_parent_operation_id"], "O008")
        self.assertTrue(all(op["required_predecessor_ids"] == [] for op in saved["project"]["operations"]))
        self.assertNotIn("PAIR_UNCONFIRMED", {e["code"] for e in saved["report"]["errors"]})

    def test_auto_group_survives_compile_and_formal_export(self):
        p = self.paired_project()
        status, compiled = self.request("POST", "/api/compile_geometry", {"project": p})
        self.assertEqual(status, 200)
        self.assertTrue(compiled["report"]["ready"], compiled["report"])
        status, exported = self.request("POST", "/api/export", {"project": compiled["project"], "mode": "validated"})
        self.assertEqual(status, 200)
        self.assertTrue(exported["ready"], exported["report"])
        self.assertTrue(exported["report"]["hard_feasibility"]["feasible"])

    def test_read_migrates_old_group_in_memory_then_user_save_persists_it(self):
        p = self.paired_project()
        p["workflow_version"] = 1
        for operation in p["operations"]:
            operation["process_group_id"] = "PG001"
        self.store.save(p, 0)
        status, loaded = self.request("GET", "/api/project")
        self.assertEqual(status, 200)
        self.assertTrue(all(o["process_group_id"] == "PG_O008" for o in loaded["project"]["operations"]))
        self.assertEqual(self.store.load()["revision"], 1)
        self.assertTrue(all(o["process_group_id"] == "PG001" for o in self.store.load()["operations"]))
        status, saved = self.request("POST", "/api/save", {"project": loaded["project"], "expected_revision": 1})
        self.assertEqual(status, 200)
        self.assertEqual(saved["project"]["revision"], 2)
        self.assertTrue(all(o["process_group_id"] == "PG_O008" for o in self.store.load()["operations"]))


if __name__ == "__main__":
    unittest.main()
