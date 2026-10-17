<!-- owner: agent -->
# L0 → L1 → 测试：覆盖表

> **agent 维护**（L0 本身在 `spec/*.md` 里，由用户手写、agent 只读）。
> 规则见 `README.md`，流程见 `../docs/10-文档分层与协作规范.md`。
>
> `l0_sha1` 是上一次核对时该 L0 文件的内容哈希。
> `python tools\pdt\pdt_spec.py check` 在两者不一致时提醒"决定改了，L1/测试要跟进"，
> 跟进完用 `python tools\pdt\pdt_spec.py sync` 刷新。
>
> 状态取值：`已覆盖`（有测试钉住）/ `仅文档`（只体现在文档与实现里，没有独立断言）/ `待补`（缺口，check 会报）。

## spec/node_info.md
l0_sha1: db1b7ab201371f2eff7bc5d011940ba5e06bc3d9

| L0 条目 | L1 章节 | 测试 | 状态 |
| --- | --- | --- | --- |
| MMB-1 | docs/manual/operators/node_info.md#mmb-1-只读不改场景不改选择不留垃圾 | gui_mmb_node_info.py, test_mmb_node_info.py | 已覆盖 |
| MMB-2 | docs/manual/operators/node_info.md#mmb-2-命中与编辑器自身一致, docs/09-MMB节点信息.md#58-命中判定节点空间-vs-视图空间中键点不准的真凶 | gui_mmb_node_info.py, test_mmb_node_info.py | 已覆盖 |
| MMB-3 | docs/manual/operators/node_info.md#mmb-3-折叠节点仍然可读 | gui_mmb_node_info.py, test_mmb_node_info.py | 已覆盖 |
| MMB-4 | docs/manual/operators/node_info.md#mmb-4-帧节点不抢命中 | gui_mmb_node_info.py, test_mmb_node_info.py | 已覆盖 |
| MMB-5 | docs/manual/operators/node_info.md#mmb-5-信息内容与新增属性, docs/manual/operators/node_info.md#信息块里有什么 | test_mmb_node_info.py, gui_mmb_node_info.py | 已覆盖 |
| MMB-6 | docs/manual/operators/node_info.md#mmb-6-只有几何输入的节点默认不显示 | test_mmb_node_info.py | 已覆盖 |
| MMB-7 | docs/manual/operators/node_info.md#mmb-7-手势按住显示松开消失没有-pin | gui_mmb_node_info.py, test_mmb_node_info.py | 已覆盖 |
| MMB-8 | docs/manual/operators/node_info.md#mmb-8-不绑快捷键放一条未绑定条目键由用户在-keymap-里指定 | gui_mmb_node_info.py, test_mmb_node_info.py | 已覆盖 |
| MMB-9 | docs/manual/operators/node_info.md#mmb-9-失败可见不静默 | test_mmb_node_info.py | 已覆盖 |
| MMB-10 | docs/manual/operators/node_info.md#mmb-10-只报告当前编辑的树 | — | 仅文档 |

## 待确认

**无。** 上一轮列的四条（Pin / 4 px 拖动隐藏 / 包围盒与坐标哈希 / 属性表的"元素数 + 域"两列）
在 2026-09-13 的 L0 改版里都拍了：前两条从实现里删掉，后两条不再出现
（属性表只写"名称 + 类型"，域改成**分组标题**）；同时给了版式参考图
`spec/node_info_ui.png`，已按它实现并用 `qa/mmb/mmb_ui_shot.py` 截图复核
（`qa/mmb/mmb_ui_shot_added.png` / `_added_scaled.png` / `_small.png`）。

之后 L0 又改了两处，都已跟进（L1 + 代码 + 测试）：

* **计数半是固定表格**：列出全部几何域（point cloud / instances / volume 都算，蜡笔先不管），
  没有的写 `0`、"不需要按是否有元素区分颜色"；下方 `0` 元素的属性不显示，
  网格物体只有曲线时那个空 `Mesh` 组件**不列属性**。→ `COMPONENT_LAYOUT` + `_component_lines()`，
  L1 的"上半/下半"两节与 §4。
* **"新增属性"只和第一个输入比**：中间的"与全部输入的并集比"版本已推翻，Join 会点亮后几路
  输入带来的属性。**"第一个"**按 `NodeLink.multi_input_sort_id` 最小取（编辑器里最上面那一路），
  不是**倒序**的 `links[0]`。→ `upstream_geometry_source()`，L1 的"高亮"一节与
  `docs/09-MMB节点信息.md` §5.10。

再之后是当前这一轮 L0，也已跟进（L1 + 代码 + 测试 + `docs/00-当前状态.md`）：

* **计数半只留 domain 子类**：去掉 `Mesh`/`Curve` 这类组件名（组件只决定换行），每个域一种
  颜色、数字比标签大一号；参考图 `spec/node_info_ui_v2.png`，其颜色与字形高已用
  `qa/mmb/mmb_v2_measure.py` 量出（见 `docs/09-MMB节点信息.md` §4）。→ `COMPONENT_LAYOUT` 带
  RNA 域 + `DOMAIN_SLOTS` + `DEFAULT_COLORS`，下半的属性组标题复用同一个 slot（同域同色，
  有测试钉住）。
* **颜色进了偏好**（MMB-8 第 3、4 项）：9 个 domain 颜色 + 属性色 + 新增属性色共 11 个取色器，
  收在 `layout.panel("pdt_mmb_info_colors")` 折叠分组里，另加一个 `Reset Colours`
  （`pdt.mmb_reset_colors`，在 `_CLASSES` 里）。默认配色是 agent 选的**和谐**配色，
  不是参考图的像素取样（用户："颜色不要按参考图精确走…使用和谐的配色作为默认"）。
* **新增属性用颜色而不是加粗**：实测 `blf` 没有可加载的粗体面（5.2 的 datafiles 只有 woff2、
  没有 TTF/OTF，`blf.load()` 读不了；只有 `C:\Windows\Fonts` 里有系统粗体），
  用户据此改选"单独的颜色偏好"。**L0 随后也把这句改成了**"用单独的颜色标出（默认 `#FFB433`，
  可在首选项里改）"——L0、L1、实现三方一致。

最后一版 L0（当前 `l0_sha1`）把参考图换成 `spec/node_info_ui_v3.png`，并复核了上面那条颜色说法。
v3 与 v2 的差异是用 `qa/mmb/mmb_v2_measure.py`（`$env:PDT_MMB_IMG` 指定路径）逐行量出来的，只动了
下半排版：组标题 41→28px（正文号）、标题与属性行 28→16px、两半之间 115→54px、属性条目之间
~36→~60px。用户只采纳**"组标题降到正文号"**，间距明确保持现状 → `font_sizes()` 从
`(12, 15, 19)` 改成 `(12, 15)`，`_panel_lines()` 的组标题改用 `body` 号；L1/L2/`00-当前状态.md`
与测试同步（新增断言：组标题字号 == 域标签字号，且都小于数字字号）。

再之后用户又改了一轮 MMB-8：**只保留"提醒文字"的措辞**（
`Assign one shortcut yourself: Preferences>Keymap>Operator Name`，`Operator Name` 换成实际名字，
"不需要其他提示信息"），并把边界里那句"加粗"改成"高亮"。跟进时发现了一个**真 bug**：
工厂设置下启用插件后，`Preferences → Keymap` 的搜索框**搜不到这个操作符**——那个框只筛
"键位表里**已有**的条目"（`rna_keymap_ui.draw_filtered`），而当时的做法是"一个字都不写"。
现在插件往**自己的 addon 键位表**注册**一条 `type=NONE` 的未绑定条目**（不绑键、不可派发，
只让操作符可搜、可改键），这就是 `pdt_geo_select` 一直以来的做法减去默认键；`binding_is_active()`
也随之只认**真的绑了键**的条目。证据：`qa/mmb/mmb_keymap_probe.py`（搜 `node info`/`pdt`
0 → 1 条，`unregister()` 正对照回 0）、`qa/mmb/mmb_keymap_startup_probe.py`（重启后条目仍在；
用户绑 `Alt+MMB` 后重启 `binding_is_active()` 仍为 True）。L1 的 MMB-8 一节、§5.5 第 7 条、
`00-当前状态.md` 与两个测试同步。

用户随后把这条纪律**写进 L0**（MMB-8 补一句）："keymap 新建未绑定条目、不写 default 表、
`unregister()` 不动用户绑的键"。三条都能逐条对到断言，L1 的 MMB-8 一节现在就是按这三条列的表：
① 无头断言注册项恰好一条且 `type == "NONE"`、真窗口断言 addon 表里恰好一条且未绑定；
② 真窗口断言 `our_items(default) == []` 与注册前后 keymap 条目数快照相等；
③ 真窗口断言 `unregister()` 后用户自己绑的那条（实测 `Alt+MMB`、`user_modified=True`，
`qa/mmb/mmb_binding_probe.py` 只读复核）逐字段不变。这一轮 `l0_sha1` 已随之刷新。

