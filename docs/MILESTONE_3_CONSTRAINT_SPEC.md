# Milestone 3 v0.1：ConstraintContext 与 Plan 约束

本轮实现依据 `PROJECT_SPEC.md`、冻结的 M1/M2 和本轮确认的 M3 定义。
决策单位始终为 **Operation → Station**。M1 数据语义、M2 Graph0/1/2、Sheet1–Sheet5 和 expert reference plan 均保持不变。

## 四种职责

| 层 | 实现 | 作用 |
| --- | --- | --- |
| Hard Constraints | `HardConstraintEngine` | H1–H6 决定 Plan feasibility |
| Soft Priors | `SoftPriorEngine` | P1–P3 为未来 candidate generation 提供启发式偏好 |
| Diagnostics | `DiagnosticEngine` | D1–D3 只观察，不参与 SoftPenalty |
| Generator Guardrail | `StationOverloadGuardrail` | G1 为未来生成器标记极端负载，不表达质量 |

`SoftPenalty(P)` 不是真实工艺质量，不是 GNN label，也不是 preference label。
`SoftPenalty(P1) < SoftPenalty(P2)` 不能直接解释为 P1 的工艺质量更好。
本轮没有正式 CandidatePlanGenerator、批量 Plan 生成、自动调权、优化 expert penalty、GNN 或训练标签。

## 文件与接口

新增源文件如下；没有修改已有文件：

```text
docs/MILESTONE_3_CONSTRAINT_SPEC.md
configs/m3_constraints.yaml
src/constraints/__init__.py
src/constraints/config.py
src/constraints/context.py
src/constraints/hard_constraints.py
src/constraints/soft_priors.py
src/constraints/diagnostics.py
src/constraints/region_assignment.py
src/constraints/guardrails.py
scripts/evaluate_plan_constraints.py
tests/m3_fixtures.py
tests/test_m3_constraint_context.py
tests/test_m3_hard_constraints.py
tests/test_m3_soft_priors.py
tests/test_m3_diagnostics.py
tests/test_m3_guardrails.py
```

```python
from src.constraints import (
    load_constraint_context, HardConstraintEngine, SoftPriorEngine,
    DiagnosticEngine, StationOverloadGuardrail, load_config,
)

context = load_constraint_context("data/raw/钣金加工数据表_P001.xlsx")
plan = context.expert_reference_plan
hard = HardConstraintEngine(context).evaluate(plan)
if hard.feasible:
    config = load_config()
    soft = SoftPriorEngine(context, config).evaluate(plan)
    diagnostics = DiagnosticEngine(context, config).evaluate(plan)
    guardrail = StationOverloadGuardrail(context, config).evaluate(plan)
```

Plan 接受 M1 `ExpertReferencePlan`、`{operation_id: station_id}` mapping 或 `(operation_id, station_id)` 序列。
序列保留重复项以检查 H1。mapping 值必须是单个 Station ID，不能用一个列表表达多个分配。
soft/diagnostic/guardrail 对非法 Plan 拒绝分析；G1 violation 不会影响 hard feasibility。

## ConstraintContext 与输入校验

`ConstraintContext` 组合冻结的 `PartData`、`FixedAssignment`、`BendInfluence`。
Operation、Station、Station.order/capability、required predecessor、process group、Geometry 和 expert plan 均复用 M1 对象。
Sheet4/5 只进入 M3 metadata，**不写入 StaticPartGraph，不产生新 graph edge**。

Sheet4 原始列：`operation_id, fixed_station`。模型将 `fixed_station` 组织为 `FixedAssignment.station_id`，保留原始 Sheet/row。
Sheet5 完整保存：

```text
bend_operation_id
start_x/start_y/end_x/end_y
bend_axis/far_boundary_coord
affected_depth/affected_side
bend_length/affected_area_proxy
influence_region_id/parent_region_id
```

只读 Loader 先调用冻结的 M1 loader，再以工作簿 byte snapshot 读取 Sheet4/5，并检查两次快照 SHA-256 一致。
不保存、不重算、不修改工作簿；公式必须已有有效 cache，Excel error/缺失 cache 直接报告。

校验 Sheet4 Operation/Station 真实存在，fixed TRIM 分配可唯一确定；Sheet5 Operation 为 BEND，region ID 唯一，parent 真实存在且能确定 BEND，hierarchy 无环。
空白 parent 表示 root。输入数值必须有限，depth/length/area 不为负，不修正或重新推导已有值。
当前每个 BEND 对应一个 region；若同一个 BEND 出现多个 region 记录，则 per-BEND prior 的输入不再唯一，报告 metadata ambiguity，不自行选值。
没有提供 Sheet5 metadata 的 BEND 会列在 pair audit 的 `missing_bend_metadata_ids` 中，不从 Geometry 补造影响区域。
CLI 同时运行冻结的 M1 Validator；其错误会停止分析。

