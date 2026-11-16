import numpy as np
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt

from information_extract.three_d.p004_collision import exact_overlap
from information_extract.three_d.p004_motion import Schedule, build_model


def test_p004_positive_volume_distinguishes_overlap_from_contact():
    base = BRepPrimAPI_MakeBox(1.,1.,1.).Shape()
    for offset, expected in [(2.,0.),(1.,0.),(.25,.75)]:
        other = BRepPrimAPI_MakeBox(gp_Pnt(offset,0,0),1.,1.,1.).Shape()
        result = exact_overlap(base,other,dict(volume_tolerance_mm3=1e-6))
        assert result['status']==('OVERLAP' if expected else 'NO_POSITIVE_VOLUME')
        assert abs(result['intersection_volume_mm3']-expected)<1e-9


def test_p004_shared_fold_supports_and_same_station_material_event():
    operations = [
        dict(operation_id='O013',operation_type='BEND',bend_angle_deg=170),
        dict(operation_id='O043',operation_type='DIMPLE'),
    ]
    facts = dict(operations=operations,stations=[dict(station_id='OP40',order=4)])
    joints = [
        dict(operation_id='O013',joint_id='J1',parent_region_id='base',child_region_id='rib1',
             angle_deg=10,hinge_point_mm=[0,0,0],axis_direction=[1,0,0]),
        dict(operation_id='O013',joint_id='J2',parent_region_id='base',child_region_id='rib2',
             angle_deg=-10,hinge_point_mm=[20,0,0],axis_direction=[-1,0,0]),
    ]
    skeleton = dict(root_region_id='base',joints=joints,regions=[
        dict(region_id='base',parent_region_id=None),dict(region_id='rib1',parent_region_id='base'),
        dict(region_id='rib2',parent_region_id='base'),
    ])
    model = build_model(facts,skeleton,dict(domains=[dict(operation_id='O013',joint_id='J1')]))
    schedule = Schedule(model,dict(assignments={'O013':'OP40','O043':'OP40'}))

    entry,half,done = [schedule.frame(*sample) for sample in schedule.samples(2)]
    assert entry['executed_forming_ids']==half['executed_forming_ids']==[]
    assert half['current_operation_ids']==['O013','O043']
    assert half['joint_angles_deg']=={'J1':5.,'J2':-5.}
    np.testing.assert_allclose(half['transforms']['rib1'],half['transforms']['rib2'],atol=1e-14)
    assert done['executed_forming_ids']==['O043']
    assert all(np.allclose(p,np.eye(4)) for p in done['transforms'].values())