再之后（2026-09-14）用户指出"node info 似乎调用了 selection 插件的部分功能，感觉不合理"，
于是把求值器收进插件自己（新模块 `evaluate.py`，MMB-1 的机制不变、只是不再借别人的代码）：
借的那 5 个函数的闭包是 453 行（`qa/mmb/mmb_pdt_closure.py` 量出），自持版只留报告需要的部分。
护栏加在无头测试里：按 AST 断言**没有任何子模块 import `pdt_geo_select`**，
并把 `sys.modules["pdt_geo_select"]` 置 `None` 后重建面板状态，与可用时**逐字段相同**
（reason / components / attribute_groups / added）。顺带记录了一个探索结论（§8.2）：
Blender 自己的 Viewer node + 求值日志里有同样的数据，但 `viewer_path`、`ViewerPath`、
日志全部只读或不在 RNA 里，**Python 无法读取**，所以自己求值是唯一可行路线。

**本轮（2026-09-15）用户从另一台机器报的乱序**：下半属性表的域分组读成
`Face Corner / Face / Vertex / Edge`，与上半的 `Vertex / Edge / Face / Face Corner` 错位。
根因不在 L0 的措辞里，而在实现跟着 `mesh.attributes` 的**存储顺序**走。修法与证据见
`docs/00-当前状态.md` 的本轮块、`docs/09-MMB节点信息.md` §4"下半"、L1 的"下半"一节与
`qa/mmb/mmb_group_order_probe.py`；测试 `test_mmb_node_info.py` 144 → 149（带正对照）。

L0 随后把这条**写进了 `MMB-5`**（用户落笔，就接在"属性颜色统一"后面）：
"下方属性表按域分组，组顺序与上方一致。" 实现与 L1 早就按这个意思做了，所以这一轮
没有行为改动、只有措辞对齐：`l0_sha1` 已随之刷新（`pdt_spec.py sync`），
MMB-5 的覆盖状态仍是 `已覆盖`（同一条 L1 章节 + 同一批断言）。

## spec/pdt_selection.md
l0_sha1: a1b9271109f4d63fdbbf003a810faea2e56e65ab

| L0 条目 | L1 章节 | 测试 | 状态 |
| --- | --- | --- | --- |
| PDS-1 | docs/manual/reference/selection_syntax.md#1-求值模型一个累加器从左到右 | test_selection_patterns.py, test_manual_examples.py | 已覆盖 |
| PDS-2 | docs/manual/reference/selection_syntax.md#12-四个运算规范, docs/manual/reference/selection_syntax.md#34--与--的区别最容易踩 | test_selection_patterns.py | 已覆盖 |
| PDS-3 | docs/manual/reference/selection_syntax.md#2-词法什么算一个记号, docs/manual/reference/selection_syntax.md#3-记号详解 | test_selection_patterns.py, test_manual_examples.py | 已覆盖 |
| PDS-4 | docs/manual/reference/selection_syntax.md#4-错误error与警告warning | test_selection_parser.py, test_pdt_selection.py | 已覆盖 |
| PDS-5 | docs/manual/nodes/pdt_selection.md#语法错误时g1-安全失败 | test_pdt_selection.py | 已覆盖 |
| PDS-6 | docs/manual/nodes/pdt_selection.md#引用缺失时g2-警告但不失败 | test_selection_patterns.py, test_pdt_selection.py | 已覆盖 |
| PDS-7 | docs/manual/reference/selection_syntax.md#31-序号与区间 | test_manual_examples.py, test_pdt_selection.py | 已覆盖 |
| PDS-8 | docs/manual/nodes/pdt_selection.md#作用范围只在-domain-对应的组件上 | test_pdt_selection.py, test_selection_instances.py | 已覆盖 |
| PDS-9 | docs/manual/nodes/pdt_selection.md#domain元素种类 | test_pdt_selection.py, test_selection_instances.py | 已覆盖 |
| PDS-10 | docs/manual/reference/selection_syntax.md#14-组件与域的范围规范 | test_pdt_selection.py, test_selection_instances.py | 已覆盖 |
| PDS-11 | docs/manual/nodes/pdt_selection.md#限制 | — | 仅文档 |
| PDS-12 | docs/manual/reference/selection_syntax.md#36-prop超长选择 | test_pdt_selection.py | 已覆盖 |
| PDS-13 | docs/manual/index.md#g3-拾取器的预选只来自序号, docs/manual/index.md#g4-两个解析器对同一个字符串的判断一致 | test_selection_parser.py | 已覆盖 |

实现与实测记录：`docs/archive/07-曲线支持评估.md`（组件与域）、`docs/archive/08-实例支持评估.md`（实例域）。
本轮（2026-09-14）按 PDS-8/PDS-9 把 `Domain` 从"属性域菜单 socket"改成**元素种类节点属性**
（DNA `NodeGeometryPDTSelection` + `RNA_def_node_enum`，和 `Delete Geometry` 同一种做法），
并删掉 `Component` 输入：种类本身就说明作用在哪个组件上。旧文件不做迁移（用户决定）。

## spec/viewer_overlay.md
l0_sha1: 81d67d77581b7d2754c0c7f30189dd21b7565f10

| L0 条目 | L1 章节 | 测试 | 状态 |
| --- | --- | --- | --- |
| EVO-1 | docs/manual/operators/viewer_overlay.md#作用域与元素位置evo-1 | gui_viewer_overlay.py | 已覆盖 |
| EVO-2 | docs/manual/operators/viewer_overlay.md#面板evo-2 | gui_viewer_overlay.py, test_viewer_overlay.py | 已覆盖 |
| EVO-3 | docs/manual/operators/viewer_overlay.md#面板evo-2 | gui_viewer_overlay.py | 已覆盖 |
| EVO-4 | docs/manual/operators/viewer_overlay.md#旋转与矩阵evo-4--evo-5 | gui_viewer_overlay.py | 已覆盖 |
| EVO-5 | docs/manual/operators/viewer_overlay.md#旋转与矩阵evo-4--evo-5 | gui_viewer_overlay.py | 已覆盖 |
| EVO-6 | docs/manual/operators/viewer_overlay.md#规模上限evo-6 | gui_viewer_overlay.py | 已覆盖 |
| EVO-7 | docs/manual/operators/viewer_overlay.md#绘制样式evo-7 | gui_viewer_overlay.py | 已覆盖 |

实现与实测记录：`docs/11-Viewer属性可视化评估.md`（含 Python 原型截图与 `bf_draw` 构建成本实测）。
绘制在 C++ overlay 引擎里（`draw/engines/overlay/overlay_attribute_viewer.hh`），
所以验收判据是**真窗口截图里的像素计数**（`gui_viewer_overlay.py` 的 EVO-1…7 断言），
无头那一半（属性、默认值、枚举、DNA 往返）在 `test_viewer_overlay.py`。

**EVO-7 的覆盖**（用户 2026-09-14 把这三条从"实现时定下"升进 L0，随后补了测试）：
`gui_viewer_overlay.py` 的 `phase_style` 两段 ——

* **不做深度遮挡**：在相机与 viewer 对象之间放一个不透明立方体，先用对照证明它确实挡住了对象
  （颜色 overlay 的彩色像素 5434 → 1587 = 什么都不画的水平），再断言引导线在遮挡物后面的差分
  像素数仍是 2024（与没有遮挡物时逐像素同量）。把 `DRW_STATE_DEPTH_ALWAYS` 换成深度测试这条会立刻红。
* **退化值与起点小点**：`|v| = 0` 且 `Vector Scale = 0.4` 时，改缩放的差分 ≈ 0（实测 1080 px =
  该场景自身的噪声地板，限 3240）。实现侧也不依赖驱动：`append_vector_line` 在"方向 × 缩放 = 0"
  时跳过，不生成零长线段。


## spec/selection_pick.md
l0_sha1: b6efc3c1fd6a6188fedc9c658c9e04a04c086221

| L0 条目 | L1 章节 | 测试 | 状态 |
| --- | --- | --- | --- |
| PICK-1 | docs/manual/operators/selection_pick.md#打开, docs/manual/operators/selection_pick.md#两个-pie-menu-的排布, docs/manual/operators/selection_pick.md#参数写到哪, docs/manual/operators/selection_pick.md#元素种类element-kind | test_pick_operator.py, gui_pick_modal.py | 已覆盖 |
| PICK-2 | docs/manual/operators/selection_pick.md#预选只认序号保证-g3, docs/manual/index.md#g3-拾取器的预选只来自序号 | test_selection_parser.py, test_pick_operator.py | 已覆盖 |
| PICK-3 | docs/manual/operators/selection_pick.md#元素种类element-kind, docs/manual/operators/selection_pick.md#限制 | test_pick_operator.py | 已覆盖 |
| PICK-4 | docs/manual/operators/selection_pick.md#打开, docs/manual/index.md#g5-绝不改写用户键位也不替用户决定快捷键 | test_pick_operator.py, gui_pick_modal.py | 已覆盖 |
| PICK-5 | docs/manual/operators/selection_pick.md#现场保护与异常处理 | test_pick_operator.py | 已覆盖 |
| PICK-6 | docs/manual/operators/selection_pick.md#现场保护与异常处理, docs/manual/operators/selection_pick.md#限制 | test_pick_operator.py | 已覆盖 |

