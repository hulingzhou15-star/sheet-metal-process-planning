"""Read-only source inventory; native WKF/DWG history is not inferred from names."""
from pathlib import Path
import hashlib
import json

ROOT=Path(__file__).resolve().parents[2]
MODULE=ROOT/'information_extract'
FOLDER=MODULE/'sources/P001/originals'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    files=[]
    for path in sorted(FOLDER.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in {'.wkf','.bak','.stp','.step','.x_t','.dwg'}:
            continue
        with path.open('rb') as stream:
            header=stream.read(32)
        files.append({'path':str(path),'bytes':path.stat().st_size,'sha256':digest(path),
                      'header_hex':header.hex(),
                      'role':'NATIVE_HISTORY_UNINSPECTED' if path.suffix.lower() in {'.wkf','.bak','.dwg','.x_t'} else 'KNOWN_FORMED_STEP_EXPORT'})
    old_outline=ROOT/'baselines/planning_history/m3_visual_review_v05/internal/outline_extraction_report.json'
    outline=json.loads(old_outline.read_text(encoding='utf-8'))
    source=MODULE/'sources/P001/legacy_outline.html'
    outline_check={key:outline[key] for key in ['source_path','source_sha256','boundary_model',
                     'exterior_contour_count','closed_component_count','display_only']}
    outline_check['source_hash_still_matches']=digest(source)==outline['source_sha256']
    outline_check['meaning']='Existing display recovery, not a validated initial material domain; no union/extrusion performed.'
    report={'part_id':'P001','user_flat_model_availability':'UNCONFIRMED',
            'files':files,'known_formed_step_has_intermediate_states':False,
            'separate_flat_3d_model_confirmed':False,
            'native_wkf_or_backup_may_contain_other_states':'UNKNOWN_NOT_PARSED',
            'outline_evidence':outline_check,
            'design_decision':'A separately exported flat STEP is optional. A computational flat reference can be built from checked 2D material geometry and thickness, with unverified cut/deformation details retained as unknown.',
            'next_independent_work':['material-region association','local coordinate frames','bend transition and attached-feature mapping'],
            'prohibited_inferences':['File name or backup difference proves a process stage',
                                     'All final holes already exist in the initial blank',
                                     'Display contours are a validated physical blank']}
    out=MODULE/'outputs/three_d_state_v01/P001/initial_state_source_audit.json'
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'file_count':len(files),'separate_flat_confirmed':False,
                      'native_history':'UNKNOWN','display_contour_count':outline['exterior_contour_count'],
                      'source_outline_hash_matches':outline_check['source_hash_still_matches']},ensure_ascii=False))


if __name__=='__main__':
    main()
