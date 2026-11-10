# 人工提取与三维状态模块

全项目入口见 ../README.md；当前事实见 ../CURRENT_STATUS.md。

- `sources/<part>`：原始 DXF/STEP/历史 CAD 与来源指纹。
- `projects/<part>`：人工标注和修订，禁止当生成缓存删除。
- `extract_app`、`web`：标注编辑器。使用根入口 `project.py annotate --part P002` 或 `start_P002.ps1`。
- `three_d`、`scripts`：几何内核与构建；`configs`：来源配置、用户确认及显示配置。
- `baselines/P001`：迁移前独立回归期望与历史碰撞问题；不是当前模型输入替代品。
- `outputs/local_forming_v03/P001/review.html`：当前几何审核页，由核心复现命令生成。

最新三维状态细节见 THREE_D_STATE_PROGRESS_V01.md；原长 README 已归档至 ../docs/archive/information_extract_README_20261110.md。
当前覆盖 100 去料、4 中桥、46 翻孔端态；连续成形、守恒、全部工具/带对检测仍未完成。
