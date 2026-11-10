# Milestone 2：方案评价图表示规范

## 0. 文档定位

本文档定义项目 Milestone 2 的第一版图表示设计。

开始工作前必须先阅读：

```text
PROJECT_SPEC.md
docs/MILESTONE_2_GRAPH_SPEC.md
```

`PROJECT_SPEC.md` 仍然是整个项目的问题定义和数据语义规范。

本文档只进一步规定：

- 如何把 Milestone 1 的结构化数据转换成图表示；
- Graph 0 / Graph 1 / Graph 2 的具体边界；
- 静态零件信息和候选 Plan 状态如何分离；
- 第一版二维几何摘要特征；
- 第一版二维几何邻接关系。

如果本文档与 `PROJECT_SPEC.md` 或 Milestone 1 已冻结的数据语义发生冲突：

> 停止相关实现并报告，不要自行修改原始数据或改变项目语义。

---

# 1. Milestone 2 的目标

Milestone 2 的目标不是训练 GNN。

本阶段只负责建立：

\[
(G_0,S,P)
\rightarrow
\text{统一、可复现、可测试的方案图表示}
\]

其中：

- \(G_0\)：零件的静态基础加工图；
- \(S\)：该零件的完整 Station 配置；
- \(P\)：一个完整的 Operation → Station 候选方案。

需要最终支持三个逐级 baseline：

\[
Graph0
\rightarrow
Graph1
\rightarrow
Graph2
\]

三者使用相同的 Operation 数据和 Plan 表示，只逐步增加 Operation 之间的关系信息。

本阶段不负责：

```text
GNN训练
方案评分
pairwise ranking loss
CP-SAT
候选方案生成
Top-K搜索
强化学习
UI
数据库
```

---

# 2. 核心原则：1 Operation = 1 Node

第一版固定：

\[
\boxed{1\ Operation = 1\ Node}
\]

P001 当前有 224 个 Operation，因此：

```text
num_nodes = 224
```

节点顺序必须确定且可复现。

建议按照：

```text
operation_id
```

的稳定顺序建立：

```text
node_index <-> operation_id
```

映射。

本阶段不增加：

```text
Station Node
Feature Node
Geometry Primitive Node
Local Region Node
ProcessGroup Node
```

---

# 3. 静态基础图与候选 Plan 必须严格分离

定义：

\[
G(P)=G_0+\operatorname{PlanOverlay}(P)
\]

其中：

## 3.1 StaticPartGraph / \(G_0\)

只包含不会因为候选 Plan 不同而变化的信息：

```text
Operation identity
operation_type
surface_side
geometry_features_v1
static semantic relations
static geometric relations
StationContext
```

---

## 3.2 PlanOverlay

只包含一个完整候选方案中：

```text
Operation -> Station
```

的分配状态，以及由该状态派生的动态特征。

例如：

```text
P1:
O161 -> OP40

P2:
O161 -> OP50
```

O161 的：

```text
operation_type
surface_side
geometry
semantic relations
```

完全不变。

变化的只有 Plan 状态。

---

## 3.3 expert_station

必须继续保持 Milestone 1 已确定的设计：

> `expert_station` 不属于 Operation 的静态属性。

专家参考方案：

\[
P_{expert}
\]

只是一个特殊的 Plan。

不得把 `expert_station` 写入：

```text
Static Node Features
StaticPartGraph
geometry_features_v1
```

---

# 4. Station 第一版不作为图节点

第一版保持：

\[
\boxed{\text{Station 不作为独立 Graph Node}}
\]

但必须完整保存该零件的 Station 配置：

```text
StationContext
```

StationContext 至少包含：

```text
station_id
order
relative_order
allowed_operation_types
```

其中：

\[
relative\_order_j=
\frac{order_j-1}{M-1}
\]

如果零件只有一个 Station，则需要避免除零并采用明确的退化处理。

---

## 4.1 station_id 只作为元数据

不得把：

```text
OP10
OP20
OP30
...
```

作为跨零件具有固定工艺意义的全局类别 embedding。

模型可使用的 Station 语义应主要来自：

```text
local order
relative order
station capability
```

因为不同零件上的 OP20 不具有固定全局含义。

---

## 4.2 Station capability

使用项目当前全部 OperationType 的固定顺序构造 multi-hot：

