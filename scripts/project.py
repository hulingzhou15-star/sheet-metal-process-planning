"""Single repository entry point; delegates algorithms to their owning modules."""
from pathlib import Path
import argparse
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CONFIG = json.loads((ROOT/'configs/project.json').read_text(encoding='utf-8'))
GEOMETRY_BUILD = [
    'build_three_d_review', 'build_material_state_review', 'build_stage_review',
    'build_process_drawing_evidence', 'build_finite_material_model',
    'build_finite_material_states', 'build_finite_material_highlights',
    'audit_missing_material_features', 'extract_flanging_step_inventory',
    'build_local_forming_states',
]


def run(command, name):
    folder = ROOT/'outputs/run_logs'; folder.mkdir(parents=True, exist_ok=True)
    started=time.monotonic()
    print('Running '+name, flush=True)
    env={**os.environ,'PYTHONUTF8':'1','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1'}
    env.pop('PYTHONPATH',None)
    with (folder/(name+'.log')).open('w',encoding='utf-8') as log:
        result=subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT)
    if result.returncode:
        print((folder/(name+'.log')).read_text(encoding='utf-8')[-6000:],flush=True)
        raise RuntimeError(f'{name} failed ({result.returncode}); see outputs/run_logs/{name}.log')
    print(f'Passed {name} ({time.monotonic()-started:.1f}s)',flush=True)


def python_module(name):
    run([sys.executable,'-B','-X','utf8','-m',name],name.rsplit('.',1)[-1])


def doctor():
    from scripts.verify_core import verify_protected
    files=verify_protected()
    packages={name:importlib.metadata.version(name) for name in ['openpyxl','numpy','ortools','cadquery-ocp','vtk','ezdxf']}
    import OCP, ezdxf
    if any('.cache/codex' in str(p).replace('\\','/') for p in [sys.base_prefix,OCP.__file__,ezdxf.__file__]):
        raise RuntimeError('Runtime still depends on a Codex account cache')
    if not (ROOT/'node_modules/playwright/package.json').is_file():
        raise RuntimeError('Run npm ci in the repository')
    print(json.dumps({'protected_files':files,'python':sys.version,'base_runtime':sys.base_prefix,
                      'packages':packages,'node':shutil.which('node'),'workbook':CONFIG['workbook'],
                      'review_exists':(ROOT/CONFIG['review']).exists()},ensure_ascii=False,indent=2))


def verify():
    python_module('scripts.verify_core')
    python_module('scripts.test_current')
    python_module('information_extract.tests.test_local_forming')
    python_module('information_extract.scripts.verify_migration_geometry')
    run(['node','information_extract/tests/test_local_forming_browser.mjs'],'local_forming_browser')


def main():
    parser=argparse.ArgumentParser(description='项目安装检查、复现、验证和审核入口')
    parser.add_argument('command',choices=['doctor','status','reproduce','verify','review','annotate'])
    parser.add_argument('--part',default='P001')
    parser.add_argument('--target',choices=['core','planning'],default='core')
    args=parser.parse_args()
    os.chdir(ROOT)
    if args.command=='status':
        print((ROOT/'CURRENT_STATUS.md').read_text(encoding='utf-8'));return
    if args.command=='annotate':
        if args.part not in {'P001','P002','P003','P004'}:parser.error('Unknown part')
        config='app' if args.part=='P001' else args.part
        subprocess.run([sys.executable,'-B','-X','utf8','information_extract/scripts/serve.py','--config',f'configs/{config}.json'],cwd=ROOT,check=True);return
    if args.part!='P001':parser.error('Core reconstruction currently supports P001 only')
    if args.command=='doctor':doctor()
    elif args.command=='reproduce':
        doctor();python_module('scripts.verify_core')
        if args.target=='planning':return
        python_module('scripts.test_current')
        for module in GEOMETRY_BUILD:python_module('information_extract.scripts.'+module)
        python_module('information_extract.tests.test_local_forming')
        python_module('information_extract.scripts.verify_migration_geometry')
        python_module('information_extract.scripts.check_local_forming_collision')
        python_module('information_extract.scripts.build_local_forming_review')
        run(['node','information_extract/tests/test_local_forming_browser.mjs'],'local_forming_browser')
        python_module('information_extract.scripts.build_local_forming_report')
    elif args.command=='verify':verify()
    elif args.command=='review':
        page=ROOT/CONFIG['review']
        if not page.is_file():raise FileNotFoundError('Run reproduce --part P001 --target core first')
        os.startfile(page)


if __name__=='__main__':main()
