# 钣金加工工序分配项目

## 1. 推荐项目目录

建议项目根目录：

```text
D:\sheet_process_assignment\
│
├─ PROJECT_SPEC.md                 # 项目事实与研究路线，最高优先级
├─ README.md                       # 简短入口，指向 PROJECT_SPEC.md
│
├─ data\
│   ├─ raw\
│   │   └─ 钣金加工数据表_P001.xlsx
│   │
│   ├─ processed\                  # 程序生成的规范化数据，禁止手工修改
│   └─ candidates\                 # 后续生成的候选完整方案
│
├─ docs\
│   └─ DECISIONS.md                # 后续记录新确认的工艺事实和架构决策
│
├─ src\
│   ├─ data\                       # Excel读取、schema、数据校验
│   ├─ graph\                      # Graph 0 / Graph 1 / Graph 2 构造
│   ├─ constraints\                # 硬约束与可行性检查
│   ├─ candidate\                  # 完整可行方案生成
│   ├─ models\                     # 后续方案评价模型
│   └─ evaluation\                 # 排序、对比、实验评价
│
├─ scripts\                        # 可直接运行的实验/数据处理入口
├─ tests\                          # 数据和算法单元测试
├─ configs\                        # 后续实验参数
└─ outputs\                        # 图、日志、模型结果、实验报告等
```

当前阶段不要求上述所有目录中都有代码。

第一阶段真正需要存在的只有：

```text
PROJECT_SPEC.md
data/raw/钣金加工数据表_P001.xlsx
src/data/
tests/
```

在问题定义和图结构确定以前，不要提前搭建复杂模型训练框架。

---

# PROJECT_SPEC.md

# 钣金加工动作工序分配项目规范

## 0. 文档作用

本文档定义本项目目前已经确认的：

- 研究问题；
- 核心术语；
- 数据语义；
- 工艺约束；
- 当前研究路线；
- 图建模目标；
- 已确认事实与尚未确认的问题；
- 当前开发阶段和禁止提前开展的工作。

本文档是当前项目的主要事实来源。

如果代码、注释、实验脚本、模型假设与本文档冲突，应优先检查实现是否偏离了问题定义，而不是擅自修改项目语义。

未经明确讨论，不得：

1. 自行增加新的工艺硬规则；
2. 把观察到的专家习惯直接写成硬约束；
3. 把 `expert_station` 当作唯一正确答案；
4. 把 OP10、OP20 等工序编号当成跨零件固定类别；
5. 擅自要求补充新的二维或三维 CAD 数据；
6. 把项目改写成普通的节点分类问题；
7. 提前引入 RL、复杂异构 GNN 或过程三维重建。

---

# 1. 项目总体目标

本项目研究的是：

> 给定一个钣金零件的全部具体加工动作、展开态二维几何、该零件已经确定的加工工序序列，以及每一道工序允许执行的加工动作类型，自动获得一套完整的加工动作到工序的分配方案。

设一个零件共有 \(N\) 个具体加工动作：

\[
O=\{O_1,O_2,\ldots,O_N\}
\]

该零件有若干按顺序排列的加工工序：

\[
S=\{S_1,S_2,\ldots,S_M\}
\]

最终输出完整方案：

\[
P=\{O_i\rightarrow S_j\}
\]

即每一个 Operation 都被分配到一个合法 Station。

例如：

```text
O001 -> OP10
O002 -> OP20
O003 -> OP20
...
```

最终目标不是预测一个局部标签，而是生成并评价一个零件的完整工艺分配方案。

---

# 2. 核心概念

## 2.1 Operation

`Operation` 是本项目真正的决策单元。

一个 Operation 表示一个具体的加工动作实例。

例如：

```text
圆形冲孔
非圆形冲孔
预孔
翻孔
一般折弯
切边
凸米
折桥
刻线
```

最终被分配到工序的是 Operation。

因此：

```text
Operation -> Station
```

才是本项目的基本决策。

---

## 2.2 Feature 与 Operation 必须区分

CAD 中的一个最终几何特征不一定对应一个 Operation。

例如一个最终翻孔可能包含：

```text
PRE_PUNCH
+
FLANGING
```

它们是两个独立加工动作：

```text
Oa = PRE_PUNCH
Ob = FLANGING
```

并且可能分别被分配到：

```text
Oa -> OP10
Ob -> OP30
```

因此不能把“最终三维特征”直接当成工序分配节点。

