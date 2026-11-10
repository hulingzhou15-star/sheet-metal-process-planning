# 学习模块预留契约

状态：接口设计，尚无已实现评分模型、训练数据集或 checkpoint。

复用现有 `src.data` 的零件事实与 `src.graph` 的 StaticPartGraph、PlanOverlay。
一个样本对应同一零件的一套完整合法方案，输出一个越高越偏好的分数。
`TensorAdapter(PartFacts, StaticPartGraph, PlanOverlay) -> ModelInput` 不接收标签。
`PlanScorer(ModelInput) -> score` 不读 reference、不改变硬约束或候选分配。

真实 PreferenceRecord 独立记录 part_id、plan_a_hash、plan_b_hash、A/B/C/D 答案及来源。
人工答案只有在候选身份、盲号和回答来源核清后才能进入监督；C/D 的处理遵循评价协议。
按零件划分数据，预处理仅拟合训练集。P001 已参与开发，不宣称为未见测试件。

| 位置 | 后续职责 |
|---|---|
| src/datasets | 来源审计、split、无标签 tensor adapter |
| src/models | 简单基线与 Graph0/1/2 统一评分接口 |
| src/training | 共享评分器、pairwise loss、checkpoint、训练记录 |
| src/evaluation | 生成覆盖、偏好排序与 Top-K 分开评价 |
| configs/learning | 版本化特征、模型和训练配置 |
| data/splits | 按零件的划分清单 |
| experiments | 可复用实验定义，不放生成产物 |
| registry/models | 模型哈希、特征版本、训练零件、预处理及局限 |

实现前不提前引入深度学习框架或空模型代码。模型架构和超参数由实际实验决定。