## spec/branding.md
l0_sha1: 443973a425c5af00c69a3ed0ce0f1e6c76c68832

用户 2026-09-15 落笔（`BRAND-1…5`）：标识文本带版本号；标题栏只加后缀；官方版那边
只警告、不硬拒；警告走 Blender 原生报错通道。
**2026-10-18 用户拍板的三件事**（代码已落地，等 L0 落笔后本条与下表一起改成 slug 键）：
① 品牌版本号 = 发布 tag，从 `v0.1.0` 起（新增 BRAND-6）；② 三个占位 tag
`pdt-v0.1.0/0.2.0/0.3.0` 删除；③ 外来构建判定按**族前缀**，同族不同版本不报警（BRAND-4 收窄）。
L1 = `docs/manual/operators/build_branding.md`，
测试 = `test_branding.py`（BRAND-1..4，自己拉起本二进制 + 官方参考版做交叉验证）+
`gui_branding_title.py`（BRAND-5，真窗口用 Win32 读回标题）+
`version.ps1 -Action verify`（BRAND-6，发布时核对品牌 ↔ 两侧 tag）。

| slug | L1 章节 | 测试 | 状态 |
| --- | --- | --- | --- |
| one-brand-source | docs/manual/operators/build_branding.md#brand-1-分支给自己命名但不冒充上游的版本号 | test_branding.py | 已覆盖 |
| file-carries-brand | docs/manual/operators/build_branding.md#brand-2-本分支存的文件带品牌且自己读得回来 | test_branding.py | 已覆盖 |
| official-sees-brand | docs/manual/operators/build_branding.md#brand-3-文件里的标识能穿过官方版但改写会改署名 | test_branding.py | 已覆盖 |
| warn-foreign-build | docs/manual/operators/build_branding.md#brand-4-外来构建会被本分支提示按族判不按版本 | test_branding.py | 已覆盖 |
| window-title-brand | docs/manual/operators/build_branding.md#brand-5-标题栏的标识在真实窗口里成立 | gui_branding_title.py | 已覆盖 |
| version-follows-tag | docs/manual/operators/build_branding.md#brand-6-品牌里的版本号就是发布-tag | test_branding.py | 已覆盖 |

BRAND-6 的 tag 相等性本身由**发布流程**那一步核对：`tools/pdt/version.ps1 -Action verify`
（品牌 = fork 的 `pdt-v*` = 产物仓的 `v*`，不一致退 1）。它不是 `tests/` 下的文件，所以上表这一列
填 `test_branding.py` —— 那条测试保证的是另一半：二进制的品牌确实来自 header，`stamp` 之后
不需要改任何测试。

实现与实测记录：`docs/00-当前状态.md`（本轮块）、`docs/manual/operators/build_branding.md`
（§3 BRAND-3 的"官方改写会改署名"、§4 官方版看到什么，都是实测）。

**BRAND-4 的判据为什么不是"文件更新"**：先在实现里写成"别的构建 **且** 子版本更新"，
随后实测证明那条分支不可达也不必要——子版本比本二进制新的文件在读取阶段就被拒
（逐字节打补丁造 sub 46 → `Missing DNA block`）。2026-10-18 又收窄一次：品牌带上版本号之后，
精确比较会让**升级后打开自己的旧文件**误报，所以判据改成族前缀
（`STRPREFIX(build_hash, "PDT-GN")`）。现在的规则：族外的构建 → 警告；同族其它版本、
没署名的老文件 → 静默。`test_branding.py` 三个方向都有断言。

## spec/outliner_parent_drop.md
l0_sha1: 87ba9ae81e0d527759925d57ab6d6c2d7233d5ea

用户 2026-10-15 落笔（`OPD-1…9`，草稿由 agent 按 `docs/20-Outliner拖放父级-无inverse.md`
与两轮现场决定拟出、用户几乎逐字落盘）：Outliner 里拖动物体设父级，**默认档不写父级反矩阵**、
Alt 才写，两档都保持世界变换。L1 = `docs/manual/operators/outliner_parent_drop.md`，
测试 = `test_outliner_parent_drop.py`（headless：两档矩阵语义、重父级、成环、源码接线）。
**拖放事件本身只能真窗口验收**：`qa/outliner_parent/checklist.md`（判据表）+
`qa/outliner_parent/demo_scene.py`（场景），agent 驱动不了 `EVT_DATA_DRAGDROP`，
按用户 2026-10-15 的现场结论记为已通过。

| L0 条目 | L1 章节 | 测试 | 状态 |
| --- | --- | --- | --- |
| OPD-1 | docs/manual/operators/outliner_parent_drop.md#世界变换不变opd-1 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-2 | docs/manual/operators/outliner_parent_drop.md#默认档不写偏移-alt-档写偏移opd-2-opd-3 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-3 | docs/manual/operators/outliner_parent_drop.md#默认档不写偏移-alt-档写偏移opd-2-opd-3 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-4 | docs/manual/operators/outliner_parent_drop.md#与菜单同义opd-4 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-5 | docs/manual/operators/outliner_parent_drop.md#多选opd-5 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-6 | docs/manual/operators/outliner_parent_drop.md#成环与不可编辑opd-6-opd-7 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-7 | docs/manual/operators/outliner_parent_drop.md#成环与不可编辑opd-6-opd-7 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-8 | docs/manual/operators/outliner_parent_drop.md#模式无关opd-8 | test_outliner_parent_drop.py | 已覆盖 |
| OPD-9 | docs/manual/operators/outliner_parent_drop.md#目标必须是物体opd-9 | test_outliner_parent_drop.py | 已覆盖 |

**OPD-5 / OPD-7 / OPD-8 / OPD-9 的"已覆盖"是分层的**：headless 那条钉住的是**接线**
（每个拖动 ID 各走一遍、`BKE_id_is_editable` 分支、不按 `outlinevis` 分支、
父级只认物体行），**现场那半**（真拖一次多选、拖库链接物体、Scenes 模式、拖到集合行）
在 `qa/outliner_parent/checklist.md` 里由用户逐条做过。这两半合起来才算覆盖，
缺一半时状态要退回 `仅文档`。

## spec/pdt_wrangle.md
l0_sha1: b4350fa9bf30c7c4133ed9f9e727a66d8b3d1f92

`pdt_wrangle.md` 由用户维护：2026-09-26 落笔 `WR-1`…`WR-14`（草稿由 agent 按
`docs/14-Wrangle节点实施记录.md` §M1.7 的五个待定问题拟出、用户改了两处后落盘），
2026-09-30 又追加了三批（`WR-22`…`WR-24` 多组件 / `@P` / 菜单取值，`WR-26`…`WR-28` 作用范围，
`WR-29`…`WR-37` 多几何流与拓扑），并删除了 `WR-15` / `WR-20` / `WR-21` 三条——它们描述的
正是后来要实现的那些能力。L1 = `docs/manual/nodes/pdt_wrangle.md`；
节点侧测试 = `test_pdt_wrangle.py`（`W-1`…`W-4` 接口与持久化，`E-1`…`E-34` 逐元素语义），
引擎侧自测 = `wrangle_engine_selftest.cpp`（同目录；语言与类型检查、错误信息，零 Blender 依赖，
由 `tests/build_engine_selftest.ps1` 编译运行，`run_tests.ps1` 已把它纳入门禁）。
两个文件都在 `tests/` 下，所以"测试"列按条目各自列出——语言与错误信息那几条同时由两边断言。

