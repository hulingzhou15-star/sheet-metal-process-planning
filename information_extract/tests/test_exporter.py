"""Excel contract, precision, provenance, and formal status regression tests."""
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
import sys
import tempfile

import openpyxl
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from extract_app import exporter


def project():
    return {
        "part_id": "P_TEST", "source_sha256": "a" * 64,
        "operations": [{"uid": "stable_1", "operation_id": "O001", "name": "定位",
                        "operation_type": "MARKING", "surface_side": "NOT_APPLICABLE",
                        "cad_parent_operation_id": None, "required_predecessor_ids": [],
                        "process_group_id": None, "source_segment_ids": ["source:line:1"],
                        "geometry_mode": "none", "no_geometry_reason": "无适用几何"}],
        "station_rows": [{"part_id": "P_TEST", "station_id": "OP10", "order": 10,
                          "allowed_operation_types": "MARKING"}],
        "fixed_rows": [], "sheet4_confirmed": True,
    }


def report():
    return {"errors": [], "warnings": [], "ready": True, "geometries": {},
            "regions": [], "statistics": {"operations": 1}}


def export(root, p=None, r=None, **kwargs):
    return exporter.export_workbook(p or project(), r or report(), root / "outputs",
                                    module_root=root, **kwargs)


def workbook_path(result):
    return Path(result["directory"]) / result["files"][0]["name"]


def table(book, sheet):
    rows = list(book[sheet].values)
    return [dict(zip(rows[0], row)) for row in rows[1:]]


def book_bytes(book):
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()