```text
CIRCLE_PUNCH
IRREGULAR_PUNCH
PRE_PUNCH
FLANGING
BEND
TRIM
DIMPLE
BRIDGE
MARKING
```

例如某 Station 允许：

```text
IRREGULAR_PUNCH
FLANGING
MARKING
DIMPLE
```

则其 capability vector 对应位置为 1，其余为 0。

`NONE` 已在 Milestone 1 中解释为空能力集合。

---

# 5. Operation 节点特征分为 Static 和 Plan 两部分

## 5.1 Static Node Features

第一版至少包括：

```text
operation_type
surface_side
has_geometry
geometry_features_v1
```

其中：

### operation_type

使用固定 OperationType 集合编码。

不要进一步拆分：

```text
IRREGULAR_PUNCH_NORMAL
IRREGULAR_PUNCH_BEND_RELATED
```

等 subtype。

---

## 5.2 关于 IRREGULAR_PUNCH 的特别规定

当前全部异形冲孔继续统一保持：

```text
operation_type = IRREGULAR_PUNCH
```

包括当前 O193～O214 这些与后续折弯等加工特征存在明显关系的异形孔。

原因是：

> “是否与折弯等其它 Operation 存在关系”属于 graph context / relation，而不是加工动作本体类型。

这些异形孔的特殊性应该通过：

```text
PRECEDES
CAD_PARENT
其它已经确认的图关系
```

体现。

不得把这些关系提前压缩成新的 `operation_type`。

否则 Graph 0 会提前获得 Graph 1 本应提供的关系信息，破坏消融实验。

未来如果为了分析需要，可以派生：

```text
is_bend_related_irregular_punch
```

之类的 analysis tag。

但该 tag：

- 不改变原始 OperationType；
- 默认不进入 Graph 0 节点输入；
- 不得作为新的硬规则来源。

---

# 6. 第一版 geometry_features_v1

第一版不训练独立的 contour encoder。

采用：

\[
RawGeometry
\rightarrow
GeometryFeatureExtractorV1
\rightarrow
FixedLengthVector
\]

原始 Geometry 必须完整保留。

`geometry_features_v1` 只是可重新生成的派生特征。

未来可以增加：

```text
geometry_features_v2
```

而无需重新提取 CAD。

---

# 7. 几何归一化坐标系

对每个零件，从所有有效二维 Geometry 得到整个零件的二维 bounding box：

\[
x_{min},x_{max},y_{min},y_{max}
\]

定义：

\[
c_x^{part}=\frac{x_{min}+x_{max}}2
\]

\[
c_y^{part}=\frac{y_{min}+y_{max}}2
\]

定义：

\[
W=x_{max}-x_{min}
\]

\[
H=y_{max}-y_{min}
\]

以及：

\[
D=\sqrt{W^2+H^2}
\]

其中 \(D\) 作为第一版统一长度尺度。

坐标归一化：

\[
x'=\frac{x-c_x^{part}}D
\]

\[
y'=\frac{y-c_y^{part}}D
\]

长度类：

\[
l'=\frac{l}{D}
\]

面积类：

\[
A'=\frac{A}{D^2}
\]

需要对退化尺寸做显式保护，不得出现除零。

原始毫米值仍保留在 Milestone 1 的 Geometry 数据中。

---

# 8. geometry_features_v1 的统一字段

第一版采用简单、稳定、可解释的几何摘要。

每个 Operation 节点生成以下字段：

```text
has_geometry

geom_center_x_norm
geom_center_y_norm

bbox_width_norm
bbox_height_norm

geometry_length_norm
geometry_area_norm
geometry_perimeter_norm

orientation_cos2
orientation_sin2

has_length
has_area
has_perimeter
has_orientation
```

不存在或不适用的数值字段填 0，但必须通过对应 mask 区分：

```text
“不适用 / 不存在”
```

与：

```text
“真实值等于 0”
```

---

# 9. geom_center 的定义

不同 Geometry 使用稳定的代表中心。

## CIRCLE / PRE_PUNCH / DIMPLE

使用圆心。

---

## FLANGING

使用两个同心圆共同的圆心。

---

## BEND

使用折弯线中点：

\[
c=\frac{p_1+p_2}{2}
\]

