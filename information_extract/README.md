# 人工提取与三维状态模块

全项目入口见 ../README.md；当前事实见 ../CURRENT_STATUS.md。

当前分配入口已停用H6、保留H7，P1已正式采用几何连接树带动材料量。运行 `.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.run_geometry_allocation` 评分，或使用`--mode generate`生成。完整命令、参数、验证和审查页见 [几何P1正式采用记录](GEOMETRIC_P1_ADOPTION_20261113.md)。冻结旧复现入口保留历史H6/P1；此前替代实验保留作历史证据。

- `sources/<part>`：原始 DXF/STEP/历史 CAD 与来源指纹。
- `projects/<part>`：人工标注和修订，禁止当生成缓存删除。
- `extract_app`、`web`：标注编辑器。使用根入口 `project.py annotate --part P002` 或 `start_P002.ps1`。
- `three_d`、`scripts`：几何内核与构建；`configs`：来源配置、用户确认及显示配置。
- `baselines/P001`：迁移前独立回归期望与历史碰撞问题；不是当前模型输入替代品。
- `outputs/local_forming_v03/P001/review.html`：当前几何审核页，由核心复现命令生成。

最新三维状态细节见 THREE_D_STATE_PROGRESS_V01.md；原长 README 已归档至 ../docs/archive/information_extract_README_20261110.md。
当前覆盖 100 去料、4 中桥、46 翻孔端态；连续成形、守恒、全部工具/带对检测仍未完成。

录入页已支持专家工序与工序能力直接编辑，使用方式与隔离说明见 [PROCESS_EDITOR_20261111.md](PROCESS_EDITOR_20261111.md)。

P001 已接入原业务表 224 动作及 211 份已有几何；在录入页勾选“仅看待补几何”补录剩余三项。接入、恢复与验证记录见 [P001_BUSINESS_EDITOR_IMPORT_20261111.md](P001_BUSINESS_EDITOR_IMPORT_20261111.md)。
