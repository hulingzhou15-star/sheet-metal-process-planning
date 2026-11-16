# 候选生成、三维反馈与回放

适用于 P001–P004 的新候选批次。工艺语义和输入边界见 [项目规范](../PROJECT_SPEC.md)，当前完成情况见 [进度](../CURRENT_STATUS.md) 和 [交接](../HANDOFF.md)。每个批次应能从完整分配追溯到逐序材料、检测记录和回放采样。

## 工作流程

| 环节 | 执行口径 | 记录 |
| --- | --- | --- |
| 事实和材料域 | 从本件的有效业务事实读取动作、工序能力和严格先后。核对 DXF 对应、STEP 材料域、折弯转轴和端态。用相容的材料域编译几何 P1。 | 活动动作和材料域清单、来源与实现指纹 |
| 初始候选 | 按 H1–H5、H7 求合法的完整分配。确立现行 P5 最优值，再按次级评分和多样性生成初始候选。以分配哈希识别方案，默认目标为 64 个独立分配。 | 初始池、评分、求解状态和先后核对 |
| 逐序运动 | 按实际分配生成进入、运动和完成态。按实际使用的材料和相对位姿对逐序运动做 24 等分筛查，已验证等价的状态可复用。报告写明检测对象、参照模型和采样分母。 | 逐序材料、采样计划、区域对检测及几何未决 |
| 干涉反馈 | 有可重现的正体积交叠时，将见证支持的分配组合反馈求解器。继续生成并复核新方案，直到达到目标、求解空间耗尽或预算停止。 | 可追溯的交叠见证、排除的完整分配或等价路径、停止原因 |
| 补全与选择 | 核对通过路径的每个完整方案的材料事件。在通过方案中按现行评分和路径多样性选代表，目标 16 个。对入选方案以 96 等分复核整条逐序路径。出现交叠或未决时继续处理实际证据和候选池。 | 通过池、入选编号和排名、完整复核报告及不足额原因 |
| 发布 | 由本轮入选报告生成回放数据，逐项核对方案、工序、进度、材料和检测状态。浏览器验证主要操作后接入综合页。 | 独立回放目录、资产与来源指纹、浏览器验证结果 |

H7 始终按 `Station.order` 严格子动作先于父动作。现行新流程启用 H1–H5、H7，停用 H6。切边退出新规划和建模。旧批次有遗留固定切边时，执行投影与新候选重新评分分别留档。新批次以有效动作事实重新计算 P5 和次级分数。评分沿用现行规则和权重，材料树的带动体积用于几何 P1，工艺先后仍由已确认的动作关系决定。[现行几何 P1](GEOMETRIC_P1_ADOPTION_20261113.md)

方案不足额时发布实际通过数、不同运动数以及截至何处的证据。64 指完整分配，16 是入选目标，另记录其中实际不同的同步运动数。同一运动可有多个完整方案，优先从不同运动中选代表。空工序可在运动等价比较中归并，但承载去料、成形或参照切换的工序须保留。求解超时、候选预算达到上限和可行域搜尽分别记录。

## 状态、判定与复用

- 同序折弯共用一个进度。运动段使用本序进入态材料，本序去料及局部成形在完成端统一切换。预孔和翻孔分别记为独立事件。确认相同材料事件时才复用对应的状态实体。
- STEP 局部成形采用已对应的完成态，曲线运动使用本件证据支持的近似。两步折到同一关节时记录各步的**有符号增量**和累计角度。多处几何支撑共享一个动作时，核查各支撑的位姿闭合。转轴或材料参照改变时记录对应工序及影响，用同一参照复核原有见证。
- 至少保留每序进入与完成端点，对实际运动的工序采样。24 与 96 是当前默认等分；加密记录所用等分、各序采样数与区域对数。每态的区域对总数应与各判定计数相符，并把缺失覆盖与几何求交失败写明。
- 保守包围盒可判定分离。若同一坐标系下包含两实体的包围盒交集体积不超过阈值，可记录该上界并跳过实体求交。实体求交确认正体积超过配置阈值时，保存工序、进度、相关区域、交叠体积及定位信息。零正体积可按检查能力保留接触和分离细分，或统一记录无正体积交叠。项目现有实体筛查通常使用 `1e-6 mm³` 体积阈值，以实际批次配置为准。[实体求交](three_d/p004_collision.py) · [P002 局部上界](three_d/p002_regeneration.py)
- 网格穿越、接触或包含提示按其证据强度呈现。需要排除候选时对提示做实体复核，或记录仍待复核的原因。无正体积的接触、缺少材料或位姿、内核失败以及证据不足的近似转轴交叠都需要各自的记录。缺少覆盖的状态不得计为已通过。已确认碰撞反馈只约束本轮同模型的候选搜索，超出见证支持的组合须由独立证据核对。
- 用于缓存或反馈复用的身份至少区分零件、输入及实现指纹、轴位、材料版本、活动动作和采样配置。在能证明所比较材料和相对位姿等价时，才可跨方案缓存结果或排除同一路径。若去料或成形与折弯同序，须验证进入态和完成端分别使用正确的材料；无法证明等价时按完整分配重新评估。

报告注明范围是已建模材料和离散运动状态。连续局部成形、模具及装夹、完整折弯带检测按实际覆盖写出。此类筛查证据与真实监督资料保持独立。