| slug | L1 章节 | 测试 | 状态 |
| --- | --- | --- | --- |
| code-passthrough | docs/manual/nodes/pdt_wrangle.md#直通 | test_pdt_wrangle.py | 已覆盖 |
| code-error-output | docs/manual/nodes/pdt_wrangle.md#Code 报错 | test_pdt_wrangle.py | 已覆盖（编译错误与运行期错误都输出空几何；`E-6/9/10/13/14/15/18/19/21/29/47/48/57/61-65` 全按空断言） |
| elementwise-determinism | docs/manual/nodes/pdt_wrangle.md#逐元素独立 | test_pdt_wrangle.py | 已覆盖 |
| at-write | docs/manual/nodes/pdt_wrangle.md#`@` 属性写入与前缀 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖 |
| at-read | docs/manual/nodes/pdt_wrangle.md#`@` 属性读取 | test_pdt_wrangle.py | 已覆盖 |
| one-type-per-attribute | docs/manual/nodes/pdt_wrangle.md#一个属性一种类型 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖 |
| type-conversion | docs/manual/nodes/pdt_wrangle.md#类型转换 | test_pdt_wrangle.py | 已覆盖 |
| element-numbering | docs/manual/nodes/pdt_wrangle.md#元素编号 | test_pdt_wrangle.py | 已覆盖（别名已退役：用 `@elemnum` / `@numelem`，tag `wrangle-l0-aliases`） |
| run-over-kinds | docs/manual/nodes/pdt_wrangle.md#Run Over 九档 | test_pdt_wrangle.py | 已覆盖（九档 + 一档一组件 + 0 元素什么都不做：W-2 / E-8 / E-20） |
| menu-value-compat | docs/manual/nodes/pdt_wrangle.md#菜单取值兼容性 | test_pdt_wrangle.py | 已覆盖（文档性） |
| position-alias | docs/manual/nodes/pdt_wrangle.md#别名 | test_pdt_wrangle.py | 已覆盖 |
| bounded-execution | docs/manual/nodes/pdt_wrangle.md#有界执行 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖 |
| numeric-model | docs/manual/nodes/pdt_wrangle.md#数值 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖 |
| at-p-scope | docs/manual/nodes/pdt_wrangle.md#`@P` 的适用范围 | test_pdt_wrangle.py | 已覆盖（E-18 边/面没有 `@P`、E-6 面角只读、E-19 曲线控制点读写、E-22 实例平移） |
| at-operator | docs/manual/nodes/pdt_wrangle.md#`@` 操作符 | test_pdt_wrangle.py | 已覆盖（"读冻结的输入视图"今天成立；条目是新拆出来的） |
| selection-scope | docs/manual/nodes/pdt_wrangle.md#Selection | test_pdt_wrangle.py | 已覆盖 |
| geometry-inputs | docs/manual/nodes/pdt_wrangle.md#多几何流 | test_pdt_wrangle.py | 已覆盖（4 条输入；输入 1…3 由显式索引函数 + `geo` 读取，E-45/E-46/E-47） |
| point-read-domain | docs/manual/nodes/pdt_wrangle.md#point() 与 npoints() | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（`domain` 词表 + 跨组件读 + 按 (输入, 组件) 的冻结尺寸；E-19b / E-42 / 自测六条词表检查） |
| prim-read-domain | docs/manual/nodes/pdt_wrangle.md#prim() 与 nprimitives() | test_pdt_wrangle.py | 已覆盖（`fprim`/`iprim`/`vprim`/`nprimitives` + 两词词表；E-44b 读真实 FACE 属性、E-44 数样条） |
| indexed-read-silent | docs/manual/nodes/pdt_wrangle.md#显式索引读异常静默 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（缺席组件 / 缺属性 / 越界 / 本次新建的元素都读 0，点名的组件同样如此） |
| indexed-read-error | docs/manual/nodes/pdt_wrangle.md#显式索引读异常报错 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（`geo` 非 0..3、词表外的 domain、存储类型不合；E-45/E-46/E-47 + 自测） |
| set-point-attrib | docs/manual/nodes/pdt_wrangle.md#setpointattrib() | test_pdt_wrangle.py | 已覆盖（`domain` 词表 + 跨组件写 E-57e + 缺属性新建 E-50/E-53） |
| set-prim-attrib | docs/manual/nodes/pdt_wrangle.md#setprimattrib() | test_pdt_wrangle.py | 已覆盖（`domain` 词表 + 样条域写 E-57f + 网格面写 E-55） |
| indexed-write-silent | docs/manual/nodes/pdt_wrangle.md#显式索引写异常静默 | test_pdt_wrangle.py | 已覆盖（组件不在流里 / 越界 / 值类型不合 都是静默：E-54 / E-56 / E-57 / E-57b） |
| indexed-write-error | docs/manual/nodes/pdt_wrangle.md#显式索引写异常报错 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（`geo` 非 0 = E-59；词表外 = E-57c/E-57d；编译错误按 `code-error-output` 输出空几何） |
| add-point | docs/manual/nodes/pdt_wrangle.md#addpoint() | test_pdt_wrangle.py | 已覆盖（签名/位置/两种点表/跨组件/缺组件时新建，**Instances 遍**也行了：E-85） |
| add-prim | docs/manual/nodes/pdt_wrangle.md#addprim() | test_pdt_wrangle.py | 待改（签名/词表/下限/引用/两种复制/instance 源都已落地；**面号用的是 `addpoint` 交出的号** —— 这一条原本是坏的：面在计数遍记录，而那里 `addpoint` 的基址还不知道，于是所有盒子都建到输入自己的点上；已按阶段拆开修复并由 `E-88` 钉住；**`Run Over = Detail` 也能造拓扑了**（那条分支原本跑一遍就把产物丢掉，现在程序要造拓扑时走同一套两遍机制：点表/网格面由 `E-89` 钉住、**从 Detail 建曲线**由 `E-90` 钉住 —— 曲线的元素迭代就用 polyline 点名的那个 `pointdomain`，因为 Detail 没有 `@`、那是它唯一能触到的组件）。另外**两处记录的限制**：①同一程序里曲线请求必须点名同一个源 —— 已由 **E-87 钉成断言**；②instance 源的**属性**不复制 —— **Blender 限制**（`gather_attributes` 读不了 instance 域，实测 `attribute_access.cc:756` ← `instances_attributes.cc:114` 空读崩溃），只复制位置） |
| remove-prim | docs/manual/nodes/pdt_wrangle.md#removeprim() | test_pdt_wrangle.py | 已覆盖（prim 词表两个词；点名组件的表、越界与缺席静默） |
| remove-point | docs/manual/nodes/pdt_wrangle.md#removepoint() | test_pdt_wrangle.py | 已覆盖（四个词都有：mesh 连带边面 / curve 删控制点、空样条一并删 / pointcloud 删点 / instance 删实例；缺席与越界静默） |
| ordered-parallelism | docs/manual/nodes/pdt_wrangle.md#有序并行 | test_pdt_wrangle.py | 已覆盖（五条承诺逐条核对：E-12/E-52/E-60/E-67/E-72 + 自测的相邻编号；新增 `E-86` 在 **1024 元素**上压合并与编号，并记下 `-t 1` 单线程整轮同样是 217 项 0 失败） |
| code-comment-shortcut | docs/manual/nodes/pdt_wrangle.md#code-框快捷键 | wrangle_engine_selftest.cpp | 已覆盖（`Ctrl+/` 纯变换 + 断言 ✓；键处理只对 `Code` 框生效 ✓；**变换之后选区仍盖住同样的行**（不漂移，tag `wrangle-shortcut-selection`）✓；无事可做时按键也被吃掉（那个 `/` 不会打进代码）✓；**按键已人眼验收通过**（2026-10-09，步骤见 `qa/wrangle/code_shortcuts_checklist.md`）✓） |
| code-tab-indent | docs/manual/nodes/pdt_wrangle.md#code-框快捷键 | wrangle_engine_selftest.cpp | 已覆盖（`Tab`/`Shift+Tab` 纯变换 + 断言 ✓，含**目标行取光标所在行**（不是 `sel_start`）✓；`Tab`/`Shift+Tab` 在本节点**永不移动焦点** —— 无事可做也吃掉按键，tag `wrangle-code-shortcuts` / `wrangle-shortcut-selection` ✓；只对本节点的 `Code` 框生效 ✓；**按键已人眼验收通过**（2026-10-09，步骤见 `qa/wrangle/code_shortcuts_checklist.md`）✓） |
| code-highlight | docs/manual/nodes/pdt_wrangle.md#语法高亮 | wrangle_engine_selftest.cpp | 已覆盖（分词器九类 + 19 条断言 ✓；绘制层按同类游程上色、颜色全走主题 `get_color_ptr(theme_get(), SPACE_TEXT, TH_*)` ✓，只有本节点的 `Code` 框启用 ✓；tag `wrangle-highlight-lexer` / `wrangle-highlight-draw` / `wrangle-highlight-paint`。**像素没法断言，靠人眼验收** ✓（截图 `qa/houdini/wrangle_highlight.png` ✓）；错误类别也已接上编译的行列号 ✓ —— 位置以**代码串为键**记录（位置是代码的纯函数 ✓），绘制层用同一字符串读回后调 `mark_error()` ✓，tag `wrangle-highlight-error`） |
| code-highlight-lexical | docs/manual/nodes/pdt_wrangle.md#语法高亮 | wrangle_engine_selftest.cpp | 待改（**类型名与函数名撞车已处置**：旋转类型叫 `quat`、函数叫 `quaternion(...)`，三条断言钉住"`quat` 是 Type、`quaternion` 是 Function、`matrix` 是 Type"，见 `pdt_wrangle_highlight.hh` 的 `is_type_name` 注释与 `docs/14-…实施记录.md` §改名；纯词法已断言：未定义名字不标错、`//` 在字符串里不算注释、`mark_error` 只按行列号标；错误位置已接上编译输出 ✓（以代码为键、行列号直接取自 `error.format()` 的来源 ✓）） |
| code-line-numbers | docs/manual/nodes/pdt_wrangle.md#行号 | wrangle_engine_selftest.cpp, test_wrangle_code_box.py | 待改（tag `wrangle-code-gutter`：编号规则（哪一行拿哪个号、折行续行留空、位数随行数增长、越界钳位）是**无 Blender 依赖的纯函数** `pdt_wrangle_edit.hh`，由引擎自测 7 条断言 ✓；接线（折行宽度 / 绘制 / 鼠标映射**共用同一个宽度函数**、颜色全走主题 Text 空间 `line_numbers_background` / `line_numbers` / `hilite`、只有本节点 `Code` 框启用）由 `test_wrangle_code_box.py` 结构守卫 ✓；**像素断言不了**，自检截图 `qa/wrangle/code_gutter_shot.png` ✓，等用户人眼确认） |
| code-enter-apply | docs/manual/nodes/pdt_wrangle.md#回车与生效时机 | test_wrangle_code_box.py | 待改（tag `wrangle-code-gutter`：`Enter`/`Shift+Enter`/小键盘回车插换行 ✓、`Ctrl+Enter` 走 `apply_but_TEX()` 写回且不退出编辑态 ✓（**不动 `data->applied_interactive`** —— 结构守卫专门盯这一条，动了会在退出时丢字）、离开编辑态照旧写回 ✓、`Esc` 仍是取消 ✓、框未编辑时 `Enter` 只进框 ✓、其余文本框的 `Enter` 确认行为不变 ✓。**按键断言不了**（文本框无法脚本驱动，实测见 `test_wrangle_code_box.py` 抬头），等用户人眼确认） |