## H1–H6

| Constraint | 定义 | 来源 |
| --- | --- | --- |
| H1 | 每个当前零件 Operation 恰好分配一次；不允许缺失、重复、额外 Operation 或不存在的 Station | Plan 结构 |
| H2 | `operation_type ∈ station.allowed_operation_types` | Sheet3 capability，不由 expert 推断 |
| H3 | 每个显式 `A ∈ B.required_predecessor_ids` 满足 `order(P(A)) < order(P(B))` | Sheet1 |
| H4 | 同一 process_group 内每个 PRE_PUNCH 严格早于 FLANGING | Sheet1 process_group |
| H5 | Sheet4 中每个 TRIM 保持指定 fixed Station | Sheet4 |
| H6 | `R_child.parent_region_id=R_parent` 时，child BEND 严格早于 parent BEND | Sheet5 人工确认 parent |

先后判断只用 `Station.order`，不比较或解析 OP 名称。不把 CAD parent、距离、area 或 geometry containment 转成 hard precedence。
H6 只建立已记录的直接 parent relation；传递先后由这些直接不等式自然保证。

结果为 `feasible: bool` 及 violation 列表。每项包含：
`constraint_type, operation_ids, station_ids, message, context`。
无效/重复分配的其它关系检查只在其端点可唯一解析时进行，H1 本身已经保证非法 Plan 不可行。

**必须先运行 expert reference plan。** 如果 expert 违反 H1–H6，CLI 输出冲突并停止后续 soft、diagnostic 和 G1 结论；不改源数据、不放宽规则、不增加 exception。
测试已验证：即使传入另一个可行 Plan，expert gate 失败时仍停止。

## Soft Priors：仅 P1–P3

令三个 component 为 `c_area, c_extra_gap, c_type_fragmentation ∈ [0,1]`：

```text
SoftPenalty(P) = 0.45*c_area + 0.20*c_extra_gap + 0.35*c_type_fragmentation
```

权重由配置读取，必须有限、非负且和为 1.0，不自动归一化。
P1–P3 只对已通过 hard feasibility 的 Plan 计算。返回 raw component、weight、weighted contribution、完整 audit 和 total。

### P1：Bend affected-area ordering

对具有 Sheet5 metadata 的无序 BEND pair `(i,j)`：

1. 如果 region 为直接或间接 ancestor/descendant，排除。
2. 只有 `area_i > ratio*area_j` 或反向满足时才参与，当前 `ratio=1.15`。
3. 面积显著较大的 BEND 位于严格更早的 Station 时记一次 inversion；同 Station 不记。

```text
c_area = inversion_count / comparable_pair_count
comparable_pair_count=0 时 c_area=0
```

同 Station 的显著 pair 仍属于 comparable 分母。显著性是严格不等式，正面积采用等价比例比较，避免 `1.15*100` 浮点乘法把 115 误判为显著。
零面积与正面积比较也被支持，不增加影响范围或新的 precedence。
输出全部 comparable / excluded hierarchy / insignificant / inversion pair 以及面积、Station 和 order。

### P2：Precedence extra-gap

硬前置来源只有 H3 `EXPLICIT_PREDECESSOR`、H4 `PROCESS_GROUP`、H6 `BEND_REGION_PRECEDENCE`。
不使用 graph 的 reverse message edge、CAD parent、expert median 或从 expert 学到的 gap。

所有 Station 按 `Station.order` 排序后赋连续 rank，包含空 capability Station。
rank 从 0 开始，差值与从 1 开始相同；不直接用原始 order 数值差。

对每条 `A → B`，分别根据两个 Operation 的 capability 和 Sheet4 fixed assignment 获得合法 Station 集。
只取 `rank(B)>rank(A)` 的局部 Station 组合：

```text
g_min = min(rank(B)-rank(A))
g_max = max(rank(B)-rank(A))
g_actual = rank(P(B))-rank(P(A))
p_e = 0                                  if g_max == g_min
p_e = (g_actual-g_min)/(g_max-g_min)       otherwise
c_extra_gap = mean(p_e over valid relations)
```

