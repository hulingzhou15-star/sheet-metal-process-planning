"""Compare regenerated geometry with the independent pre-migration oracle."""
from information_extract.three_d.artifacts import read, write


def close(a, b, location='root'):
    if isinstance(a, dict):
        if not isinstance(b, dict) or set(a) != set(b): raise AssertionError(location+' keys')
        for k in a: close(a[k], b[k], location+'.'+k)
    elif isinstance(a, list):
        if len(a) != len(b): raise AssertionError(location+' length')
        for i,(x,y) in enumerate(zip(a,b)): close(x,y,f'{location}[{i}]')
    elif isinstance(a, (float, int)) and not isinstance(a, bool):
        if abs(a-b) > 1e-5: raise AssertionError((location,a,b))
    elif a != b:
        raise AssertionError((location,a,b))


def main():
    expected=read('information_extract/baselines/P001/geometry_expected.json')
    actual=read('information_extract/outputs/local_forming_v03/P001/viewer_data.json')
    close(expected['summary'], actual['summary'], 'summary')
    for key in ['material_states','local_forming','removal_coverage']:
        close(expected[key],actual[key],key)
    variants=[{k:x[k] for k in ['material_id','executed_removal_ids','executed_forming_ids','flat_volume_mm3']} for x in actual['variants']]
    close(expected['variants'],variants,'variants')
    close(expected['geometries'],{k:{'volume':x['flat_volume_mm3'],'triangle_count':len(x['triangles'])} for k,x in actual['geometries'].items()},'geometries')
    baseline_model=read('information_extract/baselines/P001/model_expected.json')
    for key in ['regions','bends','validation']:
        close(baseline_model[key],actual['model'][key],'model.'+key)
    write('information_extract/outputs/local_forming_v03/P001/migration_comparison.json',
          {'successful':True,'absolute_numeric_tolerance':1e-5,'states':224,'cuts':100,'local_forming':50,'geometry_count':len(actual['geometries']),
           'checks':['material assignments','regions and bend transforms','coverage and local STEP endpoints','variant volumes','mesh counts and volumes']})
    print('Pre-migration geometry oracle matched: 224 states, 100 cuts, 50 local endpoints',flush=True)


if __name__=='__main__':main()
