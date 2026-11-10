from copy import deepcopy
import unittest

from extract_app.pairing import synchronize_pairing, AUTO_SOURCE
from extract_app.workflow import annotation_project, batch_fingerprint


def action(uid, kind, parent=None, group=None):
    return {"uid": uid, "operation_id": uid.upper(), "operation_type": kind,
            "cad_parent_operation_id": parent, "process_group_id": group,
            "required_predecessor_ids": [], "source_segment_ids": [uid+"_geometry"]}


class PairingTests(unittest.TestCase):
    def pair(self):
        return {"workflow_version": 2, "operations": [action("o008", "FLANGING"), action("o009", "PRE_PUNCH", "O008")]}

    def test_unique_cad_pair_automatically_shares_group(self):
        p = self.pair()
        report = synchronize_pairing(p)
        self.assertEqual([o["process_group_id"] for o in p["operations"]], ["PG_O008", "PG_O008"])
        self.assertTrue(all(v["status"] == "PAIRED" for v in report["statuses"].values()))

    def test_order_and_feature_shape_do_not_change_pairing(self):
        p = self.pair()
        p["operations"].reverse()
        p["operations"][0]["geometry_shape"] = "NONCIRCULAR"
        synchronize_pairing(p)
        self.assertEqual(p["operations"][0]["process_group_id"], "PG_O008")

    def test_no_cad_relation_does_not_pair_by_name_or_geometry(self):
        p = self.pair()
        p["operations"][1]["cad_parent_operation_id"] = None
        for op in p["operations"]:
            op["name"] = "光面翻孔11"
            op["source_segment_ids"] = ["same_location"]
        report = synchronize_pairing(p)
        self.assertTrue(all(o["process_group_id"] is None for o in p["operations"]))
        self.assertTrue(all(s["status"] == "PENDING" for s in report["statuses"].values()))

    def test_missing_parent_remains_pending_then_resolves(self):
        p = self.pair()
        parent = p["operations"].pop(0)
        report = synchronize_pairing(p)
        self.assertIn("尚未录入", report["statuses"]["o009"]["message"])
        p["operations"].append(parent)
        synchronize_pairing(p)
        self.assertEqual(p["operations"][0]["process_group_id"], "PG_O008")

    def test_multiple_prehole_candidates_are_not_arbitrarily_chosen(self):
        p = self.pair()
        p["operations"].append(action("o010", "PRE_PUNCH", "O008"))
        report = synchronize_pairing(p)
        self.assertEqual(report["issues"][0]["code"], "PAIRING_AMBIGUOUS")
        self.assertTrue(all(o["process_group_id"] is None for o in p["operations"]))

    def test_existing_group_id_is_canonicalized_without_changing_members(self):
        p = self.pair()
        p["operations"][1]["process_group_id"] = "PG001"
        report = synchronize_pairing(p)
        first = deepcopy(p)
        synchronize_pairing(p)
        self.assertEqual(p, first)
        self.assertTrue(all(o["process_group_id"] == "PG_O008" for o in p["operations"]))
        self.assertEqual(report["group_renames"], [{"operation_ids": ["O009", "O008"], "old_group_ids": ["PG001"], "new_group_id": "PG_O008"}])

    def test_both_members_of_an_old_explicit_group_are_renamed_together(self):
        p = self.pair()
        for op in p["operations"]:
            op["process_group_id"] = "PG001"
        synchronize_pairing(p)
        self.assertTrue(all(o["process_group_id"] == "PG_O008" for o in p["operations"]))

    def test_renamed_flange_updates_canonical_group_but_keeps_same_pair(self):
        p = self.pair()
        synchronize_pairing(p)
        pair_uids = p["operations"][0]["process_group_pair_uids"]
        p["operations"][0]["operation_id"] = "O088"
        p["operations"][1]["cad_parent_operation_id"] = "O088"
        synchronize_pairing(p)
        self.assertTrue(all(o["process_group_id"] == "PG_O088" for o in p["operations"]))
        self.assertEqual(p["operations"][0]["process_group_pair_uids"], pair_uids)

    def test_conflicting_explicit_groups_not_overwritten(self):
        p = self.pair()
        p["operations"][0]["process_group_id"] = "PG_A"
        p["operations"][1]["process_group_id"] = "PG_B"
        report = synchronize_pairing(p)
        self.assertEqual(report["issues"][0]["code"], "PAIRING_GROUP_CONFLICT")
        self.assertEqual([o["process_group_id"] for o in p["operations"]], ["PG_A", "PG_B"])

    def test_renaming_cannot_silently_split_a_group_with_extra_members(self):
        p = self.pair()
        for op in p["operations"]:
            op["process_group_id"] = "PG001"
        p["operations"].append(action("o010", "DIMPLE", group="PG001"))
        report = synchronize_pairing(p)
        self.assertEqual(report["issues"][0]["code"], "PAIRING_GROUP_CONFLICT")
        self.assertTrue(all(o["process_group_id"] == "PG001" for o in p["operations"]))

    def test_changed_parent_removes_stale_auto_pair(self):
        p = self.pair()
        synchronize_pairing(p)
        p["operations"][1]["cad_parent_operation_id"] = None
        synchronize_pairing(p)
        self.assertTrue(all(o["process_group_id"] is None for o in p["operations"]))
        self.assertTrue(all(o.get("process_group_source") != AUTO_SOURCE for o in p["operations"]))

    def test_reverse_cad_link_not_inverted_to_force_a_pair(self):
        p = self.pair()
        p["operations"][0]["cad_parent_operation_id"] = "O009"
        p["operations"][1]["cad_parent_operation_id"] = None
        before = deepcopy(p)
        synchronize_pairing(p)
        self.assertEqual(p, before)

    def test_existing_explicit_pair_without_cad_is_preserved(self):
        p = self.pair()
        p["operations"][1]["cad_parent_operation_id"] = None
        for op in p["operations"]:
            op["process_group_id"] = "HUMAN_CONFIRMED"
        report = synchronize_pairing(p)
        self.assertEqual(report["statuses"]["o009"]["status"], "EXPLICIT_PAIR")
        self.assertEqual(p["operations"][1]["process_group_id"], "HUMAN_CONFIRMED")

    def test_no_sources_or_predecessors_or_cad_relations_are_changed(self):
        p = self.pair()
        before = deepcopy(p)
        synchronize_pairing(p)
        for old, new in zip(before["operations"], p["operations"]):
            for field in ("uid", "operation_id", "operation_type", "cad_parent_operation_id", "source_segment_ids", "required_predecessor_ids"):
                self.assertEqual(old[field], new[field])

    def test_migration_and_fingerprint_normalize_pairing_without_mutating_input(self):
        p = self.pair()
        before = deepcopy(p)
        migrated = annotation_project(p)
        normalized = deepcopy(p)
        synchronize_pairing(normalized)
        self.assertEqual(p, before)
        self.assertEqual(migrated["operations"][0]["process_group_id"], "PG_O008")
        self.assertEqual(batch_fingerprint(p, {}), batch_fingerprint(normalized, {}))

