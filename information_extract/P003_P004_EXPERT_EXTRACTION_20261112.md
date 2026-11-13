# P003 / P004 专家工序图提取（2026-11-12）

## 当前结论

用户提供完整专家工序图后，已生成逐动作证据与审查页；没有批量回填录入页、工作簿或 reference。提取范围只包括已保存动作绑定的几何，不将专家图额外实体自动新增为动作。

|零件|实际专家来源（相对 information_extract）|标注修订/动作数|唯一完整高亮建议|待核|
|---|---|---|---|---|
|P003|sources/P003/originals/P003_FULL.dxf|5 / 119|111|8|
|P004|sources/P004/originals/P004_ALL.dxf|9 / 77|62|15|

P004 实际文件名为 P004_ALL.dxf，没有改名为用户所称的 P004_FULL.dxf。P003 OP70 校平（整形）未可靠配准，故 111 项唯一匹配仅限已配准工序，不能称为全七序唯一对应。P004 七道正式工序均已配准。

## 待核动作

P003：

- O069 光面一般折弯4：OP40 高亮匹配 3/6，OP50 为 6/6，不按首次出现或完整程度擅自决定工序。
- O070–O074：未找到彩色高亮匹配。
- O099 毛面米凸18、O113 圆孔7：OP10 与 OP20 均完整高亮。
- OP70 当前圆孔锚点配准无候选。额外扩大标题相对检索窗口至 x[-1600,100]、y[-1200,1600]，878 个图元仍无圆孔锚点配准候选；这不是不存在相关特征的证明。需采用其它基准或人工定位。
- “2/7 后攻牙”是附加工序视图，保留显示但不作为正式分配候选。

P004：

- O002、O008–O012、O055–O061 共 13 个折弯均在 OP60 匹配到 1/2 条高亮线。局部对照显示展开绑定双线而专家图仅一条匹配线为彩色，保留 OP60 证据但暂不算完整确认。
- O077 光面刻印1：OP30 高亮匹配 4/5。
- O013 光面一般折弯23：8 个绑定片段未找到高亮匹配。
- 已提取建议存在 6 对 CAD 子→父同在 OP20：O016→O015、O020→O019、O022→O021、O024→O023、O030→O029、O034→O033。与 H7 严格先后冲突，未改父子关系、未引入同序内部先后。

## 方法与限制

实现：`scripts/extract_full_expert_drawings.py`，复用 `extract_p002_expert_dxf.py` 的原始颜色、基础图元及圆孔锚点配准。使用修订后的展开图加载器和当前项目绑定，检查源 SHA256 一致；输入文件及依赖哈希写入 assignments_draft.json，输出前再次核对。

配准只使用单位比例的平移、直角旋转/镜像；镜像只对齐坐标，不修改材料侧。按 LINE 端点、ARC 端点及中点、CIRCLE 圆心半径进行 0.0001 mm 量化匹配。配准须有至少 20 个匹配，且领先第二名至少 10 个；不支持的视图不参与分配。灰/白/黑视为中性颜色，其它原色作为高亮证据保留。

严格匹配局限：折后投影、圆角差异、曲线及仅部分轮廓着色可能导致未匹配；不代表动作不存在。原文件还含未用于匹配的 SPLINE、ELLIPSE、HATCH、尺寸标注等实体，逐类计数保留在输出。没有进行完整 H1–H7、工序能力或“不晚于”约束审计；本轮关系诊断仅检查双方已有提取建议的 CAD 父子严格先后。P003 的零冲突只对该范围成立。

## 查看与复现

- P003：<http://127.0.0.1:8770/p003_expert_dxf_v01/review.html>
- P004：<http://127.0.0.1:8770/p004_expert_dxf_v01/review.html>
- 输出目录为 `outputs/p003_expert_dxf_v01`、`outputs/p004_expert_dxf_v01`，各含 review.html、assignments_draft.json、evidence.json、REPORT.md。生成产物不作为人工基线。

从仓库根目录运行：

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.extract_full_expert_drawings --part P003
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.extract_full_expert_drawings --part P004
.venv/Scripts/python.exe -B -X utf8 -m http.server 8770 --bind 127.0.0.1 --directory information_extract/outputs
```

实际验证：两零件提取成功、输入哈希检查通过；`test_full_expert_drawings` 与 `test_expert_dxf_evidence` 合计 9 项单元测试通过；`node information_extract/tests/test_full_expert_browser.mjs` 验证两页动作/待核数量、筛选、局部/整体对照及零页面异常通过，两页截图已目视检查。未重跑全仓测试、doctor 或三维检查。此前保护输入差异未通过更新期望哈希消除。本轮及先前工作区修改未提交/推送。

下一步：先定位 P003 OP70 与 O070–O074，再逐项审查多序高亮、P004 单线/双线证据及六对 H7 冲突；审核后的专家分配方可回填。P002 已确认的复合动作语义不自动套用到这两件。
