# P003 O069 / O120 两步折弯（2026-11-12）

用户明确将原 O069 拆为两个加工动作，并确认二者共用几何。已通过 ProjectStore 保存到 P003 revision 6，共 120 条活动动作；旧 revision 5 仍可追溯。

|动作|名称|表面侧|本次折弯角|专家工序|以光面计增量|累计状态角|
|---|---|---|---|---|---|---|
|O069|光面一般折弯4|SMOOTH 光面|135°|OP40|+135°|135°|
|O120|光面一般折弯4|ROUGH 毛面|45°|OP50|−45°|90°|

O120 名称按用户原文保留，不能因名字含“光面”把表面侧自动改为 SMOOTH。`bend_angle_deg` 存各动作的正值幅度，方向由材料侧解释；最终90°不是 O069 的单步角度。两步累计说明保存在项目 user_decisions 中，用于追溯，尚未实现 P003 三维累计旋转适配。

两动作绑定相同的原 6 个 source_segment_ids，两边 shared_source_segment_ids 均显式确认全部6片段。O120.required_predecessor_ids=[O069]，为严格跨序依赖；不虚构 CAD 父子关系。O069 原有前置 O109/O110、O070 的 CAD 父 O069 保持不变。保存后逐对象对比，其余118条动作完全不变。未自动改写其它动作的依赖端点或将它们转挂至 O120。

修改脚本：`scripts/split_p003_o069.py`。只接受 P003 原119动作、无O120、O069光面90°的输入，重复执行会拒绝，不覆盖后续人工编辑。通过版本号乐观锁保存；修改前快照和验证结果在 `outputs/p003_two_step_bend_20261112`，规范修订历史在 `projects/P003/revisions`。清除失效的 geometry_batch；其它零件、工作簿、冻结输入、reference 和保护哈希期望均未修改。

专家提取脚本读取这条已确认决定，分别给 O069 / O120 标注 USER_CONFIRMED_BEND_STEP；保留 raw_matching_status 与原始逐段高亮，不伪装为自动唯一几何匹配。若已确认的角度、表面侧或专家序随后改变，重新提取会拒绝静默套用旧决定。

P003 最新专家结果：120动作，111个唯一完整几何匹配建议＋2个用户确认分步动作，尚待核7项（O070–O074、O099、O113）。OP70未配准的限制仍存在；不能报告120项全序对应完成。P004未改变。

## 本轮验证

- 标注校验0错误，当前已填写专家序的关系检查0问题。
- 11项针对性单元测试通过（拆分/原记录保留、重复执行保护、原色证据及角度变更保护等）。
- P003录入页只读浏览器验证：120动作；O069光面135°/OP40，O120毛面45°/OP50；共用确认均勾选；O120前置O069；零页面异常。
- P003/P004审查页浏览器检查通过，分别120/7和77/15条总数/待核。
- 首次录入页测试错误地预期“共用提示隐藏”，实际界面正确显示共用提示并已勾选确认；修正测试预期后通过，未因此改动界面行为。
- 未重跑全仓测试或三维建模；本轮及此前修改未提交/推送。

录入入口：<http://127.0.0.1:8768/>。已打开的旧页应先保存尚未保存的编辑，再重新读取服务器项目；版本冲突保护避免旧修订直接覆盖新记录。

复核命令（仓库根目录）：

```powershell
.venv/Scripts/python.exe -B -X utf8 -m unittest information_extract.tests.test_p003_two_step_bend information_extract.tests.test_full_expert_drawings information_extract.tests.test_expert_dxf_evidence
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.extract_full_expert_drawings --part P003
node information_extract/tests/test_p003_two_step_editor.mjs
node information_extract/tests/test_full_expert_browser.mjs
```

下一步继续处理P003其余7个待核项及OP70配准。后续三维必须将O120作为O069状态上的反向增量，不得将45°当最终状态，也不能对共用几何重复生成两份材料。