---

## 2.3 Station

Station 表示当前零件的一道具体加工工序。

例如：

```text
OP10
OP20
OP30
...
```

需要特别注意：

> OP10、OP20、OP30 等名称只具有零件内部的局部意义，不是跨所有零件共享语义的全局类别。

不同零件可以都有 `OP20`，但两个零件的 OP20：

- 加工能力可能不同；
- 工序位置可能不同；
- 允许执行的 OperationType 可能不同。

因此本项目不是：

```text
Operation -> 全局 OP 类别
```

的普通多分类问题。

未来进行跨零件训练时，不得把字符串 `OP20` 当成具有固定全局工艺含义的标签。

---

## 2.4 Plan

Plan 是一个完整的工序分配方案：

\[
P=\{O_i\rightarrow S_j\}
\]

同一个零件可以有多个不同但都满足硬约束的方案：

\[
P_1,P_2,\ldots,P_m
\]

方案 B 的核心就是评价这些完整方案之间的优劣。

---

# 3. 当前数据集

当前第一份零件数据：

```text
part_id = P001
```

原始文件：

```text
data/raw/钣金加工数据表_P001.xlsx
```

当前 P001 静态数据提取视为：

```text
v0.1 Frozen
```

也就是说：

> 当前阶段暂停继续从二维或三维 CAD 中无目的地增加字段。

只有后续实验明确证明缺少某项具体信息时，才重新回到 CAD 做针对性提取。

---

# 4. Excel 数据结构

当前 Excel 包含三张主要数据表：

```text
Sheet1 = Operations
Sheet2 = Geometry
Sheet3 = Stations
```

三者共同描述一个零件。

---

# 5. Sheet1：Operation 数据

Sheet1 中：

> 一行代表一个具体加工 Operation。

核心字段包括：

```text
part_id
operation_id
source_feature_ref
operation_type
surface_side
required_predecessor_ids
cad_parent_operation_id
process_group_id
expert_station
geometry_id
```

---

## 5.1 operation_id

Operation 在当前零件中的唯一标识。

例如：

```text
O001
O002
...
```

后续所有图节点、约束、方案分配都通过 `operation_id` 引用具体动作。

---

## 5.2 operation_type

当前数据中已经明确的 OperationType 包括：

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

OperationType 是判断某个动作能否进入某道 Station 的核心字段之一。

---

## 5.3 surface_side

`surface_side` 表示该加工动作属于材料的光面还是毛面。

当前定义：

```text
SMOOTH = 光面
ROUGH  = 毛面
```

在展开态材料坐标中，可以理解为：

```text
SMOOTH -> (0, 0,  1)
ROUGH  -> (0, 0, -1)
```

这里表达的是材料两侧的工艺语义，而不是最终成形状态下的世界坐标法向量。

因此：

> `surface_side` 不应该通过最终三维几何位置反推。

即使两个翻孔的最终三维位置和形态完全一致，也可能分别被定义为光面翻孔和毛面翻孔。

---

## 5.4 process_group_id

`process_group_id` 用于表示多个 Operation 属于同一个复合加工对象。

当前最典型的情况是：

```text
PRE_PUNCH
+
FLANGING
```

属于同一个翻孔加工对象。

例如：

```text
O011  FLANGING   PG001
O012  PRE_PUNCH  PG001
```

这表明两个 Operation 具有明确的业务关联。

对于当前已经确认的预孔—翻孔关系：

\[
PRE\_PUNCH \rightarrow FLANGING
\]

即预孔必须先于对应翻孔完成。

`process_group_id` 的主要作用是建立这种跨 Operation 的语义关联，而不是替代 `operation_id`。

---

## 5.5 required_predecessor_ids

该字段表示已经明确确认的工艺前置依赖。

例如：

```text
O161.required_predecessor_ids = O214
```

含义：

\[
O214\rightarrow O161
\]

即：

> O214 必须先成型，O161 才能执行。

对于完整方案 \(P\)，必须满足：

\[
order(P(O214)) < order(P(O161))
\]

这是硬工艺约束，而不是模型需要自己学习的软偏好。

---

## 5.6 cad_parent_operation_id

该字段来自 CAD / 特征树结构关系。

需要严格区分：

```text
CAD_PARENT
```

与：

```text
PROCESS_PRECEDENCE
```

二者不是同一概念。

因此：

