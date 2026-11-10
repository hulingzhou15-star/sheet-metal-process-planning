"""Annotation-first workflow, multi-path provenance and stale-export regression."""
from copy import deepcopy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from extract_app.model import new_project, validate_project
from extract_app.workflow import (FEATURE_BY_KEY, annotation_project, compile_project,
                                  current_batch, compile_operation)

CONFIG = {"geometry_tolerance": 1e-6, "parallel_tolerance": 1e-6}


def line(sid, a, b):
    return {"id": sid, "segment_type": "LINE", "start_x": a[0], "start_y": a[1], "end_x": b[0], "end_y": b[1]}


def rectangle(prefix, low, high):
    x, y = low
    X, Y = high
    points = [(x,y),(X,y),(X,Y),(x,Y)]
    return [line(f"{prefix}{i}", p, points[(i+1)%4]) for i,p in enumerate(points)]


def circle(sid="c", x=0, y=0, radius=1):
    return {"id": sid, "segment_type": "CIRCLE", "center_x": x, "center_y": y, "radius": radius}


def op(key, segments, uid="u"):
    feature = FEATURE_BY_KEY[key]
    return {"uid": uid, "operation_id": uid.upper(), "name": feature["label"],
            "feature_type": key, "operation_type": feature["operation_type"],
            "geometry_shape": feature["shape"], "surface_side": "NOT_APPLICABLE",
            "source_segment_ids": [s["id"] for s in segments], "required_predecessor_ids": [],
            "geometry_options": {}, "process_group_id": None}


