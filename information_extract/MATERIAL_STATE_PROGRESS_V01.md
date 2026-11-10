# 材料关联与局部工序回放：执行记录 v0.1

日期：2026-11-03。

2026-11-05 非直角复核：[六处角度检查](outputs/bend_angle_check/P001/REPORT.md)。用户确认 O057/O072/O075/O146/O149/O161 按现有 CAD 的约 166.07°，不采用最初口述的 166.67°。STEP 内夹角为 166.069736°，原模型已使用有向转角 -13.930264°，没有误套 90°。重新读取 STEP 法向，核验六处五种进度共 30 个方向/角度检查通过；DXF 有对应斜率的辅助几何，未找到直接的角度文字。阶段页面区分显示内夹角和转动量，同时减细高亮线与白边。此结论不替代有限宽或物理成形验证。

2026-11-05 工序检视：[16 个候选的逐序页面](outputs/stage_review_v01/P001/review.html)，[报告](outputs/stage_review_v01/P001/REPORT.md)。按原分配的 Station.order 生成 112 个完成态；同序折弯统一完成，动作仅在所属工序及以后显示。59 个折弯轴线和 123 个板面包含建议可显示三维线框，29 个归属未解决、13 个缺二维。该全件页面为零宽折弯的板面运动示意，未沿用局部有限宽参数；孔只是按序出现的轮廓，不是实体开孔，其他局部成形高度仍未建模。不能据此宣称全件逐序实体或碰撞检测已完成。

2026-11-05 全件检视入口：[P001 全零件特征检视](outputs/full_part_review_v01/P001/review.html)，[覆盖报告](outputs/full_part_review_v01/P001/REPORT.md)。224 个动作完整列出；59 个折弯对应建议（其中 O009/O010 有局部几何回放）、7 个基准孔证据、2 个附属位置随动，其余 143 个仅有二维，13 个缺二维。全件显示成品 STEP，不代表完整工序运动仿真；未更改冻结数据和工艺规则。

2026-11-05 最新局部模型：[有限宽条带三维回放](outputs/finite_bend_v01/P001/review.html) 与 [建模报告](outputs/finite_bend_v01/P001/REPORT.md)。从 DXF 提取 O009/O010 各 0.4 mm 条带，替代零宽连接后，展开孔中心、末端边界、第5序厚度投影与 STEP 外圆柱均通过几何核对。原 0.8 mm 展开位置残差已在本局部几何表示中解释；恒厚插值的条带体积不守恒，明确保留为几何原型、禁止用于候选过滤。原回放保留不覆盖。

2026-11-05 参数确认补充：用户确认 P001 板厚采用 **0.8 mm**，新提供 DXF 的产品图文字也明确记载 t 0.8。见 `configs/P001_confirmed_material_parameters.json` 与 `outputs/dxf_parameter_audit/P001/REPORT.md`。以下“尚非原材料规格确认”描述的是 11 月 3 日构建时的状态；此次未改动历史输出，也未重建回放。K 因子、展开量及扣除量仍未知。

2026-11-05 工序图辅助建模：按用户授权新增 [图纸对照页](outputs/process_drawing_assistance/P001/review.html) 与 [核对报告](outputs/process_drawing_assistance/P001/REPORT.md)。9 个视图中 8 个通过 4 孔拟合 + 3 孔留出核验；确认 O011 原始展开位置与二维记录一致，最终投影与 STEP 一致，0.8 mm 展开模型残差仍须处理。新增结果明确为专家图纸辅助开发证据，尚未重建三维中间实体。

本轮将“动作对应哪里”推进到“局部材料如何随分配移动”。尚未完成全零件、全部动作的真实成形模拟或干涉判定。

## 可直接查看

页面：`outputs/material_state_v01/P001/material_review.html`。直接打开即可，无需服务或网络。

页面默认展示一份局部测试分配：O010 → OP40，O009 → OP50。可在 OP40 播放 O010，再切到 OP50 播放 O009，观察末端 O011/O012 的位置随两层材料一起移动。

也可导入含 `assignments` 的完整方案 JSON。程序只提取并计算当前示例的两道折弯；没有把其余动作当成已模拟或已检查。默认测试分配由 BEND capability 构造，不读取专家工序。

## 完成的几何工作

| 项目 | 实际结果 | 含义 |
|---|---:|---|
| 板面配对建议 | 64 组 | 平行、间距、投影范围和面积支持的两侧平面，不等于完整实体分区 |
| 基准连接树 | 60 组板面、59 个连接 | 来自圆柱过渡面与实际 CAD 邻接 |
| 未接入当前连接树 | 4 组 | 没有受支持的圆柱铰链连接，不代表材料真实断开 |
| 连接树层级 | 0 层 1、1 层 18、2 层 35、3 层 6 | 材料位姿传递层级，不自动生成制造先后规则 |
| 几何厚度估计 | 0.8 mm | 4 组较大平面支持；尚非原材料规格确认 |
| 折弯对应建议 | 59/59 | 互为唯一最佳的几何候选，尚未通过精确展开验证 |
| 独立 CAD 树核对 | 41/41 一致 | 在几何关联完成后比较，没有反馈到匹配选择 |

小板面已纳入。识别设置在 `configs/material_state_v01.json`；将面积筛选值从 1 mm² 改为 8 mm²，当前得到的板面对与连接集合不变，此项已加入回归检查。

同一折弯允许对应多段圆柱过渡面。轴向范围由全部片段共同提取，并保存每段区间；没有填补片段之间的材料空隙。