> 不得仅因为 A 是 B 的 CAD parent，就自动推断 A 必须先于 B 加工。

除非对应关系已经在 `required_predecessor_ids` 或其它明确规则中得到确认。

---

## 5.7 expert_station

`expert_station` 是当前专家标准工艺中该 Operation 所在的 Station。

它组成专家参考方案：

\[
P_{expert}
\]

需要注意：

> `expert_station` 是专家参考答案，不代表数学意义上已经证明的全局唯一最优解。

因此不得自动定义：

```text
与 expert_station 相同 = 正确
与 expert_station 不同 = 错误
```

其它满足所有硬约束的方案仍然可能是工艺上合理的方案。

---

## 5.8 geometry_id

如果一个 Operation 有当前研究所需的二维几何信息，则通过 `geometry_id` 与 Sheet2 对应。

部分不需要二维几何参与当前研究的 Operation 可以没有 `geometry_id`。

空 geometry 不应自动被认为是数据错误。

---

# 6. Sheet2：二维展开态 Geometry

本项目当前选择：

> 使用钣金展开态二维材料坐标作为静态几何的统一参考系。

原因是：

成形过程中的折弯会改变材料区域在世界三维坐标中的：

- XYZ 位置；
- 空间法向；
- 相对姿态。

如果直接使用最终成形状态的世界 XYZ 作为静态坐标，同一个材料位置在不同工序阶段并不稳定。

因此当前优先使用：

\[
(u,v)
\]

二维展开材料坐标。

当前不要求重建：

```text
OP10之后的3D模型
OP20之后的3D模型
OP30之后的3D模型
...
```

只有后续明确证明某个工艺判断必须依赖中间三维状态时，才考虑增加动态 3D 信息。

---

# 7. 各类 Operation 的二维几何表示

## 7.1 CIRCLE_PUNCH

圆形冲孔使用：

\[
(x_c,y_c,r)
\]

即：

```text
center_x
center_y
radius
```

---

## 7.2 PRE_PUNCH

预孔使用：

\[
(x_c,y_c,r)
\]

即圆心与半径。

---

## 7.3 FLANGING

翻孔当前使用其二维圆形几何表示。

当前一个翻孔可以由两个同心圆描述。

在现阶段研究中：

> 不需要人为给两个圆赋予额外复杂工艺语义。

---

## 7.4 IRREGULAR_PUNCH

非圆形冲孔表示为由：

```text
LINE
ARC
```

组成的有序闭合曲线链。

每个 geometry 内部使用：

```text
seq = 1, 2, 3, ...
```

表示曲线段顺序。

---

### 7.4.1 非圆形冲孔统一起始段规则

当前数据采用确定性的起始段规则。

概念上：

1. 选择图形左上区域的圆弧；
2. 根据当前统一的 270° 旋转判定规则，选择 x、y 尽量小的对应圆弧作为起始段；
3. 在该起始圆弧中，以 x 较小的一端作为该圆弧的起点；
4. 从起始段开始，按照规定顺序逐段连接；
5. 整个轮廓最终形成闭合曲线链。

该规则属于数据预处理协议。

不得在后续模型中重新随机选择轮廓起点。

---

### 7.4.2 LINE

直线段由：

```text
start_x
start_y
end_x
end_y
```

唯一确定。

---

### 7.4.3 ARC

圆弧保存：

```text
start_x
start_y
end_x
end_y
center_x
center_y
radius
sweep_angle
```

当前约定：

```text
sweep_angle > 0  -> 逆时针旋转
sweep_angle < 0  -> 顺时针旋转
```

`sweep_angle` 表示从当前圆弧起点绕圆心旋转到终点的有符号角度。

圆弧恢复方式为：

\[
P_{end}
=
Rotate(P_{start}, Center, sweep\_angle)
\]

其中：

- 正角表示逆时针旋转；
- 负角表示顺时针旋转。

需要特别区分：

> 整个非圆孔轮廓按照 `seq` 顺序逆时针遍历，

与：

> 单条 ARC 的局部旋转方向。

二者不是同一概念。

一个整体逆时针遍历的闭合轮廓中，可以同时存在逆时针 ARC 和顺时针 ARC。

整个非圆孔轮廓按照 `seq` 顺序逐段连接，并最终闭合。

---

## 7.5 BEND

一般折弯当前使用折弯线：

\[
(x_1,y_1)\rightarrow(x_2,y_2)
\]

