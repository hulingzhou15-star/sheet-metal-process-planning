"""Synthetic Geometry and read-only frozen P001 visual fixtures."""
from functools import lru_cache
from pathlib import Path

from m3_fixtures import context, region
from src.data.schema import Geometry, GeometrySegment as S, Operation
from src.visualization import build_geometry_catalog, load_visualization_config

ROOT=Path(__file__).resolve().parents[1]


def circle(gid,x,y,r=1):
    return Geometry("P",gid,(S(1,"CIRCLE",center_x=x,center_y=y,radius=r),))


def line(gid,start,end):
    return Geometry("P",gid,(S(1,"BEND_LINE",*start,*end),))


def mini():
    geometries=[line("GB1",(0,0),(20,0)),line("GB2",(0,5),(10,5)),line("GB3",(30,0),(30,20)),
                circle("GC",5,7),circle("GX",100,100,2),circle("GP",5,7,1.2),circle("GD",8,8,.5),
                Geometry("P","GF",(S(1,"CIRCLE",center_x=5,center_y=7,radius=2),S(2,"CIRCLE",center_x=5,center_y=7,radius=3))),
                Geometry("P","GBR",(S(1,"BRIDGE",11,11,13,13,12,12),)),
                Geometry("P","GI",(S(1,"LINE",0,0,2,0),S(2,"ARC",2,0,2,2,2,1,1,180),S(3,"LINE",2,2,0,2),S(4,"LINE",0,2,0,0)))]
    ops=[Operation("P",oid,kind,geometry_id=gid,process_group_id="PG" if oid in ("P","F") else None,
                   required_predecessor_ids=("I",) if oid=="B3" else ())
         for oid,kind,gid in (("B1","BEND","GB1"),("B2","BEND","GB2"),("B3","BEND","GB3"),
                              ("C","CIRCLE_PUNCH","GC"),("X","CIRCLE_PUNCH","GX"),("P","PRE_PUNCH","GP"),
                              ("F","FLANGING","GF"),("D","DIMPLE","GD"),("BR","BRIDGE","GBR"),("I","IRREGULAR_PUNCH","GI"),
                              ("T","TRIM",None),("M","MARKING",None))]
    regions=[region("B1","R_BIG",start=(0,0),end=(20,0),depth=20,far=20),
             region("B2","R_SMALL",start=(0,5),end=(10,5),depth=5,far=10,parent="R_BIG"),
             region("B3","R_VERTICAL",start=(30,0),end=(30,20),depth=20,far=50,axis="VERTICAL",side="POSITIVE_X")]
    a={op.operation_id:"OP90" for op in ops};a.update(B1="OP05",B2="OP80",B3="OP05",C="OP10",F="OP10")
    c=context(ops,regions,assignments=a,geometries=geometries)
    b={**a,"C":"OP80","B2":"OP90","I":"OP10"}
    cfg=load_visualization_config()
    return c,a,b,cfg,build_geometry_catalog(c,cfg)


@lru_cache(maxsize=1)
def actual():
    from scripts.build_visual_review_pack import load_visual_inputs, prepare_visual_data
    from src.constraints import load_constraint_context
    c=load_constraint_context(ROOT/"data/parts/P001/钣金加工数据表_P001.xlsx")
    cfg=load_visualization_config()
    plans,reps,pairs,mapping=load_visual_inputs(c,ROOT/"outputs/m3_candidates",ROOT/"outputs/m3_human_review")
    cat,views,comparisons,debug=prepare_visual_data(c,plans,reps,pairs,mapping,cfg)
    return c,cfg,cat,views,comparisons,debug,plans,reps,pairs,mapping