---

## BRIDGE

使用已有 center。

---

## IRREGULAR_PUNCH

第一版使用该闭合轮廓二维 bounding box 的中心。

当前不要为了第一版专门计算严格面积质心。

因此这里的 `geom_center` 应理解为：

> 稳定的二维代表中心。

不能将它错误描述为所有类型的物理质心。

---

# 10. bbox

对所有存在二维 Geometry 的 Operation 计算：

```text
bbox_width
bbox_height
```

然后按 \(D\) 归一化。

对于圆形对象：

```text
bbox_width = 2r
bbox_height = 2r
```

对于 FLANGING 使用外侧圆对应的 bounding box。

对于 BEND：

```text
bbox
```

由线段两个端点产生。

---

# 11. geometry_length

`geometry_length` 表示该 Geometry 的主要一维长度量。

第一版定义：

### CIRCLE_PUNCH / PRE_PUNCH / DIMPLE

```text
geometry_length = diameter
```

### FLANGING

```text
geometry_length = outer_diameter
```

### BEND

```text
geometry_length = bend_line_length
```

### BRIDGE

```text
geometry_length = footprint diagonal
```

### IRREGULAR_PUNCH

```text
geometry_length = bounding_box diagonal
```

这是第一版统一的 characteristic length。

不要把该字段解释为所有类型完全相同的物理意义。

其作用主要是提供稳定的局部尺度信息。

---

# 12. geometry_area

对于存在明确二维封闭区域的 Geometry，计算二维面积。

适用：

```text
CIRCLE_PUNCH
PRE_PUNCH
FLANGING
IRREGULAR_PUNCH
DIMPLE
BRIDGE
```

BEND：

```text
has_area = 0
geometry_area = 0
```

对于 FLANGING，第一版面积应采用与二维 footprint 一致且可明确解释的定义。

如果两个同心圆究竟应使用：

```text
outer circle area
ring area
其它面积定义
```

在现有规范中仍不够明确，则不要自行选择。

应将 FLANGING 的 `has_area` 暂时置为 0，并报告这一点，而不是加入未经确认的语义。

---

# 13. geometry_perimeter

对于有明确边界长度的 Geometry，可计算 perimeter。

例如：

```text
CIRCLE
IRREGULAR_PUNCH
BRIDGE
```

BEND 不把线长重复解释为 perimeter：

```text
has_perimeter = 0
```

其长度已经由：

```text
geometry_length
```

表达。

对于 FLANGING 的 perimeter 与 area 类似：

如果“双同心圆应该如何定义整体 perimeter”存在语义歧义，则第一版不要自行定义。

---

# 14. orientation

只对第一版具有明确无向主方向的 Geometry 提供 orientation。

至少：

```text
BEND
BRIDGE
```

可以具有 orientation。

对于一条无向线，其方向：

\[
\theta
\]

与：

\[
\theta+\pi
\]

等价。

因此编码：

\[
orientation\_cos2=\cos(2\theta)
\]

\[
orientation\_sin2=\sin(2\theta)
\]

而不是直接存储角度。

第一版对于：

```text
CIRCLE
PRE_PUNCH
FLANGING
DIMPLE
IRREGULAR_PUNCH
```

默认：

```text
has_orientation = 0
```

不要现在为复杂轮廓设计 PCA 或额外主方向算法。

以后如证明有价值，可在 geometry_features_v2 中增加。

---

# 15. 无 Geometry 的 Operation

当前例如部分：

```text
TRIM
MARKING
```

允许没有 Geometry。

对于这些节点：

```text
has_geometry = 0
```

所有 geometry_features_v1 数值字段填 0。

对应 mask：

```text
has_length = 0
has_area = 0
has_perimeter = 0
has_orientation = 0
```

不得把这些零值解释为实际位于原点或实际尺寸为 0。

---

# 16. Plan Node Features

给定候选方案 \(P\)，如果：

\[
P(O_i)=S_j
\]

则该 Operation 节点获得动态 Plan 特征。

至少包括：

```text
assigned_station_relative_order
assigned_station_capability
```

其中：

\[
assigned\_station\_relative\_order
=
\frac{order_j-1}{M-1}
\]

`assigned_station_capability` 为该 Station 对全部 OperationType 的 capability multi-hot。