表示。

即：

```text
start_x
start_y
end_x
end_y
```

根据当前工艺分析已经确认：

> 折弯角度不是决定该折弯应被分配到哪一道 Station 的因素。

因此 v0.1 中：

```text
bend_angle
```

不是方案评价所必需的数据。

不得仅为了几何完整性自行要求重新提取折弯角度。

---

## 7.6 TRIM

切边属于当前零件中工艺安排较固定的动作。

P001 中切边主要出现在早期工序，并具有明确的分序加工形式。

当前阶段：

> TRIM 属于完整 Plan 的 Operation，但不是需要重点依靠 GNN 学习其工序分配逻辑的对象。

如果当前数据没有为某些 TRIM 保存 Geometry，不应自动认为数据不完整。

---

## 7.7 DIMPLE

凸米在展开态可表现为小圆形区域。

当前主要保留其基础二维位置/几何信息。

根据目前观察：

> DIMPLE 对其它 Operation 工序分配的影响较弱。

当前不应为其设计复杂几何编码器。

---

## 7.8 BRIDGE

折桥在展开态主要表现为矩形或圆角矩形区域。

当前保存基础二维 footprint 即可。

根据目前观察：

> BRIDGE 对其它动作工序分配的影响较弱。

---

## 7.9 MARKING

MARKING 属于实际加工动作。

如果当前方案中其工序基本固定，且二维位置不参与其它动作分配判断，则当前阶段允许没有 Geometry。

---

# 8. Sheet3：Station 数据

Sheet3 描述当前零件全部 Station。

核心字段包括：

```text
part_id
station_id
order
allowed_operation_types
```

例如：

```text
P001
OP10
1
...
```

---

## 8.1 Station 顺序

`order` 定义工序先后顺序。

所有工艺前置判断都应该使用：

```text
station.order
```

而不是直接解析字符串 `OP10`、`OP20` 的数字大小。

---

## 8.2 Station capability

每一道 Station 明确规定允许执行的 OperationType：

```text
allowed_operation_types
```

如果：

\[
type(O_i)\notin allowed(S_j)
\]

则：

\[
O_i\not\rightarrow S_j
\]

这是硬约束。

模型不得通过评分覆盖或违反该约束。

---

# 9. 硬约束层

系统首先需要定义可行域。

GNN 不负责把非法方案变成合法方案。

当前已经确认的硬约束至少包括以下内容。

---

## 9.1 Station capability constraint

一个 Operation 只能进入允许其 OperationType 的 Station。

---

## 9.2 Explicit predecessor constraint

如果：

```text
A in B.required_predecessor_ids
```

则必须：

\[
order(P(A))<order(P(B))
\]

---

## 9.3 PRE_PUNCH / FLANGING constraint

对于属于同一预孔—翻孔加工组的 Operations：

\[
PRE\_PUNCH\rightarrow FLANGING
\]

即：

\[
order(P(PRE\_PUNCH))
<
order(P(FLANGING))
\]

---

# 10. 禁止擅自转化为硬规则的观察

下列类型的信息如果尚未由工艺人员明确确认，只能视为：

```text
观察
假设
软偏好候选
```

不得直接写成 hard constraint。

例如：

```text
“大折弯通常做得比较晚”
“某些相邻动作看起来应该在同一序”
“光面折弯和毛面折弯似乎尽量不要同序”
“专家方案中某类动作通常集中在某一 OP”
```

专家参考方案中的统计规律：

\[
\neq
\]

已经确认的工艺硬约束。

---

# 11. 当前研究路线：Scheme B

本项目当前采用：

```text
Scheme B：完整方案评价
```

而不是单动作工序适配度预测。

完整流程：

```text
原始 CAD / 图纸数据
        ↓
Operation / Geometry / Station
        ↓
构造基础加工图 G0
        ↓
硬约束定义可行域
        ↓
生成多个完整可行方案 P1...Pm
        ↓
将不同 Plan 状态附加到同一个基础加工图
        ↓
GNN / baseline 对完整 Plan 进行评价
        ↓
方案排序
        ↓
Top-K 推荐
        ↓
与专家参考方案比较
```

---

# 12. 为什么没有采用 Scheme A

曾考虑过：

> 对每一个 Operation–Station 配对预测局部适配分数，然后使用 CP-SAT 求解完整方案。

即：

\[
q_{ij}=Suitability(O_i,S_j)
\]

