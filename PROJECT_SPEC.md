# 钣金完整工艺方案规划：现行规范入口

更新：2026-11-10。此次变更整理规范入口和阶段状态，不改变工艺规则、冻结候选、评分、reference 或图定义。

## 问题定义与不变量

项目采用 Scheme B：给定零件的具体加工动作、二维材料坐标事实、本件工序顺序及加工能力，生成合法的完整 Operation → Station 方案，再评价和排序。
Operation 是决策单元；最终几何 Feature 可能对应预孔和翻孔等多个动作。OP20 等名称仅在零件内部有序，不是跨零件全局类别。

- H1–H6 的正式定义见 [约束规范](docs/MILESTONE_3_CONSTRAINT_SPEC.md)。
- H7 使用已确认的 child → parent 严格先后，见 [v0.6 修订](docs/MILESTONE_3_RULE_REVISION_V06.md)。不重新推翻此规则。
- 同序动作同时加工，不假设序内先冲孔再折弯。任何新增硬规则须有明确用户确认。
- PRE_PUNCH 与 FLANGING 是独立动作；已确认的前置关系严格按 station.order 判断。
- surface_side 是展开材料两面的工艺语义，不从最终世界坐标法向反推。
- expert_station 是参考方案，不是唯一最优答案；正式推理不读取 reference 或评审答案。
- Graph0/1/2 静态图与 PlanOverlay 分离，Graph2 k=8；不得因另一个诊断的邻域参数改变冻结定义。
- 人工罚分、P5、距离近、未决几何交叠均不能自动作为学习标签或新硬规则。

## 规范与现状的分工

| 主题 | 有效依据 |
|---|---|
| 原始字段、二维几何/圆弧方向、Station、Feature/Operation 等详细语义 | [原始规范记录](docs/archive/PROJECT_SPEC_pre_migration_20261110.md) 第 1–25、27–29、34 节，依后续明确修订解释 |
| 冻结图结构和动态属性 | [图规范](docs/MILESTONE_2_GRAPH_SPEC.md) |
| 硬约束及 H7 修订 | 上述约束规范和 v0.6 修订；修订优先于早期未确认表述 |
| 无 reference 推理、监督隔离、学习目标 | [当前训练与推理计划](docs/REFERENCE_FREE_INFERENCE_AND_TRAINING_PLAN.md) |
| 总体架构和模块边界 | [架构](docs/PROJECT_ARCHITECTURE_V1_DRAFT.md)、[迁移决策](docs/decisions/20261110_repository_migration.md) |
| 当前实现、验证与限制 | [CURRENT_STATUS](CURRENT_STATUS.md)；这里不重复维护阶段进度 |
| 下一任务与未完成事项 | [HANDOFF](HANDOFF.md) |

原始规范的“当前第一阶段只做读取”“暂不开展三维”和 M1 目录等是当时的阶段安排，已被后续用户授权取代，不再约束当前阶段。
原文完整归档，数据语义与规则修订没有因归档丢失。新事实仍需明确确认，不能从日期新旧自行推断规则变更。

## 输入与复现

P001 人工修正业务事实为 `data/parts/P001/钣金加工数据表_P001.xlsx`，保留原字节。
P001–P004 标注与修订位于 `information_extract/projects`，图纸与 STEP 位于 `information_extract/sources`。
P001 不完整标注项目不能代替当前修正工作簿。其它零件的人工工作簿与标注来源都保留，不能假设所有人工修订已经回写上游。
冻结的 16 个方案位于 `baselines/P001/plans`；保留其分配与身份，不以重新随机生成替代。

生成物不得覆盖人工来源。权威输入及冻结代码由 registry/protected_inputs.json 核对。
完整几何复现的输入不包含专家 reference；原图纸包含工艺视图的开发来源应如实记录，不能宣称 P001 未参与开发。

## 三维与学习边界

三维代码及模块文件集中于 information_extract；几何诊断消费候选，不直接回写候选或评分。
当前是逐序几何端态和采样诊断，连续成形、体积守恒、原始毛坯/全部切边、工具与装夹尚未完整建模。
同序过程用进入态材料，完成态统一切换；未知轨迹不能当已验证过程。
GNN 尚未实现，未来学习对象为完整合法方案的评分，而不是对动作预测全局 OP 类别。