允许在数据结构中保留：

```text
assigned_station_id
assigned_station_order
```

作为元数据和调试信息。

但模型表示设计不得依赖字符串：

```text
OP10
OP20
...
```

具有跨零件固定语义。

---

# 17. Graph 0

Graph 0 是无 Operation–Operation message passing 的 baseline。

它包含：

```text
Operation nodes
Static Node Features
Plan Node Features
StationContext
```

但不建立用于消息传递的 Operation–Operation edges。

概念上：

\[
h_i=f(X_i^{static},X_i^{plan})
\]

之后模型阶段再决定如何 pooling。

Milestone 2 不实现最终方案评分网络。

---

## Graph 0 的实验意义

Graph 0 回答：

> 仅依靠每个动作自身属性、几何摘要和当前 Station 状态，能够获得多少完整方案评价能力？

Graph 1 和 Graph 2 必须与 Graph 0 使用一致的基础特征，保证消融实验公平。

---

# 18. Graph 1：显式语义关系图

Graph 1：

\[
Graph1=Graph0+\text{Semantic Relations}
\]

第一版包含三类核心语义：

```text
PRECEDES
SAME_PROCESS_GROUP
CAD_PARENT
```

---

# 19. PRECEDES

PRECEDES 为有向关系：

\[
A\rightarrow B
\]

表示 A 必须在 B 之前执行。

来源至少有：

```text
EXPLICIT_PREDECESSOR
PROCESS_GROUP_PRECEDENCE
```

其中：

### EXPLICIT_PREDECESSOR

来自：

```text
required_predecessor_ids
```

### PROCESS_GROUP_PRECEDENCE

来自已经确认的：

\[
PRE\_PUNCH\rightarrow FLANGING
\]

原始 relation source 必须保留。

即使第一版模型最后将其统一视为 PRECEDES，也不得在数据层丢失来源信息。

---

# 20. SAME_PROCESS_GROUP

属于同一个 `process_group_id` 的 Operation 建立：

```text
SAME_PROCESS_GROUP
```

这是对称语义。

消息传递表示中可以保存两个方向：

\[
A\rightarrow B
\]

\[
B\rightarrow A
\]

目前 P001 中主要对应：

```text
PRE_PUNCH
FLANGING
```

配对。

---

# 21. CAD_PARENT

CAD parent-child 保持独立语义。

原始方向：

\[
Parent\rightarrow Child
\]

消息传递数据结构中应能够同时表达：

```text
CAD_PARENT
CAD_CHILD
```

或者通过：

```text
relation type + reverse edge
```

达到等价效果。

不得把 CAD_PARENT 自动转换为 PRECEDES。

---

# 22. 反向消息边

对于天然有方向的关系：

```text
PRECEDES
CAD_PARENT
```

为了后续 GNN 支持双向信息传播，可以显式产生 reverse message relation。

例如：

```text
PRECEDES
SUCCEEDS
```

和：

```text
CAD_PARENT
CAD_CHILD
```

需要明确：

> reverse edge 只是图消息传递表示，不代表新增工艺事实。

原始业务关系仍然只有其真实方向。

---

# 23. 同一 Operation pair 允许存在多个 relation

例如两个 Operation 可以同时：

```text
属于同一个 process group
并且存在 PRECEDES
```

因此同一节点对允许具有多个语义 relation。

不得为了简化而强制合并成唯一无类型 edge。

---

# 24. Plan Edge Features

对于 Graph 1 和 Graph 2 中任意已有边：

\[
i\rightarrow j
\]

根据候选 Plan 动态生成：

```text
same_station
station_delta
abs_station_gap
```

定义：

\[
same\_station_{ij}
=
\mathbf 1[P(O_i)=P(O_j)]
\]

\[
station\_delta_{ij}
=
\frac{order(P(O_j))-order(P(O_i))}{M-1}
\]

\[
abs\_station\_gap_{ij}
=
|station\_delta_{ij}|
\]

对于反向 edge：

```text
station_delta
```

应自然改变符号。

这些字段属于：

```text
PlanOverlay
```

不得保存到 StaticPartGraph。

---

# 25. Graph 2：二维几何邻接图

Graph 2：

\[
Graph2=Graph1+\text{Geometric Relations}
\]

