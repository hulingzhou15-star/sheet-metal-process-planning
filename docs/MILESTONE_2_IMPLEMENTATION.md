# Milestone 2 实现与使用

正式语义以 `PROJECT_SPEC.md` 和 `MILESTONE_2_GRAPH_SPEC.md` 为准。本文记录实现接口和数值约定，不增加工艺规则。

## 使用

在项目根目录执行：

```text
python -B -m unittest discover -s tests -v
python -B -m src.graph
python -B -m src.graph --plan outputs/my_plan.json --output outputs/another_plan --k 8
```

`--plan` 文件是完整 JSON 对象，例如 `{"O001": "OP10", ...}`；省略时使用 M1 的 ExpertReferencePlan。命令入口先调用冻结的 Loader 和 Validator，再把 PartData 交给 Graph Builder。所有 Builder 都只接受结构化对象，完全不读取 Excel。

默认生成四份 JSON：`outputs/milestone2/static_part_graph.json`、`plan_overlay.json`、`plan_graphs.json`、`statistics.json`。静态文件与 Plan 文件分离；源 Excel SHA-256 只进入统计报告的 provenance，不进入静态图哈希。额外的冻结汇报、测试日志和文件完整性检查位于同一目录。

也可以直接调用：

```python
from src.graph import build_part_graph, build_plan_overlay, build_baselines

static = build_part_graph(part_data, k=8)
overlay = build_plan_overlay(static, assignments)
graph0, graph1, graph2 = build_baselines(static, overlay)
```

## 对象与边界

- `StaticPartGraph`：part_id、有序 Operation ID、双向 node_index 映射、静态节点特征、逐节点保留的原始 Geometry、归一化上下文、完整 StationContext、语义边、几何边、kNN 诊断。使用 frozen dataclass 和 tuple。没有 expert_station、分配工位或 Plan 边特征。
- `PlanOverlay`：与静态图严格对齐的 Operation ID、StationContext 和节点分配特征；按指定端点动态计算 Plan 边特征。接受 ExpertReferencePlan、Mapping 或 `(operation_id, station_id)` 序列，检查覆盖、重复、额外 Operation 与不存在的 Station。
- `PlanGraph`：共享同一 StaticPartGraph 和 PlanOverlay 的 baseline 视图。模型和导出应通过 `edges` 访问当前 baseline 的边：0 为空，1 为语义边，2 为语义边加几何边。静态原始存储仍保留所有关系，但 Graph 0 的导出不会带入关系。
- 节点 ID 按字符串 operation_id 排序，224 个 Operation 全部保留。IRREGULAR_PUNCH 没有 subtype。Station、Geometry primitive 和 ProcessGroup 均不成为图节点。
- 静态向量为 9 维 OperationType one-hot + 3 维 SurfaceSide one-hot + 14 维 geometry_features_v1，共 26 维。Plan 向量为 relative_order + 9 维 capability，共 10 维。特征顺序写入导出文件。
- PlanOverlay 只做结构检查，接受结构完整但可能不可行的方案；没有把专家方案当成唯一合法 Plan。

## 几何摘要

统一字段按规范顺序：

```text
has_geometry
geom_center_x_norm, geom_center_y_norm
bbox_width_norm, bbox_height_norm
geometry_length_norm, geometry_area_norm, geometry_perimeter_norm
orientation_cos2, orientation_sin2
has_length, has_area, has_perimeter, has_orientation
```

全部有效 Geometry 的 bbox 构成零件 bbox。ARC bbox 包括落在其有符号 sweep 范围内的极值点。代表中心、长度、面积和周长分别按 `(coordinate-part_center)/D`、`length/D`、`area/D²`、`perimeter/D` 归一化，D 是零件 bbox 对角线。D 为 0 时显式记录 degenerate，计算尺度回退到 1，避免 NaN/Inf；只有一个 Station 时 relative_order 和工位差为 0。

| Operation | 代表中心 | characteristic length | 面积/周长 | 方向 |
|---|---|---|---|---|
| CIRCLE_PUNCH / PRE_PUNCH / DIMPLE | 圆心 | 直径 | πr² / 2πr | mask=0 |
| FLANGING | 同心圆圆心 | 外径 | 两项均填 0、mask=0，遵循规范第 12、13 节 | mask=0 |
| BEND | 端点中点 | 线长 | 两项 mask=0 | 无向线 cos2θ / sin2θ |
| BRIDGE | 已记录 center | footprint 对角线 | 矩形面积 / 周长 | 矩形长轴 |
| IRREGULAR_PUNCH | 精确 bbox 中点 | bbox 对角线 | 含圆弧的轮廓积分 / LINE+ARC 长度之和 | mask=0 |
| 无 Geometry | 0 | 0 | 0 | 全部几何 mask=0 |

