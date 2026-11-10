"""Current facts/graph/rules and annotation regression suites (no old output fixtures)."""
from pathlib import Path
import json
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(ROOT/'information_extract')]
ROOT_TESTS=['test_data_layer','test_geometry_distance','test_graph_features','test_graph_knn',
            'test_graph_semantics','test_graph_report','test_m3_reference_free_v07','test_m3_rule_revision_v06']
ANNOTATION_TESTS=['test_geometry','test_drawing_model','test_source_subsegments','test_source_deduplication',
                  'test_source_cleanup','test_pairing','test_p002_drawing','test_p003_drawing','test_p004_drawing',
                  'test_p004_supplement','test_exporter','test_bend_angle','test_api','test_annotation_workflow']


def main():
    out=ROOT/'outputs/verification';out.mkdir(parents=True,exist_ok=True)
    (ROOT/'information_extract/outputs/test_tmp').mkdir(parents=True,exist_ok=True)
    from information_extract.scripts.convert_dxf_to_html import build as drawing_preview
    for part in ['P002','P003','P004']:
        drawing_preview('configs/'+part+'.json')
    # File discovery keeps the annotation tests' historical extract_app import boundary.
    suite=unittest.TestSuite()
    for name in ROOT_TESTS:
        suite.addTests(unittest.defaultTestLoader.loadTestsFromName('tests.'+name))
    import importlib.util
    for name in ANNOTATION_TESTS:
        spec=importlib.util.spec_from_file_location('annotation_'+name,ROOT/'information_extract/tests'/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    with (out/'current_tests.log').open('w',encoding='utf-8') as stream:
        result=unittest.TextTestRunner(stream=stream,verbosity=2).run(suite)
    report={'tests_run':result.testsRun,'successful':result.wasSuccessful(),
            'failures':[(str(t),e) for t,e in result.failures],'errors':[(str(t),e) for t,e in result.errors],
            'scope':{'planning_modules':ROOT_TESTS,'annotation_modules':ANNOTATION_TESTS},
            'historical_snapshot_suites':'Not included; their old baselines/outputs require explicit archival replay.'}
    (out/'current_tests.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'tests_run':result.testsRun,'successful':result.wasSuccessful(),'failures':len(result.failures),'errors':len(result.errors)}),flush=True)
    if not result.wasSuccessful():print((out/'current_tests.log').read_text(encoding='utf-8')[-18000:])
    raise SystemExit(0 if result.wasSuccessful() else 1)


if __name__=='__main__':main()