| channel-set | docs/manual/nodes/pdt_wrangle.md#通道与它的名字和类型 | test_pdt_wrangle.py | 已覆盖（tag `wrangle-channels`：`channels.new` 就声明插座（W-5）、改名保值 / 改类型重置为新类型默认值 / 删除即撤插座（W-6）、通道不被读时几何逐位不变（W-7）、名字与类型与值随文件往返（W-4）） |
| channel-read | docs/manual/nodes/pdt_wrangle.md#chf-chi-chv-chq-chm | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（五种类型：E-91 三种 + **E-109/E-110 的 Rotation 与 Matrix 两条**；`chf(1)` 非字面量是编译错误 → 空几何 E-92；`Detail` 档能读 E-96；自测断言 `chf`/`chi`/`chv`/`chq`/`chm` 都是 host-supplied 且只读的绑定、各走各的寄存器文件。**实测三点**：Rotation 通道是 `NodeSocketRotation`（值是三个欧拉角，主机转四元数）、Matrix 通道是 `NodeSocketMatrix`（**Python 没有 `default_value`，只能用连线喂**）、类型不符一律读 0） |
| channel-value | docs/manual/nodes/pdt_wrangle.md#通道取值 | test_pdt_wrangle.py | 已覆盖（E-91 每个元素读到同一个值；**E-97 连线驱动**读的是求值后的 `4.0`，不是插座默认的 `0.25` —— 驱动源用 `GeometryNodeSwitch`，见 `qa/wrangle/channel_link_probe.py`） |
| channel-missing-zero | docs/manual/nodes/pdt_wrangle.md#通道读不到时 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-93 缺名 = 0 且几何直通、E-94 大小写不符与 `"height.y"` 分量路径都是 0；自测断言缺名仍然编译通过） |
| channel-type-mismatch | docs/manual/nodes/pdt_wrangle.md#通道读不到时 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-95 三条：vector 通道按标量读 = 0、标量通道按 `chv` 读 = `(值,0,0)`、`chi` 对 float 通道截断 `3.7 → 3`；自测断言同名读两种类型是**两个**绑定） |
| channel-name-unique | docs/manual/nodes/pdt_wrangle.md#通道与它的名字和类型 | test_pdt_wrangle.py | 已覆盖（W-6：重名变成 `h2.001`，已有通道的名字与值都不动） |

| func_rotate_rotation | docs/manual/nodes/pdt_wrangle.md#旋转 | wrangle_engine_selftest.cpp | 已覆盖（自测：base=Z90 / by=X90 两档实测——global 落在 `(0,0,1)`、local 落在 `(0,1,0)`；`space` 不是 `"global"/"local"` 是编译错误；`space` 不是字面量也是编译错误） |
| func_rotate_vector | docs/manual/nodes/pdt_wrangle.md#旋转 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-107 把 `q@r` 存下来再转 `{1,0,0}` → `(0,1,0)`；自测：绕 Z 90° 把 X 转成 Y、零轴向量保持原样、参数少一个是编译错误） |
| func_axis_angle_rotation | docs/manual/nodes/pdt_wrangle.md#旋转 | wrangle_engine_selftest.cpp | 已覆盖（自测：轴长度 0 → 单位旋转（`WR-45`）；角度是弧度——`radians(90)` 与直接给 `90` 的结果不同） |
| func_euler_rotation | docs/manual/nodes/pdt_wrangle.md#旋转 | wrangle_engine_selftest.cpp | 已覆盖（自测：`{0,0,radians(90)}` 把 X 转成 Y，并且 `to_euler` 逆回来是 `{0,0,π/2}`） |
| func_quaternion | docs/manual/nodes/pdt_wrangle.md#旋转 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-98/E-99：`q@` 写出 `QUATERNION`、**w 在前**、`.w/.x/.y/.z` 与 `q[0..3]` 一致；自测：4 个标量参数、少一个或多一个都是编译错误） |
| func_to_euler | docs/manual/nodes/pdt_wrangle.md#旋转 | wrangle_engine_selftest.cpp | 已覆盖（自测：`euler_rotation` → `to_euler` 往返在 1e-5 内相等） |
| func_to_axis_angle | docs/manual/nodes/pdt_wrangle.md#旋转 | wrangle_engine_selftest.cpp | 已覆盖（自测：`to_axis_angle(q, &axis, &angle)` 的轴 `(0,0,1)`、角 90°；漏写 `&` 或出参类型不对都是编译错误） |
| func_axes_rotation | docs/manual/nodes/pdt_wrangle.md#旋转 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-108：`axes_rotation("z","x", {1,0,0}, {0,1,0})` 的本地 Z 落在 `(1,0,0)`、本地 X 落在 `(0,1,0)`；自测两组轴对把三个基向量都读出来；轴字不在 `x/y/z` 里是编译错误；两轴字相同 → 单位旋转。**这一条抓出过 `quat_from_mat3` 末尾三个分支的行/列主序混用**——原用例用的是退化输入（落迹分支，两种约定都过），见 `docs/14-Wrangle节点实施记录.md` §旋转与矩阵第 5 条） |
| func_invert_rotation | docs/manual/nodes/pdt_wrangle.md#旋转 | wrangle_engine_selftest.cpp | 已覆盖（自测：绕 Z 30° 的反向把 `{1,0,0}` 转成 `(cos30, -sin30, 0)`） |
| func_align_rotation_to_vector | docs/manual/nodes/pdt_wrangle.md#旋转 | wrangle_engine_selftest.cpp | 已覆盖（自测：单位旋转 + `axis="z"` + `v={1,0,0}` 之后本地 Z 指向 X；`axis` / `pivot` 不在词表里是编译错误） |
| func_combine_transform | docs/manual/nodes/pdt_wrangle.md#矩阵 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-100/E-102：`m@` 写出 `FLOAT4X4`、平移落在最后一列（12…14）、无初值的 `matrix` 是单位阵；自测：缩放作用在 `m*{1,0,0}`、平移在 `m*{0,0,0}`） |
| func_matrix_multiply | docs/manual/nodes/pdt_wrangle.md#矩阵 | wrangle_engine_selftest.cpp | 已覆盖（自测两条互为镜像：`matrix_multiply(t, s)` 先把 s 作用上（点落在 `(4,2,3)`），镜像调用先作用 t（`(6,6,9)`）） |
| func_invert_matrix | docs/manual/nodes/pdt_wrangle.md#矩阵 | wrangle_engine_selftest.cpp | 已覆盖（自测：`m * invert(m)` 把 `{7,8,9}` 还原到 1e-3 内；不可逆矩阵给单位阵且不报错、不出 NaN（`WR-55`）） |
| func_transpose_matrix | docs/manual/nodes/pdt_wrangle.md#矩阵 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-101 与自测：转置把平移从最后一列搬到**最后一行**，`t[3]`（第 4 行）拿到 `(1,2,3)`、`t[3][0]` 拿到一个分量；`m[row][column]` 的下标语义按行优先、存储列主序） |
| func_matrix_determinant | docs/manual/nodes/pdt_wrangle.md#矩阵 | wrangle_engine_selftest.cpp | 已覆盖（自测：绕 X 转 45°、缩放 2 → 行列式 8） |
| func_seperate_matrix | docs/manual/nodes/pdt_wrangle.md#矩阵 | test_pdt_wrangle.py, wrangle_engine_selftest.cpp | 已覆盖（E-103 与自测：`separate_transform(m, &t, &r, &s)` 取回翻译 `(4,5,6)` 与缩放 `(7,8,9)`；绕 Z 90° 的矩阵拆出的旋转再把 X 转成 Y；出参只能是变量或整个 `@属性`。**注：L0 这一条的函数名拼作 `seperate_matrix`，实现与手册用 Blender 节点的名字 `separate_transform`——待用户改 L0 拼写**） |

**2026-10-09 通道**（切片 `feat/wrangle-channels`）：用户追加 `WR-35`…`WR-40`（六个 slug 见上表），
L1 是 `manual/nodes/pdt_wrangle.md` 新增的 `## 通道` 一节（含"改类型 = 值重置"与
"读不到就是 0"的 H21 实测规则，探针 `qa/wrangle/wrangle_h21_channel_probe.py`）。

**2026-10-08 重排后**（见文件末尾的新快照）：L0 现在 **26 条**、全部带 slug 注释，`pdt_spec.py renumber`
按文件顺序重排过一次编号。`Code` 报错 = **空几何** 已在契约与 `code-error-output` 两处统一
（不再有 `error-keeps-geometry` 那条），代码与断言要跟着改（今天仍是原样流）。

