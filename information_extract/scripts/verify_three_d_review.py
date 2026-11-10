"""Hash-verify frozen artifacts against the initial execution baseline."""
from pathlib import Path
import json
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from information_extract.three_d.facts import frozen_snapshot, sha256

out=ROOT/'information_extract/outputs/three_d_state_v01/P001'
before=json.loads((out/'frozen_before.json').read_text(encoding='utf-8'))
print(f'Verifying {len(before)} frozen artifacts...',flush=True)
after=frozen_snapshot(ROOT)
changed=[p for p in before if after.get(p)!=before[p]]
added=sorted(set(after)-set(before))
original=json.loads((ROOT/'information_extract/outputs/cad_single_solid_20261103/P001/verification.json').read_text(encoding='utf-8'))
source_checks={p:sha256(p)==value for p,value in original['source_hashes'].items()}
report={'frozen_artifact_count':len(before),'changed_or_deleted':changed,'added_outside_module':added,
        'original_cad_hash_checks':source_checks,'passed':not changed and not added and all(source_checks.values())}
(out/'frozen_verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False),flush=True)
if not report['passed']:
    raise SystemExit(1)
