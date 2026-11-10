# 工作区入口

先读 `CURRENT_STATUS.md` → `HANDOFF.md`，再按任务读取 `PROJECT_SPEC.md` 和模块文档。
用户已于 2026-11-10 批准总体架构及清理迁移；不需要再次为正常可逆迁移请求许可。
用户已于 2026-11-10 授权首次提交并推送到 origin/main，署名使用 GitHub 用户名和隐私邮箱，仅配置本仓库。后续提交/推送遵循当次用户授权。

## 固定边界

- H7 为 child → parent 严格先后；同序动作同时加工，不能增加“先冲后折”。
- 冻结候选、规则、评分、Graph0/1/2（Graph2 k=8）、reference 和人工标注不得因整理修改。
- `registry/protected_inputs.json` 记录受保护输入。哈希失配应查明原因，不能更新期望值让检查通过。
- 工艺语义以 `PROJECT_SPEC.md` 的有效修订为准；早期 M1 阶段禁令/目录描述已被后续用户授权及 `docs/decisions/20261110_repository_migration.md` 替代，不是永久禁止三维或 GNN。
- 所有几何模块实现、测试及资料放在 `information_extract`；全项目管理放根目录。
- GNN 尚未实现。真实监督、reference 与推理输入隔离，手工罚分和交叠不自动转成标签。
- 干涉诊断未覆盖连续局部成形、工具/装夹及折弯带全对，不能报告为制造安全证明。

## 执行与交接

- 项目根目录就是工作区。使用 `.venv/Scripts/python.exe`，不要从 Codex 账号缓存注入依赖。
- 统一命令：`scripts/project.py doctor|status|reproduce|verify|review`；先看 README 的完整运行方式。
- 新路径用仓库相对路径；禁止缺输入时回退桌面旧文件。历史原始路径只能作为来源记录。
- 不直接清空 outputs。删除前证明可重建，或存在已验证归档及恢复索引。
- 当前环境检查/测试只报告实际执行的范围，不把历史测试失败抹成全仓通过。
- 每个工作段更新 CURRENT_STATUS 和 HANDOFF；未实现接口、未验证事项、未提交修改须明确记录。