没有 relation 时 component=0。`g_min==g_max` 的有效 relation 以零 penalty 参与平均。
局部界限不加入第三个 Operation 的全局可行性推断。
输出每条 relation 的来源、合法 Station 集、fixed assignment、g_min/g_actual/g_max、p_e 和 `p_e/relation_count` component contribution；另给各来源 gap distribution 与贡献。

这一较弱 heuristic 表达满足必要顺序后的额外间隔，不意味着相邻 Station 必然更好。

当前 P001 不存在相同 directed pair 的多来源重叠。
若其它输入中同一 directed pair 重复，按来源记录平均还是按 pair 合并尚未确认，**P2 报告定义歧义并停止，不自行重复计权或去重**。

### P3：Selected OperationType fragmentation

当前只启用 `FLANGING`，不指定具体 Station。

```text
K_t(P) = Plan 中类型 t 实际使用的不同 Station 数
K_allowed_t = capability 允许类型 t 的 Station 数
p_t = (K_t(P)-1)/(K_allowed_t-1)    if K_allowed_t > 1
c_type_fragmentation = mean(p_t over participating enabled types)
```

`K_allowed_t<=1` 的类型不参与；没有参与类型时 component=0。
分母完全来自 capability，不按 expert 或 fixed assignment 缩小。
同类型集中一个合法 Station 时为 0，使用全部合法 Station 时为 1。
输出 enabled type、Operation count、allowed/used Stations、K_allowed、K_actual、是否参与及 penalty。

只启用 FLANGING 是本轮确认的同类资源偏好假设。没有对其它 OperationType 的独立依据，因此不自动扩展。
若未来 enabled type 没有任何 Operation 且允许多个 Station，原公式会出现负值，当前实现报告定义未覆盖，不替用户补一种平均规则。

## 已取消或暂不计分的想法

### S1 bend-related irregular punch 已取消

冻结的 IRREGULAR_PUNCH 没有可靠的 bend-related subtype，也没有一个已经确认且可执行的独立 soft 定义。
不能用二维邻近、CAD parent 或 expert Station 自动推断其 bend-related 身份。
已经人工确认的冲孔—折弯依赖由 H3 表达；除此之外没有证据支持额外计权。
因此本版完全不计算 S1，不修改 OperationType，不借 D3、距离或 Graph2 增加相关关系。

### affected_depth 当前只是 Diagnostic

`affected_area_proxy = bend_length * affected_depth`。P1 已使用面积；再给 depth 独立权重会重复强调其中一个因子。
当前没有证据证明独立 depth prior 提供额外价值，所以 D1 weight=0，完全不进入 soft 权重集合。

### region hierarchy 不重复作为 soft prior

人工确认的 hierarchy 先后已经由 H6 强制表达。
P1 与 D1 都排除直接和传递 ancestor/descendant pair；没有第四个 hierarchy soft component。
这样不会用 soft score 再次奖励必要 hard 顺序。

### local region compactness 当前只诊断

二维展开代表点的 containment 只是几何观察，没有证据证明 region 内 Station 集中度就是工艺质量。
因此 D3 不产生 penalty、predecessor、graph edge 或 OperationType 更改。

### station overload 是 guardrail

expert 的 Operation 数量不能证明 Station 的实际生产 capacity，也不能反映不同动作的真实工作量。
G1 只为未来候选生成提供很宽松的退化过滤阈值，不参与质量评分或 hard feasibility。

## D1–D3

### D1：affected-depth ordering

使用与 P1 相同 pair 方法及层级排除，改为 `affected_depth`，当前严格显著性比率 1.15。
输出 comparable、excluded、inversion、rate 和逐 pair 记录；weight=0。

### D2：hierarchy / Station distribution

输出 root/leaf count、最大 depth、每个 level 的 BEND 数和当前 Plan Station 分布、parent-child rank gap distribution、完整 forest、是否有多层 chain。
expert 报告中的当前 Plan 即 expert plan。
root level=0，depth 定义为 parent 链接数；最大 depth=2 表示 3 个 level。
不产生 score。

### D3：local influence-region operation distribution

仅复用 M2 `GeometryFeatureExtractorV1` 的代表中心定义：圆心、BEND 中点、irregular bbox 中点、BRIDGE 已记录 center。
不发明质心、几何采样或新的 region 规则。
只用 Sheet5 已记录的水平/竖直 bend line 和 far boundary 构成矩形：

```text
VERTICAL: x 为 bend_x 与 far_boundary_coord 之间；y 为两个端点之间
HORIZONTAL: x 为两个端点之间；y 为 bend_y 与 far_boundary_coord 之间
```

