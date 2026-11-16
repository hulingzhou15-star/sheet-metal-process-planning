# P003 逐序材料、折弯运动和干涉检查

## 2026-11-16 干涉反馈重新生成

用原 64 方案得到的 41 组确认碰撞折弯分配约束后续搜索。求解从保留的 NP2_036 附近探索合法折弯组合，每找到一组即按 J297 STEP 圆柱轴检查。24 等分初筛通过的路径加密到 96 等分。按折弯先后和同序组合衡量运动多样性，实际检查 21 组，5 组检出碰撞，16 种不同的折弯运动通过。达到 16 种目标后结束折弯路径搜索。

对这些运动补全冲孔、翻孔等分配，复用现行 P5 和次级评分。64 个完整方案通过筛查，P5 均为 0。选出每种折弯运动的最佳评分代表，共 16 个。NP2_036 保留在池中，入选的 P003_R024 与其折弯运动一致，采用评分更低的前段工序分配。P003_R016 排名第一，次级分为 4739/21060。

逐序复核前段材料时使用原 DXF 展开配准，完成材料后的运动使用 J297 STEP 圆柱轴。将该圆柱轴直接应用于原始毛坯会改变 J297 附近的展开位置，试算增加了约 11.43 mm³ 的相邻区交叠。两阶段采用各自的几何参照，逐采样报告记录实际使用的位姿。16 份逐序报告合计 6,304 个采样和 283,680 个材料区对×状态，正体积交叠和内核未决均为 0。筛查范围是刚性区采样及前段材料端态。

