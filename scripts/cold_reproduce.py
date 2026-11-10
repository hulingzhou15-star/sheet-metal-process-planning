"""Prepare a clean source-only checkout and reproduce there, without old outputs."""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
EXCLUDE={'.git','.local','.venv','.venv-m3','.runtime','runtime_deps','node_modules','__pycache__','.pytest_cache','outputs'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--destination',type=Path,default=ROOT/'.local/reproduction/workspace')
    parser.add_argument('--prepare-only',action='store_true')
    args=parser.parse_args();dest=args.destination.resolve()
    if dest.exists():raise FileExistsError('Cold destination must be new: '+str(dest))
    if not dest.is_relative_to(ROOT/'.local/reproduction'):raise ValueError('Use a destination below .local/reproduction')
    dest.mkdir(parents=True)
    copied=[]
    moved = json.loads((ROOT/'registry/migration_input_map.json').read_text(encoding='utf-8'))['files']
    old_inputs = {Path(row['old']).resolve() for row in moved
                  if Path(row['old']).is_absolute() and Path(row['old']).resolve().is_relative_to(ROOT)}
    for base,dirs,files in os.walk(ROOT):
        dirs[:]=[d for d in dirs if d not in EXCLUDE and Path(base)/d not in {ROOT/'data/raw', ROOT/'data/backups'}]
        for name in files:
            source=Path(base)/name
            if source.suffix in {'.pyc','.log'} or source.resolve() in old_inputs:continue
            rel=source.relative_to(ROOT);target=dest/rel;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target);copied.append(rel.as_posix())
    if (dest/'outputs').exists() or (dest/'information_extract/outputs').exists():raise AssertionError('Old outputs copied')
    manifest={'workspace':str(dest),'files':copied,'old_outputs_copied':False,'python':sys.executable,'successful':False}
    record=dest.parent/(dest.name+'_result.json');record.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Source-only workspace prepared:',dest,len(copied),'files',flush=True)
    if args.prepare_only:return
    npm=shutil.which('npm.cmd') or shutil.which('npm')
    subprocess.run([npm,'ci','--ignore-scripts'],cwd=dest,check=True)
    subprocess.run([sys.executable,'-B','-X','utf8',str(dest/'scripts/project.py'),'reproduce','--part','P001','--target','core'],cwd=dest,check=True)
    manifest['successful']=True
    record.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':main()