然后：

\[
\max \sum_{i,j}q_{ij}x_{ij}
\]

但是该路线存在明显问题：

> 很多单动作—工序适配度可能可以直接由人工规则定义。

如果如此，则 GNN 很容易退化为：

```text
人工规则
+
人工适配分数
+
CP-SAT
```

模型研究价值较弱。

因此当前重点转向 Scheme B：

> 学习规则难以直接表达的完整方案组合质量。

---

# 13. Scheme B 的模型目标

对于一个零件：

- 基础加工图为 \(G_0\)；
- Station 配置为 \(S\)；
- 一套完整分配方案为 \(P\)。

评价模型概念上为：

\[
Q_\theta(G_0,S,P)\rightarrow q
\]

其中：

\[
q
\]

表示完整方案质量或方案偏好分数。

需要特别注意：

> 模型评价的是 COMPLETE PLAN，而不是单个 Operation 的工序类别。

---

# 14. 基础图与 Plan 状态必须分离

一个零件的基础加工事实：

```text
Operation type
surface side
geometry
工艺关系
CAD关系
```

对于不同候选方案不会发生变化。

因此定义：

\[
G_0
\]

为零件的基础加工图。

候选方案：

\[
P_1,P_2,\ldots,P_m
\]

改变的是：

\[
station(O_i)
\]

例如：

```text
P1:
O161 -> OP40

P2:
O161 -> OP50
```

O161 本身仍然是同一个 Operation。

因此应理解为：

\[
G(P_k)=G_0+\text{Plan State}(P_k)
\]

不得为每个 Plan 重复生成一套完全独立的静态 Geometry 数据。

---

# 15. 当前图建模研究问题

当前下一阶段任务是：

> 确定如何从现有 P001 数据构造 Scheme B 使用的基础图，以及图结构是否真的为完整方案评价提供额外信息。

当前计划首先比较三个逐级 baseline。

---

## 15.1 Graph 0：无关系 baseline

Graph 0 不使用 Operation 之间的消息传递关系。

每个 Operation 独立编码：

```text
Operation自身属性
+
几何摘要
+
当前Plan中的Station状态
```

然后对全部 Operation 表示进行 pooling，得到完整方案表示。

Graph 0 用于回答：

> 仅使用单动作信息和当前工序归属，已经能获得多少方案评价能力？

Graph 0 是后续图模型必须超过的 baseline。

---

## 15.2 Graph 1：加入已确认语义关系

Graph 1 在 Graph 0 基础上加入已经明确存在的 Operation 关系。

至少包括：

```text
PRECEDES
SAME_PROCESS_GROUP
CAD_PARENT
```

其中：

### PRECEDES

来自：

```text
required_predecessor_ids
```

以及其它已经明确确认的工艺前置规则。

### SAME_PROCESS_GROUP

来自：

```text
process_group_id
```

### CAD_PARENT

来自：

```text
cad_parent_operation_id
```

这些关系必须保留不同语义。

不得将其全部合并成无类型的普通边而失去业务含义，除非后续实验明确决定这样处理。

Graph 1 用于回答：

> 明确的工艺/CAD关系是否能提高完整方案评价能力？

---

## 15.3 Graph 2：加入二维空间关系

Graph 2 在 Graph 1 基础上进一步加入：

```text
由展开态二维几何计算得到的空间关系
```

候选包括：

```text
GEOMETRIC_NEAR
distance
relative position
hole-to-bend distance
bend-to-bend relation
...
```

Graph 2 的具体邻接定义尚未最终确定。

例如以下问题仍需要讨论：

```text
使用 kNN 还是距离阈值？
几何距离使用中心距还是轮廓最短距离？
不同类型 Operation 如何统一定义代表位置？
BEND 与孔之间如何定义相对位置？
```

因此：

> 在图结构正式讨论完成之前，不得擅自固定 Graph 2 的邻接算法。

Graph 2 用于回答：

> 二维几何结构是否为完整方案评价提供额外信息？

---

# 16. Graph 0 / 1 / 2 的真正目的

Graph 0 / Graph 1 / Graph 2 不是为了单纯比较哪一种 GNN 架构更复杂。

真正目的在于验证：

\[
\boxed{
图结构与空间关系是否真的具有额外信息价值
}
\]

理想实验逻辑：

\[
Graph0
\rightarrow
Graph1
\rightarrow
Graph2
\]

如果：