class AnnotationWorkflowTests(unittest.TestCase):
    def setup_project(self, key="CIRCLE_PUNCH", segments=None):
        segments = segments or [circle()]
        drawing = {"source": {"sha256": "test"}, "segments": segments, "issues": [], "config": CONFIG}
        project = annotation_project(new_project(drawing, "P_TEST"))
        project["operations"] = [op(key, segments)]
        return project, drawing

    def compile(self, project, drawing):
        project["inventory_confirmed"] = True
        project["geometry_batch"] = compile_project(project, drawing, validate_project(project, drawing, annotation_only=True))
        return validate_project(project, drawing)

    def test_annotation_check_never_derives_geometry(self):
        p, d = self.setup_project()
        with patch("extract_app.model.derive_geometry", side_effect=AssertionError("must not run")):
            r = validate_project(p,d)
        self.assertEqual(r["geometries"], {})
        self.assertFalse(r["geometry_complete"])
        self.assertNotIn("GEOMETRY_OR_REGION", {e["code"] for e in r["errors"]})

    def test_all_types_allow_multiple_bindings_before_compile(self):
        selected = rectangle("a", (0,0), (2,2))
        for key in FEATURE_BY_KEY:
            with self.subTest(key=key):
                p,d = self.setup_project(key, selected)
                codes = {e["code"] for e in validate_project(p,d)["errors"]}
                self.assertFalse(codes & {"SOURCE_REFERENCE", "GEOMETRY_OR_REGION", "FEATURE_TYPE"})

    def test_compile_requires_whole_drawing_confirmation(self):
        p,d=self.setup_project()
        with self.assertRaisesRegex(ValueError,"整图"):
            compile_project(p,d,validate_project(p,d,annotation_only=True))

    def test_type_driven_circle_compilation(self):
        p,d=self.setup_project()
        r=self.compile(p,d)
        self.assertEqual(r["geometries"]["u"][0]["segment_type"],"CIRCLE")
        self.assertTrue(r["geometry_complete"])

    def test_edited_binding_invalidates_batch(self):
        p,d=self.setup_project()
        self.compile(p,d)
        p["operations"][0]["source_segment_ids"]=[]
        self.assertIsNone(current_batch(p,CONFIG))
        self.assertEqual(validate_project(p,d)["geometries"],{})

    def test_type_notes_and_region_changes_invalidate(self):
        p,d=self.setup_project()
        self.compile(p,d)
        for key,value in (("feature_type","IRREGULAR_PUNCH"),("notes","changed"),("region",{"confirmed":True})):
            changed=deepcopy(p)
            changed["operations"][0][key]=value
            self.assertIsNone(current_batch(changed,CONFIG))

    def test_station_import_does_not_invalidate_geometry(self):
        p,d=self.setup_project()
        self.compile(p,d)
        p["station_rows"]=[{"station_id":"OP10"}]
        self.assertIsNotNone(current_batch(p,CONFIG))

    def test_draw_form_two_contours_kept_separate_no_legacy_flattening(self):
        selected=rectangle("outer",(0,0),(10,10))+rectangle("inner",(2,2),(8,8))
        p,d=self.setup_project("DRAW_FORM",selected)
        r=self.compile(p,d)
        record=r["geometry_records"]["u"]
        self.assertEqual(record["status"],"READY")
        self.assertEqual(len(record["paths"]),2)
        self.assertEqual({s for path in record["paths"] for s in path["source_segment_ids"]},set(p["operations"][0]["source_segment_ids"]))
        self.assertEqual(r["geometries"],{})
        self.assertFalse(r["legacy_compatible"])
        self.assertFalse(r["ready"])

    def test_draw_form_does_not_absorb_independent_hole(self):
        sources=rectangle("o",(0,0),(10,10))
        p,d=self.setup_project("DRAW_FORM",sources)
        d["segments"].append(circle("independent",5,5))
        r=self.compile(p,d)
        self.assertEqual(r["statuses"]["independent"],"unhandled")

    def test_curved_bend_preserves_raw_arcs_no_straightening(self):
        arc={"id":"arc","segment_type":"ARC","start_x":1,"start_y":0,"end_x":0,"end_y":1,"center_x":0,"center_y":0,"radius":1,"sweep_angle":90}
        p,d=self.setup_project("BEND_CURVED",[arc])
        r=self.compile(p,d)
        rec=r["geometry_records"]["u"]
        self.assertEqual(rec["status"],"RULE_PENDING")
        self.assertEqual(rec["paths"][0]["segments"][0]["sweep_angle"],90)
        self.assertEqual(r["regions"],[])
        self.assertFalse(r["geometry_complete"])

    def test_circle_can_be_two_arcs(self):
        a={"id":"a","segment_type":"ARC","start_x":1,"start_y":0,"end_x":-1,"end_y":0,"center_x":0,"center_y":0,"radius":1,"sweep_angle":180}
        b={**a,"id":"b","start_x":-1,"end_x":1}
        p,d=self.setup_project("CIRCLE_PUNCH",[a,b])
        r=self.compile(p,d)
        self.assertEqual(r["geometries"]["u"][0]["radius"],1)
        self.assertEqual(len(r["geometry_records"]["u"]["paths"][0]["source_segment_ids"]),2)

    def test_wrong_circle_binding_is_review_issue_only_after_compile(self):
        p,d=self.setup_project(segments=[circle("a"),circle("b",x=5)])
        self.assertEqual(validate_project(p,d)["errors"],[])
        self.assertEqual(self.compile(p,d)["geometry_records"]["u"]["status"],"NEEDS_REVIEW")

    def test_shared_binding_requires_explicit_acknowledgement(self):
        p,d=self.setup_project()
        p["operations"].append(op("CIRCLE_PUNCH",d["segments"],"v"))
        self.assertIn("SHARED_SOURCE_UNCONFIRMED",{e["code"] for e in validate_project(p,d)["errors"]})
        for operation in p["operations"]:
            operation["shared_source_segment_ids"]=["c"]
        self.assertNotIn("SHARED_SOURCE_UNCONFIRMED",{e["code"] for e in validate_project(p,d)["errors"]})

    def test_duplicate_ids_not_counted_as_extra_geometry(self):
        p,d=self.setup_project()
        p["operations"][0]["source_segment_ids"]=["c","c"]
        r=validate_project(p,d)
        self.assertIn("SOURCE_REFERENCE",{e["code"] for e in r["errors"]})
        self.assertEqual(r["statistics"]["bound"],1)

    def test_general_bend_multiple_disjoint_segments(self):
        selected=[line("a",(0,0),(4,0)),line("b",(6,0),(10,0)),line("c",(0,2),(4,2)),line("d",(6,2),(10,2))]
        operation=op("BEND_STRAIGHT",selected)
        operation["geometry_options"]={"confirmed_span":True}
        operation["region"]={"influence_region_id":"R","parent_region_id":None,"confirmed":True,"parent_confirmed":True,"affected_side":"POSITIVE_Y","far_boundary_coord":5}
        record=compile_operation(operation,selected,CONFIG)
        self.assertEqual(record["status"],"READY")
        self.assertEqual(record["legacy_geometry"][0]["start_y"],1)
        self.assertEqual(record["legacy_geometry"][0]["end_x"],10)

    def test_straight_bend_needs_domain_confirmation(self):
        selected=[line("a",(0,0),(10,0)),line("b",(0,2),(10,2))]
        record=compile_operation(op("BEND_STRAIGHT",selected),selected,CONFIG)
        self.assertEqual(record["status"],"NEEDS_CONFIRMATION")
        self.assertEqual(record["legacy_geometry"],[])

    def test_bridge_absorbs_auxiliary_line_but_not_circle(self):
        selected=rectangle("edge",(0,0),(10,10))
        p,d=self.setup_project("BRIDGE",selected)
        p["operations"][0]["geometry_options"]={"confirmed_footprint":True}
        d["segments"] += [line("inside",(2,2),(8,2)),circle("hole",5,5)]
        r=self.compile(p,d)
        self.assertEqual(r["statuses"]["inside"],"absorbed")
        self.assertEqual(r["statuses"]["hole"],"unhandled")

    def test_old_project_migrates_without_changing_sources(self):
        p,d=self.setup_project()
        p.pop("workflow_version")
        p["operations"][0].pop("feature_type")
        old=json.dumps(p,sort_keys=True)
        upgraded=annotation_project(p)
        self.assertEqual(json.dumps(p,sort_keys=True),old)
        self.assertEqual(upgraded["operations"][0]["source_segment_ids"],["c"])
        self.assertEqual(upgraded["workflow_version"],2)

    def test_unbound_legacy_prehole_is_not_assumed_circular(self):
        p,d=self.setup_project("PRE_PUNCH_CIRCULAR")
        p["operations"][0].update(feature_type="",geometry_mode="unbound")
        upgraded=annotation_project(p)
        self.assertEqual(upgraded["operations"][0]["feature_type"],"")

    def test_unknown_shape_cannot_pass_as_known(self):
        p,d=self.setup_project()
        p["operations"][0]["geometry_shape"]="NONCIRCULAR"
        self.assertIn("FEATURE_TYPE",{e["code"] for e in validate_project(p,d)["errors"]})

    def test_real_p002_draw_form_inner_outer_source_groups(self):
        from extract_app.drawing import load_drawing
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "configs/P002.json").read_text(encoding="utf-8"))
        drawing = load_drawing(root / config['source_file'], config)
        selected = [s for s in drawing["segments"] if s["source_group"] in {"B8", "BF"}]
        self.assertEqual(len(selected), 8)
        record = compile_operation(op("DRAW_FORM", selected), selected, config)
        self.assertEqual(record["status"], "READY")
        self.assertEqual(len(record["paths"]), 2)
        self.assertEqual([len(p["segments"]) for p in record["paths"]], [4, 4])

    def test_no_geometry_cannot_bypass_new_type_adapter(self):
        operation = op("DRAW_FORM", [])
        operation.update(no_geometry=True, no_geometry_reason="已有来源确认无法提供二维轮廓")
        record = compile_operation(operation, [], CONFIG)
        self.assertFalse(record["legacy_supported"])

