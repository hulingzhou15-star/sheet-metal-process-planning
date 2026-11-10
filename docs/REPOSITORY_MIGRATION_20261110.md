# 仓库迁移完成记录

日期：2026-11-10。工作区仍为 `D:/sheet_process_assignment`。
本地 Git 已初始化为 main，origin 已指向用户提供的 GitHub 仓库。迁移验收时尚未提交、推送或设置作者身份；随后用户于同日授权首次发布，署名采用 GitHub 用户名和隐私邮箱。

## 实际完成

- 原始 DXF、STEP、WKF/DWG 等收拢到 `information_extract/sources/<part>`。P001 修正业务表及 P002/P003 人工工作簿进入 `data/parts`；人工标注项目和全部修订原样保留。
- 冻结 16 个完整方案进入 `baselines/P001/plans`；旧比较对象、身份映射和结构化记录进入 `baselines/planning_history`，没有提升为训练就绪标签。
- 在 `.runtime` 内另发现的 49 份历史记录、工作簿及一次性作者脚本已另行保存，见 `registry/additional_preserved_records.json`。其中普通孔浏览器下载含明确 QA 草稿，未视为用户确认。
- 最新有限宽模型不再依赖 `strip_model_probe.json` 或完整旧碰撞诊断。区间算法、网格转换、相对路径与来源校验移入明确的几何库模块；旧 62 个问题只作为独立回归案例保留。
- 环境统一为项目 `.venv` 和根 `node_modules`，锁定 Python/npm 依赖。运行时不引用 Codex 账号缓存或桌面源文件。首次环境建立复用了三个经 wheel RECORD 哈希核对的大型已安装包，其余包由锁文件同步；安装方式不要求旧环境继续存在。
- 新增统一入口 `scripts/project.py`、隔离复现 `scripts/cold_reproduce.py`、当前测试入口 `scripts/test_current.py`。
- 根 README、PROJECT_SPEC、CURRENT_STATUS、HANDOFF、AGENTS 分别承担使用、规范、现状、交接和固定规则。旧 M1 入口完整归档。未来 datasets/models/training/evaluation 等预留目录明确标注未实现。
- 当前审核页保留原配色、细线、抗锯齿和独立窗口；移除了指向已归档实验的失效链接。

## 独立复现证据

在新目录中仅复制代码和保留输入，排除旧 outputs、旧输入位置、旧环境和账号缓存，再使用独立项目环境完整执行。

| 验证 | 实际结果 |
|---|---|
| 冻结输入/规则/候选/人工标注 | 108 份受保护文件哈希一致 |
| 规划与图 | 224 动作、16 合法方案，Graph0/1/2 与动态属性一致 |
| 当前事实/规则/图/标注回归 | 368 项通过 |
| 几何实体见证测试 | 8 组通过 |
| 迁移前独立几何期望 | 224 状态、100 去料、50 局部端态、板面/折弯位姿、体积与网格数一致；数值容差 1e-5 |
| NP2_001 板面对诊断 | 83 态，每态 1770 对；1653 次内核调用，原 62 组均未再检出，局部新增交叠/内核失败均为 0 |
| 浏览器 | 6 组通过、页面错误 0；迁回主工作区并修复历史链接后再次通过 |
| Git 换行过滤 | 108 份保护文件及 374 份被测源码均可按原字节入库 |

冷重建曾发现测试依赖旧的 P002–P004 预览 HTML，以及一个遗漏的旧 DXF 路径。已改为明确生成预览、从配置解析输入，随后在另一空输出目录完整通过。没有复制旧预览来掩盖这些缺口。

机器证据：`registry/reproduction.json`。运行日志：`outputs/run_logs`。当前测试报告：`outputs/verification/current_tests.json`。几何对照：`information_extract/outputs/local_forming_v03/P001/migration_comparison.json`。
规划复现还输出 `outputs/planning/P001/static_graph.json` 和各方案 overlay。
只声明上述已执行范围；依赖旧阶段快照的历史测试没有被宣称为全仓通过。

## 清理与回退

48 个旧成果目录已压缩归档并逐成员读回校验，索引在 `registry/history_archives.json`。
迁移前第一方全量快照为 `.local/migration/before.zip`；另保存模块历史运行记录。

**自动审批拒绝了递归删除命令，未提供更具体的拒绝理由。因此最终采用可恢复迁移，没有绕过限制执行删除。**
14 项旧布局约 4.01 GB 已移至 `.local/quarantine`，包含旧输出、旧环境、重复依赖和旧输入位置。核验后的新产物已放回当前 outputs。
完整名单见 `registry/cleanup_completed.json`。隔离目录和归档都被 Git 忽略；它们仍占本机空间。

准备纳入仓库的保留集约 200 MB，主要是原始 CAD 与业务/历史基线；没有单文件超过 100 MiB。未执行 git add、commit 或 push。
未来拉取仓库可重建当前核心；本机未推送修改、聊天与 `.local` 历史包不会随 Git 恢复。

## 保留的研究边界

迁移没有改变 H7、同序同时加工、冻结评分或 reference。GNN 仍未实现。
三维仍是端态与采样诊断；连续成形、体积守恒、原始毛坯/全部切边、凸米、折弯带全对及工具/装夹仍待研究。不能据此宣称制造安全。