\[
Graph1>Graph0
\]

说明显式关系具有价值。

如果：

\[
Graph2>Graph1
\]

说明二维几何空间关系进一步具有价值。

如果：

\[
Graph0\approx Graph1\approx Graph2
\]

则应认真考虑：

> 图结构可能不是该任务性能来源，简单方案级模型可能已经足够。

不得为了使用 GNN 而强行证明 GNN 有效。

---

# 17. 当前节点定义

第一版默认：

\[
\boxed{1\ Operation = 1\ Node}
\]

P001 当前有多少 Operation，就有多少 Operation Node。

暂不采用：

```text
Feature Node
Geometry Primitive Node
Panel Node
Local Region Node
Station Node
```

等更复杂结构。

如果后续实验明确证明需要异构图，再单独升级。

---

# 18. Station 如何进入模型

当前第一版倾向：

> Station 暂不作为独立图节点。

一个候选 Plan 中，Operation 节点应能够知道：

```text
当前被分配到哪个局部Station
Station的order
必要时的Station capability信息
```

但是必须避免：

```text
把字符串 OP20 作为具有全局固定含义的类别 embedding
```

因为 OP20 只具有当前零件内部的局部意义。

未来跨零件表示时，应优先考虑：

```text
local station order
station capability
其它局部station属性
```

而不是固定全局 OP 标签。

是否最终使用 Operation + Station 异构图，是后续架构选择，目前不是第一版目标。

---

# 19. Operation 几何编码仍属于待讨论问题

虽然 Geometry 已经完成提取，但：

> 如何把不同类型 Geometry 转换成统一神经网络输入，目前尚未最终确定。

例如：

```text
圆孔           -> center + radius
BEND           -> line endpoints
非圆形冲孔     -> 可变长度 LINE / ARC chain
BRIDGE         -> footprint
```

不能因为模型需要固定长度输入，就擅自丢弃重要几何信息。

当前可能的方向包括：

1. 提取固定长度人工几何描述量；
2. 为复杂轮廓设计单独 geometry encoder；
3. 将部分几何主要用于构造边，而不是直接作为节点原始特征。

第一版应优先简单、可解释、可验证的实现。

---

# 20. 候选完整方案生成

在基础数据与硬约束建立以后，需要产生多个：

\[
P_1,P_2,\ldots,P_m
\]

完整可行方案。

候选方案可以通过：

```text
规则
CP-SAT
约束搜索
其它离散优化方法
```

生成。

所有进入评价模型的方案首先必须满足硬约束。

因此模型的任务不是判断：

```text
“这个明显违反前置关系的方案是否很差”
```

而应该重点评价：

```text
“两个都能够合法加工的方案，哪个整体更合理”
```

---

# 21. 候选方案差异

候选 Plan 不应只依赖随机修改单个 Operation。

一个零件可能有数百个加工动作，如果每个方案只改变一个节点：

> 差异信号可能过弱，也不足以反映完整方案组合质量。

后续候选方案应考虑：

```text
单动作局部变化
相关Operation小组变化
局部区域变化
工艺链变化
某一道Station内部重组
较大范围完整方案变化
```

但这些变化必须始终满足硬约束。

候选方案生成方法尚未最终确定。

---

# 22. 专家方案与监督数据

当前：

\[
P_{expert}
\]

是重要参考方案。

但不应该直接构造：

```text
expert plan = positive
all other plans = negative
```

因为可能存在多个合理完整方案。

Scheme B 更适合采用专家方案比较。

例如比较：

\[
P_A
\]

与：

\[
P_B
\]

专家标签可以是：

```text
A_BETTER
B_BETTER
EQUIVALENT
UNCERTAIN
```

训练目标是学习类似：

\[
Q(P_A)>Q(P_B)
\]

的偏好关系。

---

# 23. 当前最大未解决风险之一：专家标注成本

一张零件可能包含数百个 Operation。

如果生成大量候选方案并要求专家逐一比较：

> 专家标注工作量可能不可接受。

因此 Scheme B 的一个关键可行性问题是：

\[
\boxed{
能否用较少的专家工作量获得稳定、有信息量的方案偏好监督
}
\]

该问题目前尚未解决。

未来可能考虑：

```text
只呈现方案差异
候选方案多样性筛选
避免全排列pairwise comparison
主动学习
少量高信息量方案比较
重复样本验证专家一致性
```