## 各零件的应用要点

| 零件 | 本件资料与处理差异 |
| --- | --- |
| P001 | 权威事实来自人工修正工作簿。早期生成批次含 10 个固定切边记录，历史运动检查只做有效动作的执行投影。重生成移除 `O215–O224` 后以 214 项活动动作编译几何 P1，重新求 P5 基准和次级分数。已有逐序去料、翻孔、折桥和 STEP 局部端态提供重建依据。反馈使用完整分配，同一折弯分配中的去料或成形时机可能改变材料状态。刚性区域检测与有限宽带显示的覆盖分别记录。[P001 先前批次](GENERATED_ALLOCATION_CHECK_20261113.md) |
| P002 | 保留 O136 的两个同步铰链、四处曲线的截面扫掠近似及 O094 随动。现有回填模型支持 O112 早于 O069、O037、O017，O115 早于 O086 的去料顺序。超出适用范围记为材料证据未决，保留正式求解规则。刚性区使用实体求交，曲线区先检查网格，对穿越或包含线索复核采样闭合实体。只有确认的正体积交叠用于干涉反馈，按完整分配排除。网格提示和实体复核分别落档。[P002 逐序模型](P002_CURVE_AND_STAGE_MODELS_20261116.md) |
| P003 | J297 先由 O069 转到 +135°，再由 O120 反转 45°，累计至 90°。已发布批次在前段使用原展开配准、完成材料后使用经复核的 J297 圆柱轴。已确认的 41 组历史碰撞折弯分配进入本轮反馈。[P003 复核与重生成](P003_STAGE_MOTION_20261116.md) |
| P004 | OP40 运动使用凸米成形前材料，完成端切换四处凸米。O013 按 STEP 的 10° 转角驱动四处共轴支撑，O014 的多处支撑核对闭合。已发布批次使用板中面轴，首例有 STEP 圆柱轴对照。39 种折弯分配通过两档检查。[P004 复核与重生成](P004_STAGE_MOTION_20261116.md) |

## 回放与成果核验

统一入口从同一本地页面切换 P001–P004。各零件回放提供方案与排名、工序和采样切换、当前状态、二维／三维联动，以及对应数据支持的 STEP 对照和特征定位。播放在 1×、2×、4× 档保留工序端点。独立窗口、从工序汇总跳转、材料或几何参照切换、报告与导出链接随各自回放验证。没有完整检测数据的历史页面按实际范围显示，回放中存在的几何外形和交互能力不增加检测覆盖。[四件综合回放](http://127.0.0.1:8770/part_motion_review.html)

沿用按需加载、共享几何缓存和仅更新变化视图的做法。以原报告逐个核对发布采样的方案、工序、材料和判定，浏览器验证在地址切换时只加载当前零件。发布资产、材料和报告放在本轮输出目录，由 [受保护输入清单](../registry/protected_inputs.json) 复验冻结基线。复现日志记录执行过的测试和已知差异。

## 复现

从仓库根目录使用项目虚拟环境。构建前确认依赖产物齐全。各批次的已执行核验和阶段结果见 [当前进度](../CURRENT_STATUS.md) 和 [交接](../HANDOFF.md)。

P001 从 `outputs/generated_allocation_collision_v01/P001` 读取展开材料域、加工端态及参照模型，再以有效事实重新生成。`--generate-only` 可单独复现初始池。默认最多筛查 128 个完整分配，可用 `--max-plans` 调整并记录预算。展示构建器默认生成独立的历史回放。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.regenerate_p001_candidates
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.build_p001_stage_review --regenerated
node information_extract/tests/test_p001_stage_review_browser.mjs --regenerated
```

P002 从已核对的活动工艺事实和分区材料读取输入。初筛以 24 等分检查每个完整方案，从通过池选代表并逐一执行 96 等分复核。材料未决和加密复核失败的方案从通过池移除，按完整分配补生。默认执行至多三个生成与检测批次，可用 `--rounds` 调整。补足命令复用已校验的通过池和逐序报告，定向搜索落在现有材料模型适用范围内的完整分配，按同步折弯分组选代表并加密复核需要补查的入选方案。`--pilot 1 --rounds 1` 写入单独试跑目录，应按实际覆盖报告。发布命令生成筛查总览，有入选方案时同时生成双视图回放。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.regenerate_p002_candidates
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.supplement_p002_candidates
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.publish_p002_regenerated_review
node information_extract/tests/test_p002_regenerated_review_browser.mjs
```

P003 已发布的筛查与回放可从现有材料模型、初始候选和已复核的旧池干涉记录运行。原始前置产物的复现见 [P003 模块报告](P003_STAGE_MOTION_20261116.md)。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.regenerate_p003_candidates
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.build_p003_regenerated_review
node information_extract/tests/test_p003_regeneration_replay_browser.mjs
```

P004 可从本件资料重新生成。参照和材料重建细节见 [P004 模块报告](P004_STAGE_MOTION_20261116.md)。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.generate_p004_allocation --prepare-only
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.build_p004_material_basis
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.generate_p004_allocation
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.regenerate_p004_candidates
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.build_p004_stage_review
node information_extract/tests/test_p004_stage_review_browser.mjs
```

在本地打开综合回放时，以输出目录启动服务。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m http.server 8770 --bind 127.0.0.1 --directory information_extract/outputs
```
