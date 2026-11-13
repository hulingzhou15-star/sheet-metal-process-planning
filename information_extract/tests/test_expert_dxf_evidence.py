"""Geometry reflection and inherited DXF colors must survive evidence extraction."""
import tempfile
from copy import deepcopy
import unittest
from pathlib import Path
import ezdxf
import numpy as np
from information_extract.scripts.extract_p002_expert_dxf import extract, primitive, transformed, key, align
from information_extract.scripts.build_p002_expert_review import apply_user_decisions, audit_identified_radius_variant


class ExpertDxfEvidenceTests(unittest.TestCase):
    def test_composite_confirmation_preserves_partial_counts(self):
        rows=[{'operation_id':'O112','status':'MULTIPLE_STATION_HIGHLIGHTS','source_count':33,
               'evidence':[{'station_id':'OP20','highlighted_source_count':3},
                           {'station_id':'OP40','highlighted_source_count':19}]}]
        evidence=deepcopy(rows[0]['evidence'])
        apply_user_decisions(rows,[{'operation_id':'O112','operation_type':'IRREGULAR_PUNCH'}],
                             {'decisions':[{'id':'confirmed','operation_ids':['O112'],
                              'operation_type':'IRREGULAR_PUNCH','station_id':'OP40','quote':'暂按一步成形',
                              'resolution_method':'USER_CONFIRMED_COMPOSITE_SINGLE_STAGE'}]})
        self.assertEqual(rows[0]['proposed_expert_station'],'OP40')
        self.assertEqual(rows[0]['evidence'],evidence)

    def test_identified_variant_rejects_shift_and_wrong_color(self):
        def rectangle(radius):
            shapes=[]
            for i in range(4):
                angle=i*np.pi/2
                m=np.array([[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]])
                shapes.append(transformed(primitive('LINE',[[2.5,-2.5+radius],[2.5,2.5-radius]]),m,np.zeros(2)))
                c=2.5-radius
                shapes.append(transformed(primitive('ARC',[[2.5,c],[c,2.5],[c+radius/np.sqrt(2),c+radius/np.sqrt(2)]],radius),m,np.zeros(2)))
            return shapes
        source=[dict(s,source_id=str(i)) for i,s in enumerate(rectangle(.6942797494))]
        target=[dict(s,source_path=['handle',str(i)],color='#00ff00') for i,s in enumerate(rectangle(.5))]
        frame={'station_id':'OP20','ancillary':False,'alignment_supported':True,'frame_id':'view',
               'matrix':[[1,0],[0,1]],'translation':[0,0],'targets':target}
        data={'operations':[{'operation_id':'O117','source_segment_ids':[str(i) for i in range(8)]}],
              'flat_primitives':source,'frames':[frame]}
        decision={'operation_ids':['O117'],'station_id':'OP20','expert_entity_handle':'handle'}
        audit=audit_identified_radius_variant(data,decision)
        self.assertLess(audit['bbox_max_difference_mm'],1e-8)
        self.assertAlmostEqual(audit['expert_radii_mm'][0],.5)
        target[0]['color']='#808080'
        with self.assertRaises(ValueError):audit_identified_radius_variant(data,decision)
        target[0]['color']='#00ff00'
        frame['targets']=[transformed(s,np.eye(2),np.array([1,0])) for s in target]
        with self.assertRaises(ValueError):audit_identified_radius_variant(data,decision)

    def test_explicit_draw_decision_preserves_multiple_highlights(self):
        rows=[{'operation_id':'O063','status':'MULTIPLE_STATION_HIGHLIGHTS',
               'source_count':3,'evidence':[{'station_id':s,'highlighted_source_count':3} for s in ('OP10','OP30')]}]
        before=deepcopy(rows[0]['evidence'])
        decision={'decisions':[{'id':'confirmed','operation_ids':['O063'],'operation_type':'DRAW_FORM',
                                'station_id':'OP10','quote':'单工序，仅第1序'}]}
        apply_user_decisions(rows,[{'operation_id':'O063','operation_type':'DRAW_FORM'}],decision)
        self.assertEqual(rows[0]['proposed_expert_station'],'OP10')
        self.assertEqual(rows[0]['geometric_status'],'MULTIPLE_STATION_HIGHLIGHTS')
        self.assertEqual(rows[0]['evidence'],before)

    def test_decision_does_not_guess_absent_or_partial_geometry(self):
        decision={'decisions':[{'id':'confirmed','operation_ids':['O063'],'operation_type':'DRAW_FORM',
                                'station_id':'OP10','quote':'单工序，仅第1序'}]}
        for count in (0,2):
            rows=[{'operation_id':'O063','status':'PARTIAL_HIGHLIGHT','source_count':3,
                   'evidence':[{'station_id':'OP10','highlighted_source_count':count}]}]
            with self.assertRaisesRegex(ValueError,'lacks full geometry'):
                apply_user_decisions(rows,[{'operation_id':'O063','operation_type':'DRAW_FORM'}],decision)

    def test_block_and_layer_colors(self):
        doc=ezdxf.new();doc.layers.new('green',dxfattribs={'color':3})
        doc.modelspace().add_line((0,0),(1,0),dxfattribs={'layer':'green','color':256})
        block=doc.blocks.new('child')
        block.add_circle((0,0),1,dxfattribs={'color':0})
        doc.modelspace().add_blockref('child',(10,20),dxfattribs={'color':1})
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'color.dxf';doc.saveas(p);geometry,_,_=extract(p)
        line=next(g for g in geometry if g['kind']=='LINE')
        circle=next(g for g in geometry if g['kind']=='CIRCLE')
        self.assertEqual(line['color'],'#00ff00')
        self.assertEqual(circle['color'],'#ff0000')
        self.assertEqual(circle['points'],[[10.,20.]])

    def test_arc_reflection_is_not_confused_with_other_half(self):
        arc=primitive('ARC',[[1,0],[-1,0],[0,1]],1)
        reflected=transformed(arc,np.array([[1,0],[0,-1]]),np.array([3,4]))
        self.assertEqual(key(reflected),key(primitive('ARC',[[4,4],[2,4],[3,3]],1)))
        self.assertNotEqual(key(reflected),key(primitive('ARC',[[4,4],[2,4],[3,5]],1)))

    def test_registration_recovers_mirror_and_translation(self):
        source=[primitive('CIRCLE',[[0,0]],1),primitive('CIRCLE',[[8,1]],2),
                primitive('CIRCLE',[[2,5]],3),primitive('LINE',[[1,2],[4,7]])]
        matrix=np.array([[1,0],[0,-1]]);shift=np.array([100,200])
        targets=[transformed(s,matrix,shift) for s in source]
        result=align(source,targets)[0]
        self.assertEqual(result['matched_count'],4)
        self.assertEqual(result['matrix'],matrix.tolist())
        self.assertEqual(result['translation'],shift.tolist())


if __name__=='__main__':unittest.main()