整个几何提取过程不读取 Sheet5、专家分配或动作 CAD 父子关系。CAD 父子关系只在关联完成后用于独立核对，以及检查局部输入是否符合已有 H7；材料树本身不产生新工艺硬约束。

## 实际局部状态与几何差异

示例使用真实 CAD 板面表皮，建立可逆的中面交线铰链模型。它把折弯带简化为零宽连接，未描述弯曲过渡区、回弹或塑性变形。

附属位置建议 O011/O012 的状态：

| 状态 | X / Y / Z，mm |
|---|---|
| 计算用展开参考 | 354.8 / 1.1 / 0.0 |
| OP40 结束，O010 已完成 | 354.8 / -3.7 / -5.6 |
| OP50 结束，O009 已完成 | 354.8 / -8.9 / 10.0 |

这些坐标用于验证父区域如何带动附属特征。O011/O012 自己的预孔、翻孔事件尚未求解；表面上沿用了成品孔的几何。

二维对应残差明确保留：

- O009 折弯线：约 0.2 mm。
- O010 折弯线：约 0.6 mm。
- O011/O012 位置建议：约 0.8 mm。
- 更深层的 6 个折弯，当前端点残差约 0.801329 mm。

在配置的 0.08 mm 几何定量匹配容差下，59 个折弯当前通过数为 0。这个容差只用于几何匹配，不是加工安全间隙或新的工艺约束。因此“找到对应建议”不能替换为“精确对应已完成”。

O011 的二维记录为两个圆环半径 1.375/1.9 mm；相应位置 CAD 观察到 1.2/1.6/2.0 mm 的圆边。尚需核对它们各自表示孔壁、外缘还是工艺参考尺寸，当前不认定原始数据错误，也不修改数据使其吻合。

把模型回放到成品位置，属于可逆构造的闭合检查；它本身不构成制造过程正确性的独立证明。

## 状态求解与交互

- 所有关节先按相同工序及过程参数求出进度，再通过材料连接组合位姿。同序动作不按编号串行执行。
- 可以保持不同关节具有不同进度，基础内核不要求统一转速。页面当前采用明确标注的线性角度示意。
- 程序区分动作身份与材料区域。翻孔、预孔依然是两个动作，可以共用同一最终特征位置。
- 仅移动所选板面表皮。没有把跨越多个结构的共享侧面整面随动；厚度侧边和过渡带尚未分区。
- 三维显示使用深度缓冲，提供旋转、平移、缩放和成品轮廓参照；二维对照保留灰色原始记录与彩色计算参考。
- 分配导入支持对象和逐行动作分配；局部能力、Station 和已有先后不满足时，拒绝导入并保留原值。不是完整 H1–H7 检查入口。

## 验证

- Python 共 32 项通过：此前 16 项独立输入/状态基础测试，以及本轮 16 项材料与局部状态测试。
- 本轮 JS 运动内核 13 项检查通过；此前关联核心 12 条断言复核通过。
- 实际 Chrome 中 10 组检查通过、页面错误 0。包括工序切换、附属位置、播放暂停、分配导入、错误分配拒绝、相机及布局。
- 已用旧候选池中的一份完整候选作为离线导入样例，验证仅提取局部动作分配；没有生成新候选、读取评分参与运动或更改旧候选。
- 重新核对 43,879 个项目冻结文件，无内容变化或模块外新增，原 STEP/Parasolid 哈希不变。
- 页面预览为 `01_flat_reference.png`、`02_after_OP40.png`、`03_after_OP50.png`。

## 新增文件

实现：

- `three_d/material.py`
- `three_d/local_motion.py`
- `three_d/web/motion_core.js`
- `three_d/web/motion_renderer.js`
- `three_d/web/material_review.html`
- `three_d/web/material_review.js`
- `configs/material_state_v01.json`
- `scripts/build_material_state_review.py`
- `tests/test_material_state.py`
- `tests/test_material_motion.cjs`
- `tests/test_material_browser.mjs`
- 本记录。

更新：模块 README、三维执行计划和首批执行记录的进度链接。

生成物均位于 `outputs/material_state_v01/P001/`：

- `material_proposals.json`：平面配对、厚度及实际邻接依据。
- `kinematic_proposals.json`：基准连接树、局部坐标和有向转角。
- `bend_associations.json`：59 个未确认对应及其展开差值。
- `semantic_crosscheck.json`：41 条独立语义核对。
- `local_example.json`、`local_schedule_example.json`：局部案例与测试分配状态。
- `summary.json`、`viewer_data.json`、`REPORT.md`：摘要、页面数据和构建说明。
- `material_review.html`：离线页面。
- `python_qa.json`、`python_tests.log`、`browser_qa.json`、`motion_core_tests.log`：验证证据。

路径均相对于 `information_extract/`。

## 下一步边界

优先依据真实折弯过渡几何和二维记录，研究展开补偿及有限宽度过渡带，再通过留出特征验证几何映射。不能只加平移把图形强行对齐，也不能用调大容差代替解决差异。

随后才能扩大实际工序状态覆盖。初始毛坯与切孔状态、翻孔/拉包等局部成形、实际轨迹和工具尚未确定，当前不进入三维候选筛选或质量评分。

## 复现

```powershell
.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_material_state_review.py
.venv-m3/Scripts/python.exe -B -X utf8 -m unittest information_extract.tests.test_three_d information_extract.tests.test_material_state -v
node information_extract/tests/test_material_motion.cjs
node information_extract/tests/test_material_browser.mjs
```
