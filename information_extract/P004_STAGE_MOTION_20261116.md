# P004 运动筛查与候选重新生成

## 工艺与几何输入

使用 P004 第 12 版的 77 个已录动作，包含 21 个折弯、38 个去料和 18 个局部成形动作。按 H3 的 4 条、H4 的 12 条及 H7 的 46 条先后约束生成，P5 最优值为 0。工序顺序由允许的工序能力及先后条件求解，专家对应另作参考。

由 STEP 建立 25 个刚性材料单元，有限折弯带共 27 段。各成品材料单元最大正体积交叠为 0，分割体积残差约 8.56×10⁻⁸ mm³。二维绑定按当前 DXF 配准。O013 的 170° 为内夹角，STEP 实际转角为 10°，四处共轴支撑使用同一个动作进度。O014 的多处支撑在运动树上用一条代表边，同时在分区证据中保留完整连接。中面与 STEP 圆柱轴分别以 121 组 O013/O014 进度组合验证闭合，矩阵残差不超过 7.11×10⁻¹⁵。

## 逐序材料

56 个非折弯动作均建立材料对应。12 个预孔各有独立事件，与所属翻孔严格先后。18 个局部成形动作采用对应 STEP 完成态，两处刻印深度均为 0.3 mm。22 个普通去料采用对应 STEP 孔轮廓，O068–O071 采用已录 DXF 避让轮廓并保留成品舌片。前段去料与成形按工序完成态更新。

OP40 同时容许折弯和凸米。首例逐序状态验证表明 OP40 进入态与各运动采样使用 38 个去料和 14 个成形均已完成的材料，结束端才增加 O043–O046 四处凸米。刚性折弯运动定义使用同序共同进度。

## 筛查与展示

在 P5 为 0 的前提下生成 64 个初始候选。它们包含 39 种折弯分配，均通过 24 和 96 等分刚性区运动检查，本轮无需因正体积交叠排除候选。随后补全并核对 64 个完整方案，按评分和折弯运动差异选出 16 个。P004_R005 排名第一，次级评分为 4/125。

入选方案共检验 4,784 个逐序状态及 1,435,200 个区域对×状态，正体积交叠和几何内核未决均为 0。主轮用 STEP 板中面交线作为刚体转轴，保留 DXF 配准的展开位置。首例在两种转轴下各做 77 个折弯状态核验，均未检出正体积交叠。25 个刚性材料单元的检查覆盖运动采样与局部成形端态。

保守轴向及定向包围盒用于确定分离，其余区域对由 OCCT 做实体求交。本轮记录正体积交叠，接触和分离的零体积结果共用一类。旧距离检查对照记录单独保留在 reference_axis_probes。相关 15 项测试通过。另运行旧图纸与补充几何测试时，4 项断言失败、9 项设置错误，涉及旧配置或快照与现有 P004 绑定版本不一致。

- [双视图展示](http://127.0.0.1:8770/p004_motion_regeneration_v01/stage/index.html)
- [64 个完整方案](outputs/p004_motion_regeneration_v01/pool.json)
- [16 个入选方案和逐序报告](outputs/p004_motion_regeneration_v01/plans.json)
- [筛查和评分汇总](outputs/p004_motion_regeneration_v01/summary.json)

## 复现入口

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.generate_p004_allocation --prepare-only
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.build_p004_material_basis
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.generate_p004_allocation
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.regenerate_p004_candidates
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.build_p004_stage_review
```