**2026-10-07 用户重写 L0**（`pdt_wrangle.md` 41 条 → 30 条），覆盖表随之重建；完整对照与缺口清单见
`../docs/14-Wrangle节点实施记录.md` §L0 2026-10-07 重写。三类变化：

* **新增**：`WR-45`…`WR-52`（`prim` 读与计数、显式索引读/写的"静默 vs 报错"分裂、
  `setpointattrib` / `setprimattrib` 带 `domain`、`addpoint(geo, domain, pos)`）与 `WR-47`。
  `WR-36` 改号为 `WR-53` 后被**注释停用**（`addprim` 本轮不承诺）——解析器忽略 HTML 注释，所以它不是条目。
* **并入 / 取代**：`WR-9` → `WR-23`；`WR-22` → `WR-8`（九档改成"一档一个组件"，不再一次覆盖多种组件）；
  `WR-33` → `WR-48` / `WR-49` / `WR-51` / `WR-52`；`WR-44` → `WR-50`；`WR-42` → `WR-26`；
  `WR-16`…`WR-19` 与 `WR-25` → `## 明确不做` 的散文（从这一版起**不再编号**）。
* **没有承接条目（缺口）**：`WR-34`（新增元素编号顺序、缓冲写合并的确定性、"写刚创建的元素有效"、
  新元素不参与本次迭代）、`WR-35` 的"读任意域 / 写只落在本次跑的组件"、`WR-39`（新建的组件留在输出流里）、
  `WR-37` / `WR-43`（`removepoint` / `removeprim` 与实例删除）、`WR-40`（`addprim` 的实例来源）。
  这些今天**都有实现与断言**，处置方式（写回 L0 还是明确降级为"实现细节"）待用户决定。

**`WR-16`…`WR-19` 与 `WR-25` 的覆盖行已删**：它们是 2026-09-30 编号化的"延期条目"，2026-10-07 起
`## 明确不做` 回到散文、不再占号；"不做某事"本来也只能由"语法/函数不存在"来体现。历史记录见 L2。


**`WR-15` 与 `WR-20` 已从 L0 删除**（2026-09-30 第四次改 L0）：`WR-20`（不做 `@opinput*` 多输入）
与 `WR-15`（不做拓扑修改）正是这次多几何流/拓扑工作的目标，条目留着就自相矛盾了。
覆盖表的两行随之删除——这就是"延期条目加回来"流程反着走一遍。
**新增的 `WR-29`…`WR-37`**（多几何流 / 跨流读取 / 只读 / 读不到就是 0 / 显式索引函数 /
写入只认 0 / 拓扑与跨界写 / `prim` 索引空间 / `addprim` 的 type / 删除的级联）分两刀落地：
* **M4a（已落地）**：`WR-29`/`WR-30`/`WR-32` 的跨流部分——`E-29`…`E-34` 断言正读、`P` 别名、
  越界=0、同类型组件规则、未连=0、跨流类型不符=节点错误且不留半成品、写别的输入被拒。
* **M4b（已落地）**：`WR-31` 的八个显式索引函数与 `WR-35` 的图元表——`E-35`…`E-49` 断言
  字面量下标 / 运行期下标 / 越界（含负数）/ 缺失属性读 0 且不创建 / 点表与图元表（网格面、
  曲线样条、点云与实例没有表）/ 表不跟 `Run Over` 走 / 跨流按下标读 / 存储类型不符是节点错误 /
  只读 / `Detail` 档可用；引擎自测另钉住九个编译期拒绝（流非字面量、流越界、名字非字符串、
  缺下标、下标是向量、多给参数、不可赋值、同名跨域类型冲突、未知函数名）。
* **还没做**：`WR-33`/`WR-34`/`WR-36`/`WR-37`（写入与拓扑、跨界写的并行合并契约、`addprim`
  的 type 与目标组件、删除的级联）——即 M5 / M6，仍标 `仅文档`。
  （**这句话已过期**：`WR-33`…`WR-37` 在 M5 就落地并改成 `已覆盖` 了，见上面那五行；
  留在这里只作为当时的记录。M6 的现状看下面两条。）

**`WR-38`…`WR-44`（M6，2026-10-06 起）**：条目由用户落 L0，实现与断言分批推进。

* **`WR-41` 已落地**（用户选 (A)：把"起始数量"在计数遍之前记下来）。`npoints` / `nprimitives` 与同流
  `fpoint` / `fprim` 的越界判定都用**本次求值开始时**的表；跨流与 `Detail` 不变。判据 `E-72` 八条
  （起始数量、两次"确实建了"的几何对照、`vpoint` 读新建点 = 0，加上 2026-10-07 补的**收缩侧三条**：
  删除后 `npoints` 仍是 8 / 同程序写入落在活下来的元素上 / 写到被删元素的写消失）；两条正对照：
  去掉那处记录 → `if (npoints(0) > 8) …` 当场变号（`-1` / `nprimitives` 14 vs 6）；把删除挪回计数遍
  → 收缩侧三条变红（且量到旧顺序会把 5 号的写落到 6 号，`x` = `(0,0,0,0,0,42,0)`）。细节在
  `docs/manual/nodes/pdt_wrangle.md#WR-41 的起始数量是怎么算的（M6 选项 A）` 与
  `docs/14-Wrangle节点实施记录.md` §M6 第 5 步 / §M6 第四刀。
* **`WR-42` / `WR-44` 也已落地**（tag `wrangle-m6wr4244`）：`E-70` 钉住"只有被选中的元素建点、未选中的
  既不写也不建"（`Selection = Index < 4` 时 8 → 12 点，新点仍按元素序编号）；`E-71` 钉住"**负数**下标
  丢弃且不报错、属性留在默认值"（正的越界那一半在 M5 就由 `E-54` 钉住了）。两条的语义依据是 H21 实测
  （`group="0-3"` 的 run 8 → 12 点；`999` 与 `-1` 都无报错、属性照建全默认），见
  `docs/14-Wrangle节点实施记录.md` §M6 第一步。
  顺带：那一轮"整轮在 `E-69` 之后直接中断"当时复现不出来（13 个候选一个进程各跑一次全过）——
  **事后记一笔**：第四刀证明"一用例一进程"的隔离器对"**长序列才崩**"的缺陷是无效的，
  这类缺陷要用**截断/前缀二分**（把测试文件从某个用例处截断再跑）来定位。
* **`WR-37` 的套用时机变了**（2026-10-07，tag `wrangle-m6removal`）：网格流的删除**在真实遍之后**套用，
  这是 `WR-41`/`WR-34` 承诺 3"冻结输入视图"在删除场景下的落地（探针 `wrangle_m6_count_probe.py`：
  `mark`/`np` 落在活下来的 7 个元素上、被删元素的值随它消失）。曲线/点云仍在计数遍就地套用。
* **曲线 / 点云流的真实遍已修**（同上 tag）：`E-73`（曲线流 `i@np = npoints(0); i@p2 = addprim(0,"polyline",…)`
  → 10 个控制点、`np` = 六个 6 + 四个 0）与 `E-74`（点云流同理 → 8 点、`np` = 8）钉住
  "与建曲线同程序的写入照常落地"；修法是网格专用早退收窄 + 曲线路径替换组件后**重取属性访问器**。
* **`WR-39` 已落地**（2026-10-07，tag `wrangle-m6wr39`）：`run_over_geometry` 先快照输入的组件种类，
  本次求值**新建**的组件（`addprim(0, "polyline", …)` 建出的曲线）留在输出里但**不被本次处理**。
  判据是 `E-64` 两条——曲线仍恰好 16 个控制点（8 条样条 × 2；没有门控时读到 **48**），
  以及网格侧同一程序的 `np` / `p2` 落地（`np` 8×8、`p2` = 0..7）。
* **网格 + `polyline` 的旧错误与它背后的越界写都已修**（同 tag）：`apply_mesh_topology` 的校验循环与
  **尾部写角点的循环**都跳过 polyline 请求——后者原先会把 polyline 的点号写进 `.corner_vert` 数组末尾之外，
  干线的 `return false` 恰好把它挡住了（所以改成正确行为反而"崩"）。真凶、工具与崩溃栈见
  `docs/14-Wrangle节点实施记录.md` §M6 第五刀；`--debug-memory` 下整份测试 **exit 0 且无任何内存报告**。
* **`WR-38` / `WR-40` / `WR-43` 仍有新能力要做**（分组件点表 `addpoint`、实例来源拒绝、实例删除）；
  其中 `WR-38` 子条 2 的**曲线**那一句措辞待定——H21 实测曲线上的 `addpoint` 是**游离点、不接进曲线**，
  而 L0 写的是"新增控制点"（详见 L1 的"实现差异"段）。