当前不要提前实现复杂标注系统。

---

# 24. 完整研究流程

当前项目全流程定义为：

```text
二维 / 三维 CAD与专家工艺
        ↓
提取 Operation / Geometry / Station
        ↓
静态数据校验
        ↓
构建基础图 G0
        ↓
建立硬约束
        ↓
定义可行域
        ↓
生成多个完整可行 Plan
        ↓
将Plan状态加入基础图
        ↓
方案评价模型
        ↓
方案排序
        ↓
Top-K方案
        ↓
与专家参考方案比较
        ↓
分析差异及其工艺意义
```

这条完整流程才是项目目标。

任何局部开发任务都应该能够解释：

> 它在上述流程中处于哪一环，并服务于什么目的。

---

# 25. 最终研究问题

当前 Scheme B 真正想验证的是：

> 在明确硬工艺约束已经能够定义合法工序分配可行域的前提下，利用加工动作之间的工艺关系、CAD关系和二维空间关系，是否能够学习多个合法完整工艺方案之间的整体质量差异？

换句话说：

\[
\boxed{
GNN是否能够学习人工规则难以直接表达的“完整方案组合质量”
}
\]

而不是：

\[
\boxed{
GNN是否能够记住专家把某类Operation放在哪个OP
}
\]

---

# 26. 当前项目状态

## 已完成

P001 的 v0.1 静态数据已经完成，包括：

```text
Operation
Geometry
Station
surface_side
process_group_id
required_predecessor_ids
cad_parent_operation_id
expert_station
```

当前无需继续无目的提取额外二维/三维 CAD 信息。

---

## 当前任务

当前需要讨论并确定：

```text
1. Operation Node最终有哪些输入属性
2. 不同Geometry如何转为节点特征
3. Graph 0具体表示
4. Graph 1关系与边类型
5. Graph 2二维几何邻接规则
6. Plan状态如何附加到基础图
```

在这些问题确定以前：

> 不应开始实现完整 GNN 训练系统。

---

## 第一阶段建议开发任务

在开始模型之前，可以先实现完全不改变研究假设的基础设施：

```text
Excel Loader
数据Schema
数据Validator
Operation对象
Geometry对象
Station对象
Plan对象
```

并为当前 P001 编写自动校验。

这些代码应该是后续所有实验共用的数据基础。

---

# 27. P001 数据建议自动校验项

至少验证：

```text
operation_id唯一
geometry_id引用合法或为空
station_id合法
operation_type属于定义集合
expert_station存在
expert_station允许对应operation_type
required_predecessor_ids引用存在
expert plan满足required predecessor
process_group引用一致
Geometry内部seq连续
非圆孔轮廓连续且闭合
ARC起终点与center/radius/sweep一致
part_id跨Sheet一致
```

数据校验程序不得修改 `data/raw/` 下的原始 Excel。

发现问题时只输出报告。

---

# 28. 原始数据保护规则

```text
data/raw/
```

中的文件是人工确认后的原始研究数据。

程序必须只读。

任何：

```text
归一化
特征工程
图结构
缓存
候选方案
模型输入
```

均应输出到：

```text
data/processed/
data/candidates/
outputs/
```

不得自动覆盖原始 Excel。

---

# 29. 跨零件扩展原则

未来增加：

```text
P002
P003
...
```

时，模型目标仍然是跨零件学习完整方案评价。

必须注意：

> 不同零件的 OP10 / OP20 / OP30 不具有固定全局语义。

未来训练集 / 验证集 / 测试集划分必须按：

```text
part_id
```

划分。

同一个零件产生的：

```text
多个候选Plan
多个pairwise比较
多个扰动方案
```

必须处在同一数据 split 中。

禁止把同一个零件的不同 Plan 分别放入 train 和 test，从而造成数据泄漏。

---

# 30. 当前明确不做的工作

除非后续项目决策发生变化，当前阶段不要开展：

```text
强化学习 RL
自动迭代策略网络
过程状态三维模型重建
碰撞仿真
复杂异构图
大规模GNN架构搜索
复杂CAD插件
生产级UI
数据库系统
完整Web服务
```

这些工作不是当前 feasibility validation 的必要条件。

---

# 31. 对代码代理 / Codex 的工作要求

在开始任何较大任务前：

