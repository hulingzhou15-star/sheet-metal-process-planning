"""Package the finite-strip material states and exact planar punch evidence."""
from pathlib import Path
import sys,json,gzip,base64,hashlib
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT));MODULE=ROOT/'information_extract'
from information_extract.scripts.check_whole_panel_collision import read,sha
from information_extract.three_d.band_material import state_transforms,station_fractions,map_points


def main():
    out=MODULE/'outputs/finite_material_v02/P001';data=read(out/'viewer_data.json')
    for path,h in data['model']['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Source changed')
    highlight_path=out/'feature_highlights.json'
    if not highlight_path.exists():
        from information_extract.scripts.build_finite_material_highlights import build
        build()
    highlight=read(highlight_path)
    for path,h in highlight['source_sha256'].items():
        if sha(Path(path))!=h:raise ValueError('Stale display highlights; rebuild build_finite_material_highlights.py')
    data['feature_highlights']=highlight
    model=data['model'];plan=next(p for p in data['plans'] if p['plan_id']=='NP2_001')
    orders={s['station_id']:s['order'] for s in data['stations']}
    fractions=station_fractions(model,plan['assignments'],orders,'OP40',.37);transforms=state_transforms(model,fractions)
    mid=next(s['material_id'] for s in data['material_states'] if s['plan_id']=='NP2_001' and s['station_id']=='OP40' and s['phase']=='entry')
    variant=next(v for v in data['variants'] if v['material_id']==mid);samples=[]
    for c in variant['components']:
        if c['kind']=='band' or c['region_id'] in ['P466_470','P408_682','P273_302']:
            points=data['geometries'][c['geometry_id']]['triangles'][0]
            samples.append({'component':c,'flat_points':points,'world_points':map_points(model,c,points,fractions,transforms)})
    (out/'mapping_samples.json').write_text(json.dumps(samples,ensure_ascii=False),encoding='utf-8')
    web=MODULE/'three_d/web';html=(web/'finite_material_review.html').read_text(encoding='utf-8')
    serialized=json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    compressed=gzip.compress(serialized,compresslevel=6,mtime=0)
    for name,content in [('DATA',base64.b64encode(compressed).decode('ascii')),
        ('CORE',(web/'core.js').read_text(encoding='utf-8')),('MOTION',(web/'motion_core.js').read_text(encoding='utf-8')),
        ('BAND',(web/'band_material_core.js').read_text(encoding='utf-8')),
        ('RENDERER',(web/'finite_material_renderer.js').read_text(encoding='utf-8')),
        ('APP',(web/'finite_material_review.js').read_text(encoding='utf-8'))]:html=html.replace('/*__'+name+'__*/',content)
    (out/'review.html').write_text(html,encoding='utf-8')
    (out/'viewer_pack.json').write_text(json.dumps({'encoding':'gzip+base64','lossless':True,
        'uncompressed_bytes':len(serialized),'compressed_bytes':len(compressed),'html_bytes':(out/'review.html').stat().st_size,
        'decoded_payload_sha256':hashlib.sha256(serialized).hexdigest(),
        'authoritative_geometry_data_sha256':sha(out/'viewer_data.json'),
        'display_highlights_sha256':sha(highlight_path)},indent=2),encoding='utf-8')
    model=data['model'];complete=[r for r in data['removal_coverage'] if r['status']=='FULL_SUPPORTED_DOMAIN']
    report='''# P001 有限宽折弯带＋逐序实体去料 v0.2

[打开工序检视 HTML](review.html)。可离线使用；原审核页和旧诊断页面未覆盖。

## 视图窗口（2026-11-10 更新）

二维与三维面板标题旁各有“新窗口查看”，可分别打开大窗口，保留平移、旋转和缩放；候选、工序、进度、去料状态仍跟随主页面。
点击“收回主页面”或直接关闭独立窗口会恢复原面板。主页面应保持打开；若浏览器拦截弹窗，允许此页面弹窗后重试。
三维“正上方视角”沿世界 +Z 轴向下观察；“正对孔所在板面”按当前孔的主要去料刚性板面法向对正。跨多个材料域时，以去料体积最大的刚性板面为准。
两项视角操作只改变相机，不改变折弯进度或几何。独立窗口使用同一画布与状态，不复制或重算候选。

## 银白配色与本序高亮（2026-11-10 更新）

零件改为接近参考截图的银白色与柔和光照，背景使用灰色渐变。浅蓝色仅标记当前候选分配到所选工序的特征，线宽为 1 CSS 像素；本序折弯带为浅蓝底色。
二维、三维及独立窗口同步当前序高亮，可用“高亮本序特征”开关关闭；手动选中的去料动作显示红色，点击“取消选中”清除。
本序折弯带使用实体着色及中心线，冲孔/预孔显示轮廓；翻孔、凸米和折桥目前只显示定位轮廓，不表示已建立局部成形实体。展开“本序特征”可查看具体动作与未覆盖项。
显示轮廓从二维来源采样，在材料中厚面与各材料域求交后分别映射到前表面，避免将跨面 C 形轮廓整体贴到单一板面。该轮廓仅用于显示，未改变布尔切孔实体或工序角度。
224 个动作中，59 个有折弯带、95 个有冲孔轮廓、52 个有局部特征定位轮廓，13 个缺二维位置，5 个支持域外冲孔保留未决。
`feature_highlights.json` 为显示辅助数据，独立记录源哈希；材料和检测几何仍在原 `viewer_data.json` 中。重新生成材料版本后，需重新运行 `build_finite_material_highlights.py`。
新验证 `test_finite_material_highlights.mjs` 覆盖全部 16 候选的逐序折弯集合、实际像素配色、高亮开关、跨面轮廓与独立窗口同步；证据 `highlight_browser_qa.json`。新增显示后的计时见 `performance_with_highlights.json`。

## 抗锯齿修正与画质选择（2026-11-10，当前版本）

此前为提高大窗口帧率，永久关闭了 WebGL 抗锯齿，并在交互时把画布限制到约 90 万像素。因此静止后虽恢复原生分辨率，仍缺少抗锯齿。这是显示画质回退；原 31 ms/帧的结果包含该画质取舍，不能当作恢复画质后的性能。

当前已重新启用 WebGL 抗锯齿，默认“清晰优先”，拖动、缩放和播放全程保持原生 DPR 分辨率。只有手动选择“流畅优先”，才会在交互时降低采样；停止后恢复原生分辨率。两种模式都保持抗锯齿。画质选择在三维面板标题处，独立窗口中同样可用。
保留 GPU 静态几何缓存、二维闭合边界缓存、不重画未变二维材料、按视图窗口调度动画帧以及不保留绘图缓冲等优化；未删减孔、三角形、材料状态或检测实体。

测试环境确认实际启用了 4× 多重采样，读回画面存在 3146 个部分覆盖边缘像素；默认模式在大窗口拖动过程中维持原生分辨率。性能检查现为 6 组，证据 `performance_qa.json`。
当前完整帧间隔见 `performance_interaction_antialias.json`：同一 Chromium / SwiftShader 软件渲染环境，大窗口 OP20 高亮拖动中位数约 125 ms（清晰优先）或 53 ms（流畅优先）。这是明确的画质/性能选择，不能承诺其他设备上的帧率。旧 `performance_interaction_after.json` 是关闭抗锯齿版本的历史记录。

## 历史：大窗口连续交互修正（2026-11-10，抗锯齿修正之前）

用户再次反馈独立三维窗口卡顿后，以 2488×1440 CSS 像素、DPR 1.25 的窗口重新测量连续拖动。原本约 1–3 ms 的数字只是事件处理/绘图提交耗时，不代表实际呈现帧间隔；原大窗口 OP20 高亮拖动的帧间隔中位数约 137 ms。

该历史版本关闭 WebGL 多重采样和保留绘图缓冲，移除仅用于接收鼠标事件的全尺寸透明画布。当前特征改为 1 CSS 像素浅蓝线；银白实体保留。
拖动、缩放和共同进度播放时，三维画布自动使用不超过约 90 万采样像素，松手或短暂停止输入后恢复原生显示分辨率；同步调整投影比例，视图不会因采样变化跳动。
此优化只影响交互时的像素采样，不删孔、不减三角形、不改变材料状态或检测实体。静止视图在浏览器原生 DPR 下重新绘制。
播放改用当前三维/二维独立窗口的动画帧计时；收回窗口时切换到主窗口，避免继续使用后台主页面的定时器。工序内所有折弯仍使用共同进度。
二维填充由重复栅格化全部三角形，改为缓存同一网格的闭合边界；全部 211 个组件几何均通过闭合与面积核对，面积误差低于 1e-5 mm²。共同进度变化时不再重绘未改变的二维材料。

新的完整帧间隔证据为 `performance_interaction_before.json`、`performance_interaction_after.json`，测试环境为 Chromium / SwiftShader 软件渲染，不能视为用户设备上的保证帧率。该历史版本大窗口三维和二维拖动约 31–34 ms/帧；详见原始记录。
复现命令为 `node information_extract/tests/benchmark_finite_material_interaction.mjs performance_interaction_after.json`。`test_finite_material_performance.mjs` 已扩大为 5 组，验证 GPU 坐标、原几何缓存、二维边界、交互采样恢复、视图比例和窗口收回。
请关闭旧独立窗口并刷新主 HTML，再重新打开独立窗口；旧页面中已经运行的脚本不会随文件更新自动替换。

## 前次性能优化（2026-11-10，事件耗时）

原页面每次进度更新都在 CPU 展开全部材料顶点、计算三角形法向并上传显卡，旋转也重复上传；二维图反复遍历所有三角形，独立窗口又放大了循环中的 DOM 查找开销。
新版将每个材料几何只上传一次，刚性位姿与带内映射改为显卡求值；二维材料路径按材料版本缓存，鼠标事件合并到视图所属窗口的绘制帧。
HTML 内的模型数据改为无损 gzip 内嵌，文件由约 46.2 MB 缩小到约 5.4 MB。`viewer_data.json` 与模型数据不变，孔、三角形和检测实体未删减。
同一测试环境下，相同材料版本的热态进度事件由约 0.67 秒降至约 1–3 毫秒；该数字不是不同电脑上的保证值。首次访问其他材料版本仍需创建缓存。
新增 GPU 回读检查对照实际显示着色器与 Python 坐标，覆盖 67 个组件的 201 个顶点；在约 0.0000784 mm 的回读分辨率下通过，平直和成品状态另行核验。
证据为 `performance_before.json`、`performance_after.json`、`performance_qa.json`、`viewer_pack.json`；测试命令为 `node information_extract/tests/test_finite_material_performance.mjs`，基准命令为 `node information_extract/tests/benchmark_finite_material_browser.mjs`。

## 实现结果

- 60 个支持板面、59 个折弯；53 个 90° 带宽为 0.4 mm，6 个小角度带宽约 0.385185185 mm。
- 6 个小角度使用各自 CAD 内外半径 0.8 / 1.6 mm，转动约 13.930263593°；未改写为 90° 或套用 0.4 mm 宽度。
- 59 个关节均从 CAD 前表面切点和已配准 DXF 展开双边界推导。60 个板面的完成态位姿与 CAD 坐标闭合，最大矩阵残差约 1.0e-10，仅为数值验证。
- 材料域分成 60 个刚性板面与 64 段折弯带，共 124 个组件。O025 的六段带保留切口间隙。平直参考态逐对检查无正体积重叠。
- 先按材料连接归属从母板扣除后代板面/带的展开足迹，避免把保留舌片既算入母板、又算入子板。随后依据原二维动作闭合区域做 OCCT 实体去料。
- 在当前支持材料参考中，49 个普通冲孔、46 个预孔完整覆盖，共 95 个真实去料事件。O196–O200 位于支持域外，未生成假孔。
- C 形冲孔不再要求与成品整块内环一一同形。O193 等按二维 C 形闭合区域真实切除，中间舌片保留并归属其独立材料区域。
- 预孔按自身 0.7 mm 等原始半径切除；未拿最终翻孔孔径或高度替代预孔实体。
- 接入原 16 个候选、7 道工序；按去料动作集合复用 19 个材料版本、211 个组件几何。候选顺序与分配未改。

## 工序语义

进入态使用此前已完成动作；共同加工过程的 59 个角度均在同一 station 进度求值，再沿材料树累计变换。
本序新增孔只在完成态进入几何；同序去料过程尚未建模。过程保留上一序去料实体不代表真实动作先后，也不构成先冲孔再折弯的假设。
固定去料材料坐标随刚性板面或带内映射运动，不再以成品孔线框代替切孔实体。

## 实体、显示与检测接口

刚性板面由精确二维圆/直线/圆弧闭合区域做布尔切孔，显示网格由该实体生成。
折弯带使用恒厚、有限宽几何映射，每条带沿宽度细分 24 格；显示使用细分后的边界三角形。
`band_material_solids.component_at_state` 对刚性部分返回变换后的切孔 B-rep，对折弯带从同一映射边界缝合并校验封闭实体。
90° 成品内侧尖角处的退化面被按缝合几何精度判退，不制造极细虚假面；构造或校验失败会抛出异常，不能被当作“无干涉”。
本次已检查长 90° 带和小角度带在 0 / 0.37 / 1 状态的检测实体封闭性及与解析体积的吻合。
95 个实际去料事件目前落在刚性域，部分跨不同刚性板面（如 O193）；没有声称已完成真实带内切孔的专项验收。

## 体积与模型局限

这是几何状态模型，尚非守恒的材料成形求解器。图纸宽度、恒厚 0.8 mm 和成品 CAD 圆角同时满足时：

- 90° 带的最终体积/平直体积约为 1.570796；小角度带约为 0.757440。
- 全部当前支持去料完成后，材料平直体积约 228321.428392 mm³，成品位姿的解析体积约 228934.719757 mm³，整体变化约 +0.268609%。
- 体积差属于当前映射的局限，未用调整板厚、隐藏相邻交叠或修改评分来消除。正式物理模型仍需兼容的应变/厚度或其他经确认的展开约定。
- 外边界仍沿用成品支持参考，不是已恢复原始毛坯；TRIM、翻孔高度、凸米、折桥、工具、装夹尚未完整建模。
- 4 个未连接材料区域仍待处理；支持域外的 5 个冲孔不按无风险处理。
- 本版没有重新扫描原 62 组碰撞，也不宣称连续运动安全或制造可行。

## 证据

- `model.json`：全部 59 个带参数、DXF 来源段、CAD 圆柱与切点证据、源哈希。
- `domain_audit.json`：材料归属组件与平直域重叠检查。
- `removal_coverage.json`：100 个冲孔/预孔动作的实体覆盖量、材料域和去料体积。
- `viewer_data.json`：工序材料版本、带孔边界和冻结候选分配。
- `python_tests.log`、`browser_qa.json`、`qa.json`：验证结果；`mapping_samples.json` 为 Python/浏览器一致性样本。

Python 验证包括：平直恒等、成品坐标闭合、带端连续、嵌套运动、共同进度、圆孔空缺随动、C 形去料与保留舌片、预孔半径、封闭带实体、阶段不提前去料、16 个候选 H1–H7 及保护输入不变。
浏览器验证包括：C 孔前后状态、全部候选切换、共同进度、67 个组件 Python/JS 映射一致、显示控制与局限提示。

## 复现

```powershell
.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_finite_material_model.py
.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_finite_material_states.py
.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_finite_material_highlights.py
.venv-m3/Scripts/python.exe -B -X utf8 information_extract/scripts/build_finite_material_review.py
.venv-m3/Scripts/python.exe -B -X utf8 -m unittest information_extract.tests.test_band_material -v
node information_extract/tests/test_finite_material_browser.mjs
node information_extract/tests/test_finite_material_windows.mjs
node information_extract/tests/test_finite_material_performance.mjs
node information_extract/tests/test_finite_material_highlights.mjs
```

浏览器一致性样本由同目录 `mapping_samples.json` 提供。源工作表、H7、评分、候选和 reference 未修改。
'''
    (out/'REPORT.md').write_text(report,encoding='utf-8');print(out/'review.html')


if __name__=='__main__':main()