要求记录的 axis、端点、affected_side 与 boundary 一致；无法可靠构矩形则输出原因。
矩形使用闭集 containment（含边界）。实际比较在与 M2 相同的归一化坐标中进行，避免反变换浮点舍入让边界点错误落出 region。
不添加距离公差。审计坐标再反变换回材料坐标。

每个有效代表点最多分配到一个 region，选择实际 rectangle 面积最小者。
无 Geometry 或不在任何矩形的 Operation 忽略，但计数公开。
若最小 containing area 并列，报告歧义并保留未分配；不按 region ID、expert 或 hierarchy 猜测一个答案。
Geometry 无法可靠提取则报告 unavailable/partial，不生成新数据。

输出每个 region 的 Operation ID/数量、类型分布、当前 Plan 使用的 Station 数和分布；还给有效 region/代表点、无 geometry、outside、归区数量及并列情况。
P001 当前无 equal-smallest 歧义或无效矩形。

## G1：Extreme Station Overload Guardrail

expert load 为 `n*_j`：

```text
U_j = max(n*_j + absolute_slack, ceil(relative_limit*n*_j))
当前 relative_limit=1.75，absolute_slack=5
guardrail_violation = enabled and any(n_j(P)>U_j)
```

包括 expert load=0 的 Station，当前上限仍为 5。
阈值等号不违反；disabled 时仍返回阈值统计，但不标记 violation。
expert 必须先满足 hard feasibility；不修改 expert，不把 U_j 当作真实 capacity。

## 配置入口

唯一数值参数修改入口为 `configs/m3_constraints.yaml`：

| 路径 | 当前值 |
| --- | --- |
| soft_weights.bend_area_ordering | 0.45 |
| soft_weights.precedence_extra_gap | 0.20 |
| soft_weights.type_fragmentation | 0.35 |
| bend_area_ordering.significance_ratio | 1.15 |
| bend_area_ordering.exclude_ancestor_descendant_pairs | true |
| depth_diagnostic.significance_ratio | 1.15 |
| depth_diagnostic.exclude_ancestor_descendant_pairs | true |
| type_fragmentation.enabled_types | [FLANGING] |
| station_overload_guardrail.enabled | true |
| station_overload_guardrail.relative_limit | 1.75 |
| station_overload_guardrail.absolute_slack | 5 |

文件采用 **YAML 合法的 JSON 子集**，通过标准库 json 读取，以保持原有 openpyxl-only 依赖不变。
修改时保留花括号、双引号和 JSON true/false，不切换为 YAML 缩进语法；CLI 可用 `--config` 指定另一份同格式配置。
两个 hierarchy exclusion 当前必须为 true，关闭会违背本版定义，配置校验会拒绝。
数学定义中的 0/1、H1–H6 编号和固定的 Diagnostic 不计分语义不是可调 heuristic 参数。

## CLI 与审计输出

在项目根目录执行：

```text
python -B scripts/evaluate_plan_constraints.py --plan expert
python -B -m unittest discover -s tests -v
```

可选参数：`--input`、`--config`、`--output`；`--plan` 还可指向一份已给定 JSON Plan，不能生成 Plan。
JSON mapping 的重复 key 被保留为 assignment 序列并交 H1 检查，不悄悄覆盖重复项。

默认输出：

```text
outputs/m3_diagnostics/expert_plan_constraint_report.json
outputs/m3_diagnostics/expert_plan_constraint_report.md
```

JSON 保留所有 pair、全部 metadata/source row、关系局部界限与分项统计。
Markdown 显示分项及 weighted contribution、全部 inversion/excluded pair、全部 P2 relation、P3、D1 inversion、forest、每个 D3 region、G1 和参数快照。
所有报告只能写入项目 `outputs/`，拒绝 raw 路径和指向受保护输入的 alias。
报告保存工作簿读取前/后 SHA-256、M3 snapshot SHA-256、配置 hash 和冻结 M1 校验统计。

## P001 验证结果

expert **满足 H1–H6**，每项 violation=0。224 个 Operation，7 个 Station，10 个 fixed TRIM，59 个 BEND region。

| Prior | Raw component | Weight | Contribution |
| --- | --- | --- | --- |
| P1 area | 48/1460 = 0.032876712329 | 0.45 | 0.014794520548 |
| P2 extra gap | 0.130952380952 | 0.20 | 0.026190476190 |
| P3 fragmentation | 0 | 0.35 | 0 |
| Total | | | 0.040984996738 |

P1：1711 个无序 pair，38 个 hierarchy pair 被排除（32 直接、6 传递），213 个不显著 pair，1460 comparable，48 inversion。

