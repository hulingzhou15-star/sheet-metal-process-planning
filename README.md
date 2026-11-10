# 钣金完整工艺方案规划

给定零件事实，在硬约束下生成完整候选方案，结合图表示进行评价与审查；未来接入 GNN 评分排序。
当前已有无 reference 事实入口、合法候选、Graph0/1/2 和 P001 三维端态审查，**GNN 尚未实现**。

先读 [当前状态](CURRENT_STATUS.md)、[交接](HANDOFF.md) 和 [架构](docs/PROJECT_ARCHITECTURE_V1_DRAFT.md)。
Codex 固定规则见 [AGENTS.md](AGENTS.md)，工艺语义见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。

## 安装（Windows）

需要 Node.js/npm；Python 和依赖可由项目安装脚本安装到本机工作区：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

Python 3.12.12 位于 `.local/python`，环境 `.venv`；传递依赖锁定于 `requirements/windows-py312.lock`。
浏览器使用本机 Chrome/Edge，或先 `npx playwright install chromium`。可把 `configs/local.settings.example.json` 复制为 `.local/settings.json` 指定浏览器。

## 运行

从仓库根目录运行：

```powershell
.venv/Scripts/python.exe -B -X utf8 scripts/project.py doctor
.venv/Scripts/python.exe -B -X utf8 scripts/project.py status
.venv/Scripts/python.exe -B -X utf8 scripts/project.py reproduce --part P001 --target core
.venv/Scripts/python.exe -B -X utf8 scripts/project.py verify --part P001
.venv/Scripts/python.exe -B -X utf8 scripts/project.py review --part P001
.venv/Scripts/python.exe -B -X utf8 scripts/project.py annotate --part P002
```

仅重放规划基线可使用 `reproduce --target planning`。完整重建耗时较长，阶段日志位于 `outputs/run_logs`。
最新审核页位于 `information_extract/outputs/local_forming_v03/P001/review.html`；详情见同目录 REPORT.md。

## 文件职责

- `data/parts`：本件业务事实及来源索引；`data/supervision`：监督审计入口；`data/splits`：未来分件划分。
- `src`：事实、约束、候选、图与推理；datasets/models/training/evaluation 为未来学习预留。
- `information_extract`：人工标注、几何原始来源、三维实现、测试及派生输出。
- `baselines`：冻结候选、规划回归和历史比较身份；不是普通可删除缓存。
- `registry`：受保护输入、迁移映射和版本索引；`docs`：架构、决策、协议、历史说明。
- `outputs`：可重建产物；`.local`：本机环境与迁移恢复资料；二者不提交。

H7 子先父后，同序同时加工；reference 不进入正式推理。三维端态和板面对采样不等于连续制造安全证明。

## 换账号或上下文

在 Codex 打开这个仓库目录作为工作区，按 AGENTS → CURRENT_STATUS → HANDOFF 继续。
换账号不需要重复制本机项目；远端只能恢复已提交并推送的内容，聊天和未推送修改不会随 Git 同步。
项目远端为 https://github.com/hulingzhou15-star/sheet-metal-process-planning.git。

迁移已完成：[迁移与验证记录](docs/REPOSITORY_MIGRATION_20261110.md)。旧副本在 `.local/quarantine`，不进入 Git；用户已授权将整理后的核心基线提交并推送。