第一版采用：

\[
\boxed{\text{kNN + 实际二维 Geometry 最短距离}}
\]

当前固定初始参数：

\[
k=8
\]

未来可以作为实验参数比较：

```text
k = 4
k = 8
k = 12
```

但第一版实现默认：

```text
k = 8
```

---

# 26. Geometry distance

对于两个具有 Geometry 的 Operations：

\[
O_i,O_j
\]

定义二维最短几何距离：

\[
d_{ij}
=
\min_{p\in Geometry_i,q\in Geometry_j}
\|p-q\|_2
\]

目标是衡量：

> 两个真实加工几何对象在展开平面中最近有多远。

第一版不使用单纯 centroid distance 代替真实 Geometry distance。

原因：

例如一条很长的 BEND line 与一个孔的实际边界很近，但两者中心可能距离较远。

---

# 27. Geometry distance 应支持当前几何类型

距离计算至少应覆盖当前 P001 会参与几何邻接的类型：

```text
CIRCLE
LINE
ARC
BEND_LINE
BRIDGE footprint
IRREGULAR_PUNCH closed contour
FLANGING concentric circles
```

实现应基于当前 Milestone 1 已解析的结构化 Geometry。

不得重新读取或重新解释 raw Excel。

---

# 28. Geometry distance 实现原则

优先：

```text
简单
确定
可测试
数值稳定
```

不要为了第一版引入复杂 CAD kernel。

如果使用外部几何库，应先说明：

- 为什么标准库 / 当前实现不足；
- 新依赖的用途；
- 是否改变现有数据语义。

不要自行引入大型 CAD / mesh 框架。

---

# 29. 几何 kNN

只在：

```text
has_geometry = 1
```

的 Operation 之间计算几何邻接。

对于每个有 Geometry 的节点 \(i\)：

找到距离最近的：

\[
k=8
\]

个其它有 Geometry 节点。

如果：

\[
N_{geom}-1 < k
\]

则使用全部其它几何节点。

---

# 30. kNN 对称化

第一版将几何邻接对称化。

如果：

\[
j\in KNN(i)
\]

或者：

\[
i\in KNN(j)
\]

则建立：

```text
GEOMETRIC_NEAR
```

关系。

消息传递实现中保存双向 edge。

---

# 31. 无 Geometry 节点在 Graph 2 中的处理

没有 Geometry 的 Operation：

```text
TRIM
MARKING
...
```

第一版不参加 GEOMETRIC_NEAR kNN。

但它们仍然：

- 保持 Operation node；
- 可以拥有 Graph 1 semantic relations；
- 可以拥有 Plan node features；
- 参与完整方案表示。

不得因为没有 Geometry 而删除这些节点。

---

# 32. Geometric Edge Features

第一版 `GEOMETRIC_NEAR` edge 至少包含：

```text
distance_norm
relative_dx_norm
relative_dy_norm
```

其中：

\[
distance\_norm=\frac{d_{ij}}D
\]

两个 Operation 使用各自 `geom_center`：

\[
relative\_dx\_norm=
\frac{x_j-x_i}{D}
\]

\[
relative\_dy\_norm=
\frac{y_j-y_i}{D}
\]

由于 edge 为有向 message edge：

反向 edge：

```text
dx, dy
```

应改变符号。

distance 保持相同。

此外继续附加 Plan Edge Features：

```text
same_station
station_delta
abs_station_gap
```

---

# 33. 不在 Graph 2 v1 中加入的几何关系

第一版暂时不要增加：

```text
same_region
hole_to_bend_special_relation
parallel_bend
inside_polygon
contains_feature
manual_local_region
bend_angle_relation
3D collision
3D accessibility
```

这些都需要额外工艺假设或进一步设计。

Graph 2 v1 首先只回答：

> 简单、客观的二维空间邻近关系本身有没有价值？

---

# 34. 同一节点对上的 semantic edge 和 geometric edge 不合并

如果：

```text
Oa -- PRECEDES --> Ob
```

同时二者又是：

```text
GEOMETRIC_NEAR
```

应保留两个 relation。

不要合并为：

```text
GENERIC_EDGE
```

因为两种关系语义完全不同。

---

# 35. Graph 0 / 1 / 2 必须共享相同节点