| P2 source | Count | g_min | g_actual distribution | g_max | Mean p_e | c_extra_gap contribution |
| --- | --- | --- | --- | --- | --- | --- |
| EXPLICIT_PREDECESSOR | 34 | 全部 1 | 1:6, 2:12, 3:16 | 全部 4 | 0.431372549020 | 0.130952380952 |
| PROCESS_GROUP | 46 | 全部 2 | 2:46 | 全部 2 | 0 | 0 |
| BEND_REGION_PRECEDENCE | 32 | 全部 1 | 1:32 | 全部 2 | 0 | 0 |

共 112 个有效 relation。FLANGING 有 46 个 Operation，capability 只允许 OP30，实际也只用 OP30，K_allowed=K_actual=1，因此不参与 P3，component=0。
这来自 capability 的定义，不是从 expert 学到 OP30 偏好。

D1：1354 comparable，38 hierarchy excluded，1 inversion，rate=0.000738552437；inversion 为 O020（OP40）→ O137（OP50）的 depth 排序观察，不影响 penalty。

D2：27 roots、36 leaves、最大 depth=2（3 levels），有多层 chain；全部 32 parent-child gap=1。

| Region level | BEND count | Expert Station distribution |
| --- | --- | --- |
| 0 | 27 | OP50:25, OP60:2 |
| 1 | 26 | OP40:15, OP50:11 |
| 2 | 6 | OP40:6 |

D3：59 个有效且非空 region，211 个有效代表点，186 个 Operation 唯一归区，25 个在全部 region 外，13 个无 Geometry；没有无效 region 或最小面积并列。

| Station | Expert load | G1 upper bound |
| --- | --- | --- |
| OP10 | 53 | 93 |
| OP20 | 45 | 79 |
| OP30 | 63 | 111 |
| OP40 | 25 | 44 |
| OP50 | 36 | 63 |
| OP60 | 2 | 7 |
| OP70 | 0 | 5 |

expert 没有 G1 violation。

## 测试与冻结检查

新增 73 项 M3 测试，覆盖本轮要求的 28 项案例以及额外输入/审计边界；原有 M1/M2 共 107 项。
全量 **180 tests，0 failures，0 errors，0 skipped**。

| 模块 | Count | 主要覆盖 |
| --- | --- | --- |
| test_m3_constraint_context | 12 | Sheet4/5 引用、region/BEND 唯一性、cycle、数值、只读快照、静态图隔离 |
| test_m3_hard_constraints | 14 | expert H1–H6、缺失/重复/多分配/invalid Station/capability/各前置/fixed/parent、结构化 violation、禁止推断额外 hard |
| test_m3_soft_priors | 26 | P1 inversion/10%/严格15%/>15%/same Station/直接和传递层级排除；P2 min/max/intermediate/fixed/capability/等界；P3集中/全部分散/非enabled/多类型；范围/权重/feasibility gate |
| test_m3_diagnostics | 14 | D1/D2；D3最小包含/无geometry/outside/边界/并列/不可用；Diagnostic隔离；expert gate停止；报告完整审计与路径保护 |
| test_m3_guardrails | 7 | ceil/slack/空Station/严格超限/disabled/配置化/不影响hard与soft/P001阈值 |

人工构造测试使用内存 dataclass，未通过改原始数据让测试通过。
全量日志：`outputs/m3_diagnostics/test_results.txt`；逐 test ID 与模块计数：`test_results.json`。
冻结清单 `frozen_input_manifest.json` 记录本轮开始时 28 个既有文件的 SHA-256；`frozen_integrity_check.json` 记录结束时逐文件一致性。

## 数据与定义问题、下一阶段条件

P001 没有发现 H1–H6 数据冲突、缺失 BEND metadata、region cycle、无效 rectangle 或 D3 equal-smallest 歧义。
P3 因 capability 单 Station 而为 0 是定义的正确结果，不能为制造差异而改数据或规则。
当前 P001 已具备下一步 CandidatePlanGenerator 需要的 context、hard feasibility、三个启发式、观察接口及 G1 过滤接口。

尚未覆盖的其它输入定义边界包括：P2 同 pair 多来源如何平均、P3 enabled type 无实例如何平均、D3 最小面积并列如何唯一归区。
这些边界在当前 P001 不出现；实现会明确报告，不推测新工艺规则。
没有证明启发式等于真实质量，也没有解决未来候选多样性、生成搜索策略或专家偏好标注问题。
本轮到约束体系验证为止，等待下一步指令。
