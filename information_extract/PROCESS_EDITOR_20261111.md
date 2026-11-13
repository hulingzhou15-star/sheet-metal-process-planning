# 录入页：专家分配与工序能力

后续用户修订优先：[七类能力与切边退出分配](PROCESS_CLASSES_AND_TRIM_20261111.md)。下文十类能力、PUNCH 待细分和固定切边确认是历史实现，已被替代。

2026-11-11，用户要求在各零件的录入页直接填写，不再手动编辑表格。

## 使用

1. 进入 P002/P003/P004 录入页（端口 8767/8768/8769），刷新页面。
2. 点击顶部“工序能力”，新增工序，填编号和正整数顺序，勾选允许的动作类型。顺序按数值解释，不按 OP 名称排序。
3. P002 可以“载入本件已有工序资料”，只读带入仓库原 Sheet3 十序；只在当前工序表为空时允许，防止覆盖编辑。旧泛类 PUNCH 不自动解释，需人工取消并选择具体类型。P003/P004 没有现成工序行时直接新增。
4. “应用工序设置”回到页面；选中动作，在“专家工序”下拉框指定，然后“应用标注修改”。也可直接点“保存标注”应用当前动作并保存整个项目。
5. 动作列表及左下角显示专家分配情况。“检查数据”单独展示参考分配问题。“导出检查包”可下载事实表及独立的 expert_assignments.json。

工序窗口未应用前可取消或 Escape 放弃；应用后可撤销、重做。空能力是待补，NONE 是明确无本项目动作，二者不同。已被专家分配/固定分配引用的工序不能删除；更改工序编号会同步这些引用。专家未分配可以留空，不猜补。

## 数据边界

- `operations[].expert_station` 保存人工参考，不自动写 Sheet4 固定分配或候选。
- `station_rows` 保持原 Sheet3 契约，`allowed_operation_types` 为明确类型。十类来自现有编辑器动作类型；曲线/直线折弯仍同属 BEND，未新增规划硬规则。
- JSON 项目备份保留完整编辑状态；导出事实工作簿继续不含专家列，单独提供 `expert_assignments.json`。正式推理不读取专家文件。
- 专家分配修改不失效几何缓存。工序能力与专家实际分配独立，不由参考答案自动生成能力。
- 工序检查包含编号、顺序、能力值；参考检查包含不存在工序、能力不相容、已填 CAD child→parent/明确前置的严格顺序。未填者保留未决，诊断不是完整专家 H1–H7 证明，也不阻止保存草稿。
- 本轮未扩展固定 TRIM 编辑、复杂几何适配或完整三维建模；既有 Sheet4 导入入口继续保留。

## 实现与验证

实现：`web/index.html`、`web/app.js`、`web/style.css`；`extract_app/process_config.py` 提供独立诊断与原 Sheet3 只读复用；server/model/exporter/workflow 接入保存现有契约、报告、隔离导出和几何指纹。

实际运行：

```powershell
.venv/Scripts/python.exe -B -X utf8 -m unittest information_extract.tests.test_process_config information_extract.tests.test_api information_extract.tests.test_exporter information_extract.tests.test_drawing_model information_extract.tests.test_annotation_workflow
node information_extract/tests/test_frontend.mjs
node information_extract/tests/test_ui_dialogs.mjs
node information_extract/tests/test_process_editor_browser.mjs
node information_extract/tests/test_editor_controls_browser.mjs
node information_extract/tests/test_editor_motion_browser.mjs
```

76 项 Python、24 前端、42 DOM 场景通过。三件新功能浏览器检查覆盖能力选择/NONE 互斥、重复顺序拦截、P002 旧资料载入取消、引用删除保护、工序改名、撤销重做、保存重载、来源绑定及固定分配不变；三件原按钮及 P002/P004 运动回归通过。浏览器保存用拦截副本，真实磁盘保存由临时 HTTP 项目测试；没有修改真实标注。截图 `outputs/process_editor_8767.png` 等已目视检查。

真实项目仍是 P002 rev10/135、P003 rev4/119、P004 rev8/77，专家和工序行未擅自填入。doctor 因既有人工修正与保护清单不同而失败；逐项核对 108 项中 105 一致，差异为 P003/P004 project.json 和用户删除的旧 P004_01.dxf。没有修改期望哈希。未运行全仓测试，未提交推送。