对于同一个 Part 和 Plan：

```text
Graph0
Graph1
Graph2
```

必须具有：

```text
完全相同的 node_index
完全相同的 Static Node Features
完全相同的 Plan Node Features
完全相同的 StationContext
```

区别只允许是：

```text
Graph0: no relational edges

Graph1: semantic edges

Graph2: semantic edges + geometric edges
```

这样后续实验结果才具有可解释性。

---

# 36. Graph 0 / 1 / 2 的实验目的

## Graph 0

验证：

> 单个 Operation 自身信息 + 当前 Station 分配是否已经足够。

---

## Graph 1

验证：

> 明确工艺关系和 CAD 结构关系是否提供额外信息。

---

## Graph 2

验证：

> 展开二维几何空间邻接是否进一步提供额外信息。

因此：

\[
Graph0\rightarrow Graph1\rightarrow Graph2
\]

本质是逐级消融实验设计。

不是为了证明 GNN 一定有效。

---

# 37. 当前不冻结 pooling

Milestone 2 不决定：

```text
MeanPool
MaxPool
Mean + Max
Attention Pool
Station-level Pool
Hierarchical Pool
```

这些属于后续模型设计。

Milestone 2 的图表示只需要保证未来能够支持不同 pooling 实验。

---

# 38. 当前不决定 GNN 类型

Milestone 2 不选择：

```text
GCN
GAT
GraphSAGE
R-GCN
HGT
GIN
```

也不决定：

```text
hidden size
number of layers
dropout
optimizer
loss
```

当前禁止为了某一种具体 GNN 修改基础数据语义。

---

# 39. 推荐代码模块

可以在已有：

```text
src/graph/
```

中建立简单、清晰、framework-neutral 的图数据层。

推荐职责划分：

```text
src/graph/
├─ schema.py
├─ geometry_features.py
├─ geometry_distance.py
├─ static_graph.py
├─ plan_overlay.py
├─ builders.py
└─ report.py
```

具体文件名可以根据现有代码风格微调，但职责应保持清晰。

当前不要依赖：

```text
PyTorch
PyTorch Geometric
DGL
```

Milestone 2 应首先得到一个与神经网络框架无关的可验证图表示。

后续训练阶段再写 adapter 转换为具体 tensor / graph library 格式。

---

# 40. 建议图数据对象

可以设计类似：

```text
StaticPartGraph
PlanOverlay
PlanGraph
StationContext
NodeStaticFeatures
NodePlanFeatures
SemanticEdge
GeometricEdge
```

但不要为了未来扩展创建过深继承体系。

优先使用：

```text
dataclass
enum
明确字段
简单组合
```

保持与 Milestone 1 风格一致。

---

# 41. Graph Builder 输入

Graph Builder 不直接读取 Excel。

必须从 Milestone 1 已冻结的数据对象：

```text
PartData
Operation
Geometry
Station
ProcessGroup
ExpertReferencePlan
...
```

构建图。

正确依赖方向：

```text
Excel
  ↓
Milestone 1 Loader
  ↓
PartData
  ↓
Milestone 2 Graph Builder
```

禁止：

```text
Graph Builder -> 重新读取 Sheet
```

---

# 42. Expert Plan 的使用方式

为了验证 PlanOverlay，可以使用：

\[
P_{expert}
\]

作为第一套完整 Plan 输入。

这只是因为当前已有专家参考方案，方便测试。

不得因此：

```text
把 expert_station 写入 StaticPartGraph
```

或者：

```text
把专家方案作为唯一合法Plan硬编码进Graph Builder
```

Graph Builder 必须能够接受任意满足基本结构要求的：

```text
Operation -> Station
```

完整 Plan。

---

# 43. PlanOverlay 基本结构校验

一个 Plan 至少必须：

1. 覆盖当前 Part 的全部 Operation；
2. 每个 Operation 恰好分配一个 Station；
3. 不引用其它 Part 的 Operation；
4. 不引用不存在的 Station。

Milestone 2 可以调用已有 Validator 或轻量检查基础结构。

但是：

> Graph Builder 本身不是完整的候选方案可行性求解器。

候选方案生成和全面 feasibility checking 属于后续 Milestone。

---

# 44. Milestone 2 自动化测试

必须建立对应自动化测试。