- [重新生成汇总和选择记录](outputs/p003_motion_regeneration_v01/summary.json)
- [64 个完整候选](outputs/p003_motion_regeneration_v01/pool.json)
- [入选 16 个方案和逐序报告入口](outputs/p003_motion_regeneration_v01/plans.json)
- [入选方案双视图回放](http://127.0.0.1:8770/p003_motion_regeneration_v01/stage/index.html)

展示页支持评分与工序汇总、二维和三维联动、两步折弯定位、1× 至 4× 播放及 STEP 对照。当前几何参照随材料状态切换。已在本机 HTTP 上跑完无头 Chromium 回归，并逐个核对压缩回放的 6,304 个采样与原始报告。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.regenerate_p003_candidates
```

## 2026-11-16 全 64 方案补查结论

剩余 48 个从原池按方案 ID 及动作分配核对后逐一检查。沿用 24 等分、同序同步运动和 45 对刚性材料区的检查口径。补查覆盖 3,731 个采样和 167,895 个区域对×状态，检出 1,816 个非相邻交叠对×状态、1,619 个相邻交叠对×状态。全池共 5,105 个采样、229,725 个区域对×状态，内核未决 0，64 个最终态均无正体积交叠，成品 STEP 逐份与源文件字节一致。

**筛查结论为 63 个淘汰、1 个保留。** 63 个方案均检出小折边 P409_410 与侧板 P70_295 的正体积交叠，其中 7 个还检出 P296_305 与 P70_295 交叠。逐一抽取 63 个方案各自的非相邻峰值，改用 J297 的 STEP 圆柱轴求交，63 个峰值样本均仍有交叠，范围约 17.70–56.06 mm³。

**唯一保留的 NP2_036** 在 OP30 加工 O069，在 OP50 加工 O120。首轮正体积量只出现在 OP30、OP40、OP50 的 J297 相邻材料区。用中面轴加密至 96 等分后，391 个完成材料采样仍只检出该相邻区交叠，最大 48.40 mm³；用 STEP 圆柱轴做同样 391 态的 45 对检测后，正体积交叠为 0。NP2_036 保留进入后续验证。本轮覆盖同序同步路径中的刚性材料区，完整折弯带、连续局部成形和模具夹具另属验证范围。

- [全池逐方案工序、峰值和换轴复核](outputs/p003_candidate_motion_remaining_v01/pool_interference.json)
- [剩余 48 个原轴检查汇总](outputs/p003_candidate_motion_remaining_v01/summary.json)
- [NP2_036 中面轴加密](outputs/p003_candidate_motion_remaining_v01/confirmation/NP2_036/midplane.json)
- [NP2_036 STEP 圆柱轴加密](outputs/p003_candidate_motion_remaining_v01/confirmation/NP2_036/step_cylinder.json)

从仓库根目录复现。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.check_p003_remaining_motion
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.summarize_p003_pool_motion
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.refine_p003_motion_case NP2_036 --axis midplane
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.refine_p003_motion_case NP2_036 --axis step_cylinder
```

## 首轮 16 方案逐序模型

2026-11-16完成P003入选16个候选。使用已核对的revision 9工艺事实、原候选分配和9个STEP实体关节。10个材料区按各方案实际工序运动。O069和O120共享J297，累计角度由0°升至135°，再回折至90°。每个工序的折弯动作使用共同进度。局部成形及去料只在完成端统一切换，形成后的材料随其载体运动。

## 逐序材料

实际重建了224条进入态／完成态、12种材料状态和36份新增BRep。36份新增实体均为可回读的单个有效实心体。110个非折弯动作均接入阶段切换，包含59个去料及51个STEP局部成形端态。29个翻孔预孔与所属翻孔分别按原工序切换。O109–O111沿DXF轮廓恢复折弯避让区，保留STEP舌片。O112以注册位置匹配实际STEP开槽，保留DXF圆孔与STEP轮廓差异记录。全部非折弯动作最迟在OP20完成，先于各计划首次折弯。OP70无动作，仅保存进入态及完成态。

## 实际检查结果

16个方案以每折弯工序24等分生成1374个进入、运动和完成采样，共检查61830个刚性区域对×状态。几何内核有效判定覆盖所有区域对，检出1247个正体积交叠对×状态，内核未决0。全部16个方案均有非相邻区域交叠。最后完成态与STEP刚性区域一致且均无正体积交叠，16份成品STEP与源文件字节一致。

| 区域对 | 出现方案数 | 正体积交叠对×状态 | 最大交叠体积 mm³ |
|---|---:|---:|---:|
| P409_410／P70_295，小折边和侧板，非相邻 | 16 | 539 | 56.098 |
| P296_305／P70_295，两侧板，非相邻 | 4 | 67 | 9.702 |
| P104_105／P298_299，J297相邻区域 | 16 | 641 | 48.403 |

最大非相邻交叠位于NP2_001的OP40共同进度87.5%，O120与O052同时加工，J297累计95.625°。单独将J297的转轴从中面交线移到STEP圆柱轴（X、Z各偏0.6 mm）重新求交，峰值仍为56.055 mm³。另一对非相邻侧板在NP2_008的OP50完成端以同一转轴变化复核，交叠由9.702变为9.196 mm³。两处交叠的Y范围均在J297弯曲带端部之外。J297相邻区的48.403 mm³在同一转轴变化的135°样本降为0，须视为对转轴近似敏感。

NP2_024是首轮交叠采样数最多的候选。独立空缓存48等分复查得到202态、306个正体积交叠对×状态，内核未决0。与首轮106个共有采样的分类及求交体积一致。首轮共470次内核计算，其中458次来自完整材料折弯检查。已核对177份输入、151份核心输出、各方案的120动作分配和实际事件边界。当前诊断使用刚性区域与中面铰链，转轴变更仅作敏感性检验。完整有限宽折弯带、工装、夹具和连续局部成形均在覆盖边界之外。候选分配和评分保留，本轮不依据近似诊断筛除或重排。

## 回放和复现

P001／P002风格的双视图回放显示120项特征、进入态与完成态、J297累计角度、交叠体积与区域位置，支持本序与全方案播放。在仓库根目录运行 `.venv/Scripts/python.exe -B -X utf8 -m http.server 8770 --bind 127.0.0.1 --directory information_extract/outputs` 可启动本地审查服务。

- [16方案逐序回放](http://127.0.0.1:8770/p003_candidate_motion_v01/stage/index.html)
- [区域交叠汇总](http://127.0.0.1:8770/p003_candidate_motion_v01/overlap_analysis.json)
- [独立转轴敏感性](http://127.0.0.1:8770/p003_motion_axis_sensitivity_v01/NP2_001/sensitivity.json)

### 2026-11-16 回放性能修复

原 OP20 完成态的二维绘制每次填充 98,780 个投影三角形。现在每个材料区预先投影为透明底图并缓存，底图分辨率为 6 像素/mm，特征定位线仍为矢量。浏览器使用预生成的 Float32 顶点及法向，刚性区轮廓随变换矩阵更新。材料、视角和高亮一致时跳过重绘，滑块合并快速输入并保留最新采样，明细表按需展开。16 份精简回放文件共约 0.56 MB，可追溯到原 17.08 MB 的诊断报告。

本机 HTTP 验证通过。无头 Chromium、SwiftShader、1680×1150、DPR 1.25 下，OP40 连续切换 22 个运动采样，二维重绘和 GPU 缓冲重传均为 0。两次验证的耗时中位数均为 39 ms，最大值分别为 65、125 ms。实际走通 16 个候选切换、工序播放、STEP 对照、交叠定位、资产 404 恢复及独立窗口旋转和返回。`node information_extract/tests/test_p003_stage_replay.mjs` 通过，另核对 16 方案 1,374 个采样和 47 份显示网格与原资产一致。本轮使用项目浏览器测试工具，无 computer-use 操作。

```powershell
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.check_p003_candidate_motion
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.summarize_p003_motion
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.build_p003_stage_review
.venv/Scripts/python.exe -B -X utf8 -m information_extract.scripts.check_p003_axis_sensitivity --plan NP2_001
```

15项相关测试通过，未跑全仓或使用computer-use。保护清单103项匹配和5项既有差异保持原记录。本轮未提交推送。