1. 先阅读本文档；
2. 明确本次任务处于完整流程哪一步；
3. 不修改已经确认的工艺语义；
4. 不自行发明新的硬约束；
5. 不把 expert plan 当成唯一正确标签；
6. 不把任务简化为 Operation 的 OP 分类；
7. 不自行添加新的 CAD 数据需求；
8. 如果发现当前信息确实不足，应明确指出：
   - 缺什么；
   - 为什么缺；
   - 哪个具体算法或实验无法在没有该信息时进行；
   - 而不是直接增加字段。

如果新的实现需要改变本文档中的问题定义，应先停止实现并说明冲突。

---

# 32. Codex 第一次进入项目时应完成的理解检查

第一次进入本项目时，不要立即实现模型。

先阅读：

```text
PROJECT_SPEC.md
data/raw/钣金加工数据表_P001.xlsx
```

然后回答以下问题：

### Q1

本项目最终输出是什么？

正确理解应包含：

```text
完整的 Operation -> Station assignment Plan
```

---

### Q2

Operation 和 CAD Feature 有什么区别？

---

### Q3

为什么 OP20 不能被当成跨零件固定类别？

---

### Q4

硬约束层和 GNN 的职责分别是什么？

---

### Q5

Scheme B 的 GNN 输入和输出是什么？

应能够表达：

\[
Q(G_0,S,P)\rightarrow q
\]

---

### Q6

为什么不同候选 Plan 可以共享同一个基础图 \(G_0\)？

---

### Q7

`expert_station` 为什么不能直接被解释为唯一正确标签？

---

### Q8

Graph 0 / Graph 1 / Graph 2 分别想验证什么？

---

### Q9

当前是否需要重新从 CAD 提取更多二维或三维数据？

当前答案：

```text
不需要。
除非后续某个明确实验能够证明缺少某项具体信息。
```

只有上述理解基本正确后，才进入代码实现阶段。

---

# 33. 当前推荐的第一个代码里程碑

第一阶段不要训练 GNN。

目标：

```text
Milestone 1:
建立可靠的 P001 数据读取与校验层。
```

具体应能够：

```text
读取三个Sheet
构造Operation对象
构造Geometry对象
构造Station对象
恢复Operation与Geometry关联
恢复显式 predecessor
恢复 process group
检查Station capability
检查expert plan合法性
检查非圆孔轮廓闭合
生成结构化数据报告
```

该阶段完成以后，再讨论并实现：

```text
Milestone 2:
Graph 0 / Graph 1 / Graph 2 数据结构
```

然后再进入候选方案和模型实验。

---

# 34. 项目判断原则

整个项目需要始终保持以下原则：

> 先确认问题和数据语义，再实现算法。

> 已知硬规则交给规则系统，不要求神经网络重新学习。

> GNN 是否有价值必须通过 baseline 和消融实验验证，而不是预先假定。

> 一个复杂模型如果无法超过简单 baseline，就不应因为“更像科研”而保留。

> 当前研究的核心是完整合法方案之间的质量评价，而不是复现专家方案本身。

> 所有新的字段、边、约束和模型结构都必须能够说明其工艺意义或实验目的。

---

# 35. 2026-10-15 已确认规则修订（M3 v0.6）

用户已确认 CAD 制造先后方向为 **child → parent**。P001 的两处录入错误已由用户修正：O024.parent=O023，O047.parent=O046。

从独立的 M3 v0.6 入口起，全部有效 `cad_parent_operation_id` 按严格 Station 顺序执行：
`order(station(child)) < order(station(parent))`，命名为 FEATURE_TREE_PRECEDENCE（H7）。这是一条已明确确认的新工艺规则；§5.6 对未经确认 CAD 关系的推断禁令及旧版本行为保留。

H1–H6 定义不变。M1/M2 代码、Graph0/1/2 定义、历史候选与盲化产物不回写。CAD_PARENT 图边仍为 parent→child；约束中的 child→parent 不改变图边语义。

P2 按唯一有向先后 pair 计算一次并保留所有来源；新版 P1 排除已被任何硬先后直接或传递确定的 BEND pair。新增折弯方向分散分量按硬可行的 K_min/K_max 归一化，当前仅单独报告，权重未定。邻近折弯异形孔继续作 k=5 独立诊断，冲突类型集合和权重尚未启用。

实施边界、公式、参数及运行入口见 [M3 v0.6 规范](docs/MILESTONE_3_RULE_REVISION_V06.md)。旧版规范与报告仅代表各自历史输入和规则版本。
