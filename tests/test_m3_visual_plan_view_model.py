import unittest
from copy import deepcopy

from src.visualization.config import validate_visualization_config
from src.visualization.plan_view_model import build_plan_view_model, current_station_info, operation_focus, operation_status
from visual_fixtures import actual, mini


class PlanViewTests(unittest.TestCase):
    def setUp(self):
        self.c,self.a,self.b,self.cfg,self.cat=mini()
        self.view=build_plan_view_model(self.c,self.a,self.cat,"PLAN_A01")

    def test_station_order_uses_order_not_ids(self):
        self.assertEqual(self.view["station_ids"],["OP90","OP10","OP80","OP05"])
        self.assertEqual(self.view["operations"]["B1"]["station_rank"],3)

    def test_current_only_exact_station(self):
        current={op for op,row in self.view["operations"].items() if operation_status(row,1)=="current"}
        self.assertEqual(current,{"C","F"});self.assertEqual(operation_status(self.view["operations"]["P"],1),"inactive")

    def test_cumulative_only_completed_ranks(self):
        completed={op for op,row in self.view["operations"].items() if operation_status(row,1,"CUMULATIVE") in ("current","previous")}
        self.assertEqual(completed,{op for op,row in self.view["operations"].items() if row["station_rank"]<=1})
        self.assertEqual(operation_status(self.view["operations"]["B1"],1,"CUMULATIVE"),"inactive")

    def test_full_plan_by_station(self):
        self.assertTrue(all(operation_status(row,0,"FULL_PLAN_BY_STATION")=="full" for row in self.view["operations"].values()))

    def test_station_load_and_type_counts(self):
        info=current_station_info(self.view,"OP10",self.cat)
        self.assertEqual(info["operation_count"],2);self.assertEqual(info["operation_type_counts"],{"CIRCLE_PUNCH":1,"FLANGING":1})
        self.assertEqual(sum(r["load"] for r in self.view["station_loads"]),12)

    def test_bend_complete_metadata_and_level(self):
        bend=self.view["operations"]["B2"]["bend"]
        self.assertEqual(bend["region_level"],1)
        self.assertTrue({"bend_operation_id","assigned_station","affected_depth","bend_length","affected_area_proxy","influence_region_id","parent_region_id","region_level"}<=set(bend))

    def test_region_filter_members_and_bend_helpers(self):
        self.assertEqual(operation_focus("C",self.cat,region_filter="R_SMALL"),"primary")
        self.assertEqual(operation_focus("B2",self.cat,region_filter="R_SMALL"),"primary")
        self.assertEqual(operation_focus("B1",self.cat,region_filter="R_SMALL"),"related")
        self.assertEqual(operation_focus("X",self.cat,region_filter="R_SMALL"),"filtered")

    def test_difference_only_marks_changed_not_helpers(self):
        self.assertEqual(operation_focus("C",self.cat,changed_ids={"B2"},difference_only=True),"unchanged")
        self.assertEqual(operation_focus("B2",self.cat,changed_ids={"B2"},difference_only=True),"primary")

    def test_no_geometry_remains_selectable_current_list(self):
        info=current_station_info(self.view,"OP90",self.cat,region_filter="NO_GEOMETRY")
        self.assertEqual({row["operation_id"] for row in info["operations"]},{"T","M"})
        self.assertFalse(self.view["operations"]["T"]["renderable"])

    def test_invalid_visual_parameters_rejected_without_tuning(self):
        config=deepcopy(self.cfg);config["interaction"]["sync_zoom_pan"]=False
        with self.assertRaises(ValueError): validate_visualization_config(config)
        config=deepcopy(self.cfg);config["svg"]["previous_stage_opacity"]=1.2
        with self.assertRaises(ValueError): validate_visualization_config(config)

    def test_all_thirteen_p001_views_cover_all_operations(self):
        c,_,_,views,*_=actual();self.assertEqual(len(views),13)
        self.assertTrue(all(set(view["operations"])==set(c.operations) for view in views.values()))