**`WR-21` 已被用户删除**（2026-09-30 第三次改 L0）：那条原写作"不做 `Run Over = Edges / Faces /
Face Corners / Detail`"，但 `WR-8` 把这些档位列进了契约、实现也跟上了，措辞不再成立。
删这条正是"延期条目加回来"流程的第一步，第二步（覆盖表删行）由这次改动完成——
留着那一行 `check` 会报 `WR-21: coverage.md has a row but pdt_wrangle.md has no such item`。
同一次 L0 改动还把 `WR-9` 的旧标签 `Vertices` 改成 `Face Corners`，并给 `WR-8` 补上
"`Detail` 档不支持属性读写"——后者已实现（`Detail` 跑一次、任何属性引用报错），由 `E-21` 钉住。
`E-8` 随之改用 `Instances`（当时唯一未实现的档位）；**随后 `Instances` 也落地了**
（`@P` = 实例平移的读写、`@elemnum` 数实例，见 `E-22`），于是 **7 档全部实现**、
`WR-14` 只剩"流里没有该域的组件"这一条会触发（它那句"该档位尚未实现"目前用不上）。

**`WR-26`…`WR-28` 是 2026-09-30 第三批追加的行为条目**（作用范围 `Selection` / 选择字段的域与编号 /
选择与 `Detail`），由 `E-23`…`E-28` 钉住：只写选中的元素、空选择连属性都不创建、
不连 = 全选（这一条是文件里其余所有断言的闸门，因为它们都不连 `Selection`）、
布尔属性即群组、`@elemnum` / `@numelem` 不因筛选改变、`Detail` 忽略字段。

性能（`qa/wrangle/wrangle_m3_perf_probe.py`，100 万点、`@P.y += sin(@P.x * 1.7) * 0.5;`、取 5 次最小值）：
不连 **1.700 ms**；连一个**常量**字段 **1.701 ms**（证明"不连 = 零开销"）；
连 `Index >= 0`（全真但要逐元素算）1.910 ms（差值是**字段自身**的代价）；连 `Index < 500000`
（选一半）**1.054 ms**（0.62×）。第一次测量得到的是"连了就 +1.6 ms 且与选多少无关"这种反直觉结果——
原因是探针把 4 个用例的 modifier **叠加**在同一个 object 上，后面的用例把前面的也算了；
补上 `obj.modifiers.remove(modifier)`（M1 探针本来就有这一行）之后才是上面这组数字。

**`WR-22`…`WR-24` 是 2026-09-30 第二批追加的行为条目**（多组件 / `@P` 的适用范围 /
菜单取值兼容性），都是 `Run Over` 从 2 档长到 7 档时暴露出来的，分别由 `E-20`
（网格 + 点云两条组件都跑）、`E-18` + `E-19`（边/面/样条上 `@P` 是编译错误）、
`W-2`（7 个标签就是菜单契约）钉住。同一批实现里还包括 `Points` 覆盖曲线控制点、
新增 `Splines` 档（都在 `E-19`），以及修掉"只跑第一个匹配组件"的缺口。

**把延期条目加回来的流程**：用户先删掉 L0 里那一行（若升级成保证则在 `## 不变量` 末尾追加新 ID）
→ agent 改实现 + L1（本节与页面的"限制"段）+ 测试 → 覆盖表删掉这一行、新条目加新行 → `sync`。
删掉 L0 行而没删覆盖行时，`check` 会报 `WR-nn: coverage.md has a row but … has no such item`。

**落盘时改掉的两处**（草稿 → L0），跟进情况：

1. **WR-3 从"裸名按 float"改成"必须写前缀，裸名报错"** → 这是行为改动，实现跟着改了：
   引擎（`pdt_wrangle_vm.hh` 的 `resolve_attribute`）现在对**任何**不带前缀的属性名报编译错误
   （`@P` / `@elemnum` / `@ptnum` / `@numelem` 例外，它们的类型不用猜），**读也一样**；
   测试补了 `E-13`，引擎自测补了三条断言（含"错误信息里要给出 f@/i@/v@ 三种写法"）。
   原来那条"裸名当 float"的 VEX 习惯**已删除**。
2. **性能预算那条被删掉**（草稿里的 `WR-14`）→ 编号顺延，现在的 `WR-14` 是"不支持的输入"。
   **但 L0 的验收段里还留着一行**"性能：`qa\wrangle\wrangle_m1_perf_probe.py` 的三个用例都满足 WR-14"，
   现在 `WR-14` 指的是"不支持的输入"，这句话自相矛盾——已在回复里提请你改一句
   （要么删掉那行，要么补一条性能条目并把编号写对）。

**验收命令与实测**（L2，无规范效力）：`docs/14-Wrangle节点实施记录.md` §M1.2/§M1.4
（100 万点 1.565 ms、原生对照 0.980 ms、与 numpy 的 3.05e-05 正对照）。

WR-36` 的曲线侧两个分支（新建组件 / 追加样条）都已覆盖：`E-64` 是新建（网格流，经 `Curve to Mesh` 读到 8×2 个控制点），`E-65` 是追加（已有曲线的流上 6 → 10 个控制点，坏点号仍是节点错误）。

## 编号快照（2026-10-07 重写时）

编号是给人看的标签，会随重排变化；**slug 才是身份**。历史文本（`docs/14-…`、旧的提交信息）里出现的
编号是**写作当时**的编号，不追改——按下面这张表查它现在叫什么、或者它去哪了。
每次重编号在文件末尾追加一段新的快照（新的在后，旧的仍然保留）。

格式：`| 当时编号 | slug | 现状 |`。第三列写"现行 `WR-nn`"表示规则还在（编号可能变了）；
写"已并入/退役"表示这条规则在新 L0 里没有承接条目或换了条目承担。

| 当时编号 | slug | 现状 |
| --- | --- | --- |
| WR-1 | code-passthrough | 现行 `WR-1` |
| WR-2 | elementwise-determinism | 现行 `WR-2` |
| WR-3 | at-write | 现行 `WR-3` |
| WR-4 | at-read | 现行 `WR-4` |
| WR-5 | one-type-per-attribute | 现行 `WR-5` |
| WR-6 | type-conversion | 现行 `WR-6` |
| WR-7 | element-numbering | 现行 `WR-7`（`@ptnum` / `@primnum` 已不承诺） |
| WR-8 | run-over-kinds | 现行 `WR-8`（七档 → 九档） |
| WR-9 | face-corner-p-readonly | 已并入 `at-p-scope`（退役） |
| WR-10 | position-alias | 现行 `WR-10` |
| WR-11 | error-keeps-geometry | 现行 `WR-11` |
| WR-12 | bounded-execution | 现行 `WR-12` |
| WR-13 | numeric-model | 现行 `WR-13` |
| WR-14 | unsupported-input | 现行 `WR-14` |
| WR-16 | no-adjacency-queries | 退役（`明确不做` 的散文，2026-10-07 起不占号） |
| WR-17 | no-user-functions | 退役（同上） |
| WR-18 | no-array-attributes | 退役（同上） |
| WR-19 | no-debug-print | 退役（同上） |
| WR-22 | multi-component-run | 已并入 `run-over-kinds`（九档一档一个组件） |
| WR-23 | at-p-scope | 现行 `WR-23` |
| WR-24 | menu-value-compat | 现行 `WR-24` |
| WR-25 | no-grease-pencil-layer | 退役（`明确不做` 的散文） |
| WR-26 | selection-scope | 现行 `WR-26` |
| WR-27 | selection-numbering | 现行 `WR-27` |
| WR-28 | selection-detail | 现行 `WR-28` |
| WR-29 | geometry-inputs | 现行 `WR-29` |
| WR-30 | no-cross-stream-read | 现行 `WR-30`（语义反转：改为"不做"，代码待删） |
| WR-31 | point-read-domain | 现行 `WR-31`（旧读函数 + 新 `domain`） |
| WR-32 | indexed-read-silent | 现行 `WR-32` |
| WR-33 | write-target-literal-zero | 已并入 `set-point-attrib` / `set-prim-attrib` / `indexed-write-error` / `add-point` |
| WR-34 | topology-determinism | 退役（新 L0 无承接条目，见 `../docs/14-Wrangle节点实施记录.md` 缺口清单） |
| WR-35 | prim-table-by-component | 已并入 `prim-read-domain` 等；"读任意域 / 写只落本次组件"退役 |
| WR-36 | addprim-shape | 退役（`addprim` 改号 `WR-53` 后被注释停用） |
| WR-37 | removal-cascade | 退役（`removepoint` / `removeprim` 无承接条目） |
| WR-38 | attribute-write-and-addpoint | 已并入 `set-point-attrib` / `set-prim-attrib` / `add-point` |
| WR-39 | created-component-visibility | 退役（无承接条目） |
| WR-40 | addprim-instance-source | 退役（随 `addprim`） |
| WR-41 | start-count-and-visibility | 已并入 `indexed-read-silent`（读新建元素得 0）与计数 |
| WR-42 | selection-drives-creation | 已并入 `selection-scope` |
| WR-43 | instance-removal | 退役（无承接条目） |
| WR-44 | out-of-range-write-dropped | 已并入 `indexed-write-silent` |
| WR-45 | prim-read-domain | 新增（`WR-45`） |
| WR-46 | indexed-read-error | 新增（`WR-46`） |
| WR-47 | code-error-output | 新增（`WR-47`） |
| WR-48 | set-point-attrib | 新增（`WR-48`） |
| WR-49 | set-prim-attrib | 新增（`WR-49`） |
| WR-50 | indexed-write-silent | 新增（`WR-50`） |
| WR-51 | indexed-write-error | 新增（`WR-51`） |
| WR-52 | add-point | 新增（`WR-52`） |

## 编号快照（2026-10-08 重编号）

`pdt_spec.py renumber` 于 2026-10-08 按文件顺序重排了编号；左列是重排前的编号，读旧文本时照它查。

| 当时编号 | slug | 现状 |
| --- | --- | --- |
| WR-1 | code-passthrough | 现行 `WR-1` |
| WR-2 | code-error-output | 现行 `WR-2` |
| WR-3 | bounded-execution | 现行 `WR-3` |
| WR-4 | elementwise-determinism | 现行 `WR-4` |
| WR-5 | at-write | 现行 `WR-5` |
| WR-6 | at-read | 现行 `WR-6` |
| WR-7 | one-type-per-attribute | 现行 `WR-7` |
| WR-8 | type-conversion | 现行 `WR-8` |
| WR-9 | element-numbering | 现行 `WR-9` |
| WR-10 | run-over-kinds | 现行 `WR-10` |
| WR-11 | menu-value-compat | 现行 `WR-11` |
| WR-12 | position-alias | 现行 `WR-12` |
| WR-13 | numeric-model | 现行 `WR-13` |
| WR-14 | at-p-scope | 现行 `WR-14` |
| WR-15 | selection-scope | 现行 `WR-15` |
| WR-16 | geometry-inputs | 现行 `WR-16` |
| WR-17 | point-read-domain | 现行 `WR-17` |
| WR-45 | prim-read-domain | 现行 `WR-18` |
| WR-32 | indexed-read-silent | 现行 `WR-19` |
| WR-46 | indexed-read-error | 现行 `WR-20` |
| WR-48 | set-point-attrib | 现行 `WR-21` |
| WR-49 | set-prim-attrib | 现行 `WR-22` |
| WR-50 | indexed-write-silent | 现行 `WR-23` |
| WR-51 | indexed-write-error | 现行 `WR-24` |
| WR-52 | add-point | 现行 `WR-25` |
| WR-53 | add-prim | 现行 `WR-26` |

### 已退役的 slug（不再对应任何条目）

读历史时如果遇到下面这些 slug，它们是新 L0 里**没有承接条目**的旧规则：

| slug | 去哪了 |
| --- | --- |
| error-keeps-geometry | 并入 `code-error-output`：报错现在**输出空几何**，不再"原样流" |
| unsupported-input | 并入 `run-over-kinds`：没有该域的组件时**什么都不做**（原先报错并原样输出） |
| selection-numbering | 并入 `selection-scope` |
| selection-detail | 并入 `selection-scope` |
| no-cross-stream-read | 移进 `## 明确不做`（不占号）；代码待删 |