class ExporterTests(unittest.TestCase):
    def setUp(self):
        self.runtime = ROOT / "outputs" / "test_tmp"
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.export_root = Path(tempfile.mkdtemp(prefix="exporter_", dir=self.runtime))

    def tearDown(self):
        assert self.export_root.resolve().is_relative_to(self.runtime.resolve())
        shutil.rmtree(self.export_root)

    def patch(self, target, name, value):
        replacement = patch.object(target, name, value)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_draft_has_all_contract_headers_no_expert_or_editor_columns(self):
        result = export(self.export_root)
        book = openpyxl.load_workbook(workbook_path(result))
        try:
            assert book.sheetnames == list(exporter.HEADERS)
            for sheet, headers in exporter.HEADERS.items():
                assert tuple(next(book[sheet].values)) == headers
            row = table(book, "Sheet1")[0]
            assert row["source_feature_ref"] == "定位"
            assert row["geometry_id"] is None
            assert "expert_station" not in row
            assert "uid" not in row
            assert "source_segment_ids" not in row
        finally:
            book.close()
        assert result["ready"] is False
        assert result["report"]["data_contract"]["status"] == "NOT_CHECKED"


    def test_complete_precision_blank_fields_and_signed_arc_are_preserved(self):
        p, r = project(), report()
        p["operations"][0]["operation_type"] = "DIMPLE"
        x = 1.123456789012345
        r["geometries"] = {"stable_1": [{"seq": 1, "segment_type": "ARC",
                                          "start_x": x, "start_y": 0.0,
                                          "end_x": 0.0, "end_y": -x,
                                          "center_x": 0.0, "center_y": 0.0,
                                          "radius": x, "sweep_angle": -90.0}]}
        result = export(self.export_root, p, r)
        book = openpyxl.load_workbook(workbook_path(result))
        try:
            row = table(book, "Sheet2")[0]
            assert abs(row["start_x"] - x) <= 1e-15
            assert row["start_x"] != round(x, 6)
            assert row["sweep_angle"] == -90
            assert book["Sheet2"]["E2"].number_format == "0.000000"
            assert table(book, "Sheet1")[0]["cad_parent_operation_id"] is None
            assert table(book, "Sheet1")[0]["geometry_id"] == "G_stable_1"
        finally:
            book.close()


    def test_geometry_identity_survives_operation_rename_and_nested_coords(self):
        p, r = project(), report()
        r["geometries"] = {"stable_1": [{"segment_type": "CIRCLE", "coords": {
            "center_x": 1.123456789, "center_y": 2.0, "radius": 0.5}}]}
        first = export(self.export_root, p, r)
        p["operations"][0]["operation_id"] = "NEW_NAME"
        second = export(self.export_root, p, r)
        assert first["directory"] != second["directory"]
        for result in (first, second):
            book = openpyxl.load_workbook(workbook_path(result))
            try:
                assert table(book, "Sheet1")[0]["geometry_id"] == "G_stable_1"
                assert table(book, "Sheet2")[0]["center_x"] == 1.123456789
                assert table(book, "Sheet2")[0]["seq"] == 1
            finally:
                book.close()


    def test_station_and_fixed_values_kept_and_manifest_is_auditable(self):
        p, r = project(), report()
        p["station_rows"] = [{"part_id": "P_TEST", "station_id": "CUSTOM_20", "order": 30,
                              "allowed_operation_types": ["MARKING", "TRIM"]}]
        p["fixed_rows"] = [{"operation_id": "O008", "fixed_station": "CUSTOM_20"}]
        result = export(self.export_root, p, r)
        book = openpyxl.load_workbook(workbook_path(result))
        try:
            assert table(book, "Sheet3")[0] == {**p["station_rows"][0], "allowed_operation_types": "MARKING,TRIM"}
            assert table(book, "Sheet4") == p["fixed_rows"]
        finally:
            book.close()
        manifest = json.loads((Path(result["directory"]) / "source_manifest.json").read_text(encoding="utf-8"))
        assert manifest["source_sha256"] == p["source_sha256"]
        assert manifest["operations"][0]["source_segment_ids"] == ["source:line:1"]
        assert manifest["expert_reference_required"] is False
        assert all(item["url"].startswith("/outputs/") for item in result["files"])


    def test_formal_success_calls_real_facts_loader_and_h1_h7_once(self):
        p, r = project(), report()
        p["operations"].append({**deepcopy(p["operations"][0]), "uid": "stable_2", "operation_id": "O002"})
        p["operations"][0]["cad_parent_operation_id"] = "O002"
        p["station_rows"].append({**p["station_rows"][0], "station_id": "OP20", "order": 20})
        result = export(self.export_root, p, r, mode="validated")
        assert result["ready"] is True
        assert result["mode"] == "formal"
        assert result["report"]["data_contract"]["status"] == "PASSED"
        hard = result["report"]["hard_feasibility"]
        assert hard["feasible"] is True
        assert hard["witness_operation_count"] == 2
        assert hard["hard_constraint_check"]["violation_breakdown"]["H7"] == 0
        assert "assignments" not in hard  # a witness check is not a candidate output
        assert "formal" in workbook_path(result).name


    def test_formal_real_infeasible_is_a_diagnostic_draft(self):
        p = project()
        p["operations"].append({**deepcopy(p["operations"][0]), "uid": "stable_2", "operation_id": "O002"})
        p["operations"][0]["cad_parent_operation_id"] = "O002"
        result = export(self.export_root, p, mode="validated")
        assert not result["ready"]
        assert result["mode"] == "draft"
        assert result["report"]["data_contract"]["status"] == "PASSED"
        assert result["report"]["hard_feasibility"]["status"] == "INFEASIBLE"
        assert result["report"]["hard_feasibility"]["feasible"] is False
        assert "HARD_INFEASIBLE" in {e["code"] for e in result["report"]["errors"]}
        assert workbook_path(result).is_file()


    def test_formal_unknown_is_not_reported_infeasible(self):
        self.patch(exporter, "_check_hard_feasibility",
                            lambda context, settings: {"status": "UNKNOWN", "feasible": None})
        result = export(self.export_root, mode="validated")
        assert not result["ready"]
        assert result["report"]["hard_feasibility"]["feasible"] is None
        codes = {e["code"] for e in result["report"]["errors"]}
        assert "HARD_FEASIBILITY_UNRESOLVED" in codes
        assert "HARD_INFEASIBLE" not in codes


    def test_formal_data_error_never_attempts_hard_solver(self):
        p = project()
        p["station_rows"][0]["allowed_operation_types"] = "NOT_A_TYPE"
        def forbidden(*args):
            self.fail("invalid facts must not reach the solver")
        self.patch(exporter, "_check_hard_feasibility", forbidden)
        result = export(self.export_root, p, mode="validated")
        assert result["report"]["data_contract"]["status"] == "FAILED"
        assert result["report"]["hard_feasibility"]["status"] == "NOT_CHECKED"
        assert result["mode"] == "draft"


    def test_unfinished_annotation_cannot_be_formal_or_mutate_input(self):
        r = report()
        r.update(ready=False, errors=[{"code": "UNBOUND", "message": "待绑定"}])
        before = deepcopy(r)
        self.patch(exporter, "_load_context", lambda *args: self.fail("not ready"))
        result = export(self.export_root, r=r, mode="validated")
        assert result["report"]["annotation_ready"] is False
        assert result["report"]["ready"] is False
        assert r == before


    def test_exports_are_confined_and_do_not_overwrite(self):
        with self.assertRaisesRegex(ValueError, "Exports must remain"):
            exporter.export_workbook(project(), report(), ROOT.parent / "outputs", module_root=self.export_root)
        first, second = export(self.export_root), export(self.export_root)
        assert workbook_path(first).is_file() and workbook_path(second).is_file()
        assert first["directory"] != second["directory"]


    def test_text_that_looks_like_formula_stays_literal(self):
        p = project()
        p["operations"][0]["name"] = '=HYPERLINK("https://invalid.example")'
        result = export(self.export_root, p)
        book = openpyxl.load_workbook(workbook_path(result), data_only=False)
        try:
            assert book["Sheet1"]["C2"].value == p["operations"][0]["name"]
            assert book["Sheet1"]["C2"].data_type == "s"
        finally:
            book.close()


    def test_import_station_tables_only_ignores_expert_and_preserves_values(self):
        book = openpyxl.Workbook()
        book.active.title = "Sheet1"
        book["Sheet1"].append(["expert_station"])
        book["Sheet1"].append(['=1/0'])
        stations = book.create_sheet("Sheet3")
        stations.append(exporter.HEADERS["Sheet3"])
        stations.append(["P_OTHER", "S20", 120, "MARKING;TRIM"])
        fixed = book.create_sheet("Sheet4")
        fixed.append(exporter.HEADERS["Sheet4"])
        fixed.append(["O009", "S20"])
        value = exporter.import_station_workbook(book_bytes(book))
        assert value["station_rows"] == [{"part_id": "P_OTHER", "station_id": "S20", "order": 120,
                                          "allowed_operation_types": "MARKING;TRIM"}]
        assert value["fixed_rows"] == [{"operation_id": "O009", "fixed_station": "S20"}]
        assert value["sheet4_confirmed"] is True
        assert "expert_station" not in json.dumps(value)


    def test_import_station_rejects_formula_and_allows_explicit_empty_sheet4(self):
        book = openpyxl.Workbook()
        book.active.title = "Sheet3"
        book.active.append(exporter.HEADERS["Sheet3"])
        book.active.append(["P", "S", "=1+1", "MARKING"])
        book.create_sheet("Sheet4").append(exporter.HEADERS["Sheet4"])
        content = book_bytes(book)
        with self.assertRaisesRegex(ValueError, "公式"):
            exporter.import_station_workbook(content)
        book = openpyxl.load_workbook(BytesIO(content))
        book["Sheet3"]["C2"] = 2
        value = exporter.import_station_workbook(book_bytes(book))
        assert value["fixed_rows"] == [] and value["sheet4_confirmed"]


    def test_operation_import_preserves_explicit_parent_never_uses_row_order(self):
        content = ("operation_id,name,operation_type,surface_side,cad_parent_operation_id,required_predecessor_ids,expert_station\n"
                   "CHILD,子动作,MARKING,NOT_APPLICABLE,PARENT,,OP999\n"
                   "PARENT,父动作,MARKING,NOT_APPLICABLE,,,OP888\n").encode("utf-8-sig")
        value = exporter.import_operation_workbook(content, filename="tree.csv")
        child, parent = value["operations"]
        assert child["cad_parent_operation_id"] == "PARENT"
        assert parent["cad_parent_operation_id"] is None
        assert child["required_predecessor_ids"] == parent["required_predecessor_ids"] == []
        assert child["geometry_mode"] == "unbound" and child["source_segment_ids"] == []
        assert "expert_station" not in json.dumps(value)


    def test_operation_xlsx_import_does_not_import_old_geometry_or_expert(self):
        book = openpyxl.Workbook()
        book.active.title = "Sheet1"
        book.active.append(["operation_id", "source_feature_ref", "cad_parent_operation_id", "geometry_id", "expert_station"])
        book.active.append(["CHILD", "动作", "PARENT", "G_OLD", "OP30"])
        value = exporter.import_operation_workbook(book_bytes(book), filename="tree.xlsx")
        op = value["operations"][0]
        assert op["name"] == "动作" and op["cad_parent_operation_id"] == "PARENT"
        assert op["source_segment_ids"] == []
        assert "geometry_id" not in op and "expert_station" not in op


    def test_sheet5_metadata_is_written_without_inventing_parent(self):
        r = report()
        r["regions"] = [{"bend_operation_id": "O050", "start_x": 0.123456789,
                          "start_y": 0, "end_x": 20.123456789, "end_y": 0,
                          "bend_axis": "X", "far_boundary_coord": 10.5,
                          "affected_depth": 10.5, "affected_side": "POSITIVE",
                          "bend_length": 20.0, "affected_area_proxy": 210.0,
                          "influence_region_id": "R50", "parent_region_id": None}]
        result = export(self.export_root, r=r)
        book = openpyxl.load_workbook(workbook_path(result))
        try:
            assert table(book, "Sheet5") == r["regions"]
        finally:
            book.close()


if __name__ == "__main__":
    unittest.main()