BRIDGE 使用 M1 已确认的轴对齐矩形。正方形没有唯一长轴，因此方向 mask=0；零长 BEND 同理。没有为复杂轮廓计算 PCA 或严格面积质心。面积采用 Green 公式：每段的端点叉积面积，ARC 再加 `r²(sweep-sin(sweep))/2`，总和取绝对值。有符号 sweep 直接沿用冻结的 M1 协议。

## Station 与关系

Station 按 `order` 排序，不解析字符串数字。relative_order 按 `(order-1)/(M-1)` 计算。能力 multi-hot 固定顺序为 CIRCLE_PUNCH、IRREGULAR_PUNCH、PRE_PUNCH、FLANGING、BEND、TRIM、DIMPLE、BRIDGE、MARKING；空能力全部为 0。M1 允许的非连续正整数 order 不新增限制，公式仍使用实际 order。

语义边只来自显式 predecessor、同组 PRE_PUNCH → FLANGING、组成员关系及 CAD parent。每条边保存 relation_type、relation_source、origin_ref、原始 source_location（若输入具备）以及 reverse_message。重复节点对的不同来源和不同关系不合并。SUCCEEDS / CAD_CHILD 是反向消息表示，不新增业务事实。CAD parent 永不自动转为 PRECEDES。

每条现有边的 Plan 特征均为 same_station、`(target_order-source_order)/(M-1)` 和其绝对值。反向边的工位差改变符号。它们不写入静态边。

## 解析几何距离与 kNN

计算对象为原始二维曲线点集：CIRCLE 是圆周，LINE/BEND_LINE 是有限线段，ARC 是有符号圆弧，IRREGULAR_PUNCH 是轮廓段的并集，FLANGING 是两条记录圆周的并集，BRIDGE 是已定义 footprint 的四条边界线。没有推断填充区域、inside/contains 工艺关系或新增 CAD 信息；同心圆之间的径向边界间隙不会被改成 0。

支持 LINE–LINE、LINE–CIRCLE、LINE–ARC、CIRCLE–CIRCLE、CIRCLE–ARC、ARC–ARC；复合几何取所有 primitive pair 最小值。解析候选来自相交点、端点到曲线距离，以及圆弧法线/径向驻点。覆盖相切、相交、同心、包含但边界分离、受限 sweep、负 sweep、退化线段和零半径。生产实现没有采样；测试中的稠密采样只作为独立误差上界检查。

默认 k=8，几何节点不足时选全部其他几何节点。逐节点选择后取邻居关系的并集，再保存双向 GEOMETRIC_NEAR。距离只计算一次并对称复用；reverse distance 不变、dx/dy 相反。没有 Geometry 的节点不参加 kNN，但仍在全部 baseline 中保留。

并列排序键为 `(round(distance_mm, 9), operation_id)`，即按 10⁻⁹ 原始坐标单位的数值桶稳定排序。此约定仅解决浮点并列，不是制造公差；边特征保存未取整的实际最短距离。诊断记录候选列表内存在并列的节点、第 k/k+1 个邻居并列的节点，以及最终有序邻居名单。该选择固定为 k，不把并列扩大为可变 k。

算法评估全部 `N_geom*(N_geom-1)/2` 个 Geometry pair，时间和距离缓存空间为 O(N_geom²)，适用于当前 P001。没有引入外部几何库或神经网络框架。

## 当前边界和下一阶段注意点

FLANGING 的面积/周长定义仍按规范允许的方式暂缓，不阻碍图表示冻结。13 个缺少 Geometry 的 Operation 是当前规范允许的情况，不要求额外 CAD。`orientation_sin2` 在 P001 恒为 0，是有效方向全部轴对齐的结果，跨零件不能据此删除字段。

PlanOverlay 不验证完整候选方案可行性。本阶段没有实现 GNN、pooling、训练、评分、ranking、CP-SAT、候选生成、UI、数据库或额外工艺关系。下一阶段研究可以使用三份一致的 baseline 输入，但不能把反向消息边当作新约束，或把专家 Plan 当成唯一正确标签。