## 编号快照（2026-10-08 第 3 版（30 条））

第 3 版（30 条）

| 当时编号 | slug | 现状 |
| --- | --- | --- |
| WR-1 | code-passthrough | 现行 `WR-1` |
| WR-2 | code-error-output | 现行 `WR-2` |
| WR-3 | bounded-execution | 现行 `WR-3` |
| WR-4 | elementwise-determinism | 现行 `WR-4` |
| WR-12 | position-alias | 现行 `WR-12` |
| WR-30 | at-operator | 现行 `WR-30` |
| WR-5 | at-write | 现行 `WR-5` |
| WR-6 | at-read | 现行 `WR-6` |
| WR-9 | element-numbering | 现行 `WR-9` |
| WR-14 | at-p-scope | 现行 `WR-14` |
| WR-7 | one-type-per-attribute | 现行 `WR-7` |
| WR-8 | type-conversion | 现行 `WR-8` |
| WR-10 | run-over-kinds | 现行 `WR-10` |
| WR-11 | menu-value-compat | 现行 `WR-11` |
| WR-13 | numeric-model | 现行 `WR-13` |
| WR-15 | selection-scope | 现行 `WR-15` |
| WR-16 | geometry-inputs | 现行 `WR-16` |
| WR-17 | point-read-domain | 现行 `WR-17` |
| WR-18 | prim-read-domain | 现行 `WR-18` |
| WR-19 | indexed-read-silent | 现行 `WR-19` |
| WR-20 | indexed-read-error | 现行 `WR-20` |
| WR-21 | set-point-attrib | 现行 `WR-21` |
| WR-22 | set-prim-attrib | 现行 `WR-22` |
| WR-23 | indexed-write-silent | 现行 `WR-23` |
| WR-24 | indexed-write-error | 现行 `WR-24` |
| WR-25 | add-point | 现行 `WR-25` |
| WR-26 | add-prim | 现行 `WR-26` |
| WR-27 | add-prim | 现行 `WR-27` |
| WR-28 | remove-prim | 现行 `WR-28` |
| WR-29 | ordered-parallelism | 现行 `WR-29` |

## 编号快照（2026-10-08 重编号）

`pdt_spec.py renumber` 于 2026-10-08 按文件顺序重排了编号；左列是重排前的编号，读旧文本时照它查。

| 当时编号 | slug | 现状 |
| --- | --- | --- |
| WR-1 | code-passthrough | 现行 `WR-1` |
| WR-2 | code-error-output | 现行 `WR-2` |
| WR-3 | bounded-execution | 现行 `WR-3` |
| WR-4 | elementwise-determinism | 现行 `WR-4` |
| WR-12 | position-alias | 现行 `WR-5` |
| WR-30 | at-operator | 现行 `WR-6` |
| WR-5 | at-write | 现行 `WR-7` |
| WR-6 | at-read | 现行 `WR-8` |
| WR-9 | element-numbering | 现行 `WR-9` |
| WR-14 | at-p-scope | 现行 `WR-10` |
| WR-7 | one-type-per-attribute | 现行 `WR-11` |
| WR-8 | type-conversion | 现行 `WR-12` |
| WR-10 | run-over-kinds | 现行 `WR-13` |
| WR-11 | menu-value-compat | 现行 `WR-14` |
| WR-13 | numeric-model | 现行 `WR-15` |
| WR-15 | selection-scope | 现行 `WR-16` |
| WR-16 | geometry-inputs | 现行 `WR-17` |
| WR-17 | point-read-domain | 现行 `WR-18` |
| WR-18 | prim-read-domain | 现行 `WR-19` |
| WR-19 | indexed-read-silent | 现行 `WR-20` |
| WR-20 | indexed-read-error | 现行 `WR-21` |
| WR-21 | set-point-attrib | 现行 `WR-22` |
| WR-22 | set-prim-attrib | 现行 `WR-23` |
| WR-23 | indexed-write-silent | 现行 `WR-24` |
| WR-24 | indexed-write-error | 现行 `WR-25` |
| WR-25 | add-point | 现行 `WR-26` |
| WR-26 | add-prim | 现行 `WR-27` |
| WR-27 | remove-point | 现行 `WR-28` |
| WR-28 | remove-prim | 现行 `WR-29` |
| WR-29 | ordered-parallelism | 现行 `WR-30` |

## 编号快照（2026-10-08 第 4 版（重排后））

第 4 版（重排后）

| 当时编号 | slug | 现状 |
| --- | --- | --- |
| WR-1 | code-passthrough | 现行 `WR-1` |
| WR-2 | code-error-output | 现行 `WR-2` |
| WR-3 | bounded-execution | 现行 `WR-3` |
| WR-4 | elementwise-determinism | 现行 `WR-4` |
| WR-5 | position-alias | 现行 `WR-5` |
| WR-6 | at-operator | 现行 `WR-6` |
| WR-7 | at-write | 现行 `WR-7` |
| WR-8 | at-read | 现行 `WR-8` |
| WR-9 | element-numbering | 现行 `WR-9` |
| WR-10 | at-p-scope | 现行 `WR-10` |
| WR-11 | one-type-per-attribute | 现行 `WR-11` |
| WR-12 | type-conversion | 现行 `WR-12` |
| WR-13 | run-over-kinds | 现行 `WR-13` |
| WR-14 | menu-value-compat | 现行 `WR-14` |
| WR-15 | numeric-model | 现行 `WR-15` |
| WR-16 | selection-scope | 现行 `WR-16` |
| WR-17 | geometry-inputs | 现行 `WR-17` |
| WR-18 | point-read-domain | 现行 `WR-18` |
| WR-19 | prim-read-domain | 现行 `WR-19` |
| WR-20 | indexed-read-silent | 现行 `WR-20` |
| WR-21 | indexed-read-error | 现行 `WR-21` |
| WR-22 | set-point-attrib | 现行 `WR-22` |
| WR-23 | set-prim-attrib | 现行 `WR-23` |
| WR-24 | indexed-write-silent | 现行 `WR-24` |
| WR-25 | indexed-write-error | 现行 `WR-25` |
| WR-26 | add-point | 现行 `WR-26` |
| WR-27 | add-prim | 现行 `WR-27` |
| WR-28 | remove-point | 现行 `WR-28` |
| WR-29 | remove-prim | 现行 `WR-29` |
| WR-30 | ordered-parallelism | 现行 `WR-30` |