至少覆盖以下内容。

### Node mapping

- 224 个 P001 Operation 全部成为 node；
- node_index 稳定；
- operation_id 可双向映射。

### Static / Plan separation

- 切换不同 Plan 时 StaticPartGraph 不变化；
- PlanOverlay 变化；
- expert_station 不出现在 static node features 中。

### StationContext

- 7 个 Station 全部存在，包括空能力 OP70；
- 使用 `order` 而不是解析 station_id；
- capability multi-hot 正确。

### Geometry feature

- Circle 几何摘要正确；
- Bend 中点、长度和 orientation 正确；
- irregular punch bbox 和代表中心正确；
- 无 Geometry 节点 mask 正确；
- 数值归一化不产生 NaN / Inf。

### Graph 1

- required predecessor 正确产生 PRECEDES；
- process group 正确产生 SAME_PROCESS_GROUP；
- PRE_PUNCH -> FLANGING precedence 正确；
- CAD parent 不被误转成 PRECEDES；
- reverse message relations 正确；
- 同一 pair 可以同时具有多个 relation。

### Plan edge features

- same_station 正确；
- station_delta 使用 Station.order；
- reverse edge 的 station_delta 符号相反；
- abs_station_gap 正确。

### Graph 2

- Geometry shortest-distance 基础 primitive 有最小单元测试；
- kNN 使用真实 geometry distance；
- 默认 k=8；
- kNN 对称化正确；
- 无 Geometry Operation 不产生 GEOMETRIC_NEAR；
- 不会因为没有 Geometry 删除 node；
- geometric edge distance/dx/dy 正确；
- Graph 2 包含全部 Graph 1 semantic edges。

### Baseline consistency

对于同一个 Part + Plan：

```text
Graph0.node_count
==
Graph1.node_count
==
Graph2.node_count
```

并且：

```text
node_index
static node features
plan node features
StationContext
```

完全一致。

---

# 45. 建议实现顺序

Milestone 2 建议按以下内部阶段完成：

```text
2A
GeometryFeatureExtractorV1
+
StationContext
+
Static Node Features
+
PlanOverlay

2B
Graph 0

2C
Graph 1 semantic relations

2D
Geometry distance primitives

2E
Graph 2 kNN geometric relations

2F
完整测试与报告
```

每一步都应建立测试后再继续下一步。

---

# 46. 当前禁止事项

本阶段不要：

```text
训练任何神经网络
下载预训练模型
实现GNN层
实现pooling模型
实现pairwise ranking
实现CP-SAT
生成大量候选Plan
修改raw Excel
新增CAD字段
重新分类IRREGULAR_PUNCH
根据expert_station制造新的硬规则
添加未经确认的工艺relation
重建中间3D状态
```

---

# 47. Milestone 2 完成后的汇报要求

完成后汇报：

1. 新增和修改了哪些文件；
2. StaticPartGraph 的数据结构；
3. PlanOverlay 的数据结构；
4. geometry_features_v1 的实际字段和计算方法；
5. StationContext 的表示；
6. Graph 0 实际包含什么；
7. Graph 1 各 relation 数量及来源；
8. Graph 2 的 kNN 实现和 geometric edge 数量；
9. Geometry distance 支持了哪些 primitive pair；
10. expert plan 构造出的 Graph 0/1/2 基本统计；
11. 所有自动化测试结果；
12. 是否发现 PROJECT_SPEC 或数据语义冲突；
13. 是否存在仍需人工确认、会阻碍下一阶段的问题；
14. 是否认为 Milestone 2 可以冻结。

---

# 48. Milestone 2 的冻结标准

只有同时满足：

```text
Milestone 1数据语义未被改变
StaticPartGraph与PlanOverlay明确分离
Graph0/1/2可稳定重建
P001全部Operation未丢失
semantic relation来源可追踪
geometry_features_v1可重复计算
Graph2使用真实二维Geometry距离
自动化测试全部通过
不存在未经确认的工艺假设
```

才能认为 Milestone 2 可以冻结。

Milestone 2 冻结后，下一阶段再讨论：

```text
完整候选方案如何生成
如何获得专家pairwise preference
方案评价模型结构
pooling
GNN architecture
训练与评价指标
```

本阶段不要提前进入这些问题。