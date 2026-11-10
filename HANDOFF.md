# 当前交接

更新：2026-11-10。**仓库清理迁移与核心复现已完成。**

用户已批准总体架构和迁移，并于 2026-11-10 进一步授权首次提交/推送。当前工作区为本地 Git main，origin 是
`https://github.com/hulingzhou15-star/sheet-metal-process-planning.git`。
本次发布使用 GitHub 用户名 hulingzhou15-star 和隐私邮箱，仅配置本仓库。接手时先检查 git status 与 origin/main 的同步情况；后续发布遵循当次用户授权。

## 从这里继续

1. 读 CURRENT_STATUS.md 与 docs/REPOSITORY_MIGRATION_20261110.md。
2. 运行 `.venv/Scripts/python.exe -B -X utf8 scripts/project.py doctor`，检查 `git status`。
3. 需要查看模型时执行同一入口 `review --part P001`；完整重建为 `reproduce --part P001 --target core`。
4. 新开发遵循根 PROJECT_SPEC 和 AGENTS。未来学习模块只有目录/接口约定，尚未实现 GNN。

## 本轮实际证据

- 108 份保护文件哈希一致，224 动作、16 合法方案、冻结图及动态属性不变。
- 368 项当前规划/标注回归、8 组实体测试、6 组浏览器检查通过。
- 空 outputs 隔离重建完整通过，几何数值与迁移前期望一致，NP2_001 指定范围诊断重跑通过。
- 已修复测试对旧预览 HTML、旧 DXF 位置的依赖；当前入口不读取桌面或 Codex 缓存。
- 当前 HTML 仍在 information_extract/outputs/local_forming_v03/P001/review.html，石墨灰黑/白色高亮/冷灰背景、独立窗口和抗锯齿保留。

## 本机回退与后续

旧布局约 4.01 GB 可恢复迁入 `.local/quarantine`；递归删除被自动审批拦截，因此没有物理删除。
旧成果包在 `.local/history`，迁移前第一方快照在 `.local/migration/before.zip`。这些本机内容不进入 Git。
当前核心不依赖旧布局；来源、清理与复现索引都在 registry。历史一次性脚本保留为记录，不作为当前执行入口。

本轮没有必须续做的迁移步骤。本次首次发布已获用户授权；生成产物需按 README 在本机重建。之后的研究任务是连续成形/守恒、扩大检测范围和多零件学习数据准备。
O196–O200 五孔、O027–O030 四桥已接入，不再列为对应关系未解决。H7 与同序同时加工不变。

## 首次发布已完成

2026-11-10，基线提交 `6f87b23` 已推送至 origin/main，包含 766 个文件。Git 作者仅在本仓库配置为 hulingzhou15-star / 239384485+hulingzhou15-star@users.noreply.github.com。环境、outputs 和本机恢复资料不上传；新克隆先安装再重建。
本次 Git 直连失败，使用本机现有 HTTP 代理 127.0.0.1:7897 的命令级配置成功推送，没有将代理写入仓库配置或源码；后续联网按当时本机网络设置处理。
发布前 doctor、108 份保护输入和 424 份保护/已测试文件的暂存字节校验通过。本轮没有重跑完整测试，历史完整验证范围仍以 registry/reproduction.json 为准。
