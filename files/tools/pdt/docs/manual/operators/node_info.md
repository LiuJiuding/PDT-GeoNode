# Node Info（按住快捷键读节点信息）

> 插件：`pdt_mmb_info` ｜ 操作符：`pdt.node_info_modal`（搜索名 **PDT Node Info**）
> 快捷键：**没有默认值**，由你自己在 `Preferences → Keymap` 里指定（**MMB-8**）
> 面板：贴着触发位置的信息块（HUD），不是侧栏面板
>
> **规范来源**：`../../../spec/node_info.md`（用户手写的 `MMB-1…10`）。
> 本页是该决定层的细则展开，**编号与 L0 条目一一对应**；两者冲突时以 L0 为准。
> （更早的版本用 `G7–G13` 编号，现在统一成 `MMB-n`。）

按住你指定的快捷键、光标停在节点上，就有一块信息块告诉你**这个节点流出了什么几何**：
上半是按域分类的元素计数（每个域一种颜色），下半是按域分组的属性表；节点同时有几何输入与
输出时，**输出流里新增的属性**会另外标出来。

这个插件**只读**：不改节点、不改几何、不改选择，也不写任何数据。

---

## 打开与手势

| 操作 | 结果 |
| --- | --- |
| 光标在节点上，**按住你的快捷键** | 显示信息块，位置就是触发那一刻的光标旁 |
| 光标移动 | **什么都不发生**：块不动、不换读别的节点、也**不会消失**（L0 边界 + MMB-7） |
| **松开按键** | 信息块消失 |
| 按 **`Esc`** | 提前结束（实现细节：模态总要有个逃生口，L0 没要求这条） |

**没有钉住、没有关闭按钮**（MMB-7：不需要 Pin）——所以也没有 `P` 键、没有 `×`、
没有"钉住后点别处"那些规则。

> **先绑定**：`Preferences → Keymap`，在搜索框里输 **`PDT Node Info`**（或 `pdt`）就能
> 找到插件放进 `Node Editor` 键位表里的那一条（**没有绑键**、类型是 `(Unassigned)`），
> 点开它、给它一个字母键或修饰键 + 鼠标中键即可。加载项**不会**替你绑任何键（MMB-8）；
> 首选项面板只印一句
> `Assign one shortcut yourself: Preferences>Keymap>PDT Node Info`。
> 选键时的实测结论：**裸中键、`Shift/Ctrl`+中键都到不了加载项**，`Alt`+中键可以
> （`../09-MMB节点信息.md` §5.5）。

> **为什么要有这一条"没绑键"的条目**：`Preferences → Keymap` 里那个搜索框
> （`Search by Name`）**只筛"键位表里已经有的条目"**（`rna_keymap_ui.draw_filtered`：
> `filter_text in kmi.idname or filter_text in kmi.name`）。一个没有出现在任何键位表里的
> 操作符，在那里**搜不到**——工厂设置下启用本插件时正是这样，用户报的就是这个 bug。所以
> 插件会往**自己的 addon 键位表**里放**一条 `type = NONE` 的未绑定条目**（和键位编辑器
> 自己的 `Add New` 按钮建出来的那条一样）：它不绑任何键、也不可能被派发，
> 但操作符因此出现在 `Node Editor` 键位表里、能被搜到、能被改键。
> 实测（`qa/mmb/mmb_keymap_probe.py`，真窗口、工厂设置、运行时启用插件）：
> 没有这条条目时搜 `node info` / `pdt` 都是 **0** 条；有它之后是 **1** 条
> `('Node Editor', 'pdt.node_info_modal', 'PDT Node Info', 'NONE', 'PRESS')`；
> 用户给它绑上 `Alt+MMB` 后 `binding_is_active()` 为 True，重启后仍在（用户键位表里）。

---

## 命中的是哪个节点（规范性）

**判据与 Blender 自己完全一致**：编辑器实际画出来的那个节点矩形
（`bNode::runtime->draw_bounds`，Blender 内部用 `node_under_mouse_get()` 做点击/悬停判定）。

| 位置 | 算不算 |
| --- | --- |
| 节点标题带（编辑器在那里显示节点 tooltip） | **算** |
| 参数行 / socket 名称（编辑器在那里显示 socket tooltip） | **算** |
| 节点矩形内的空白（没有控件的地方） | **算**（Blender 认为它属于这个节点） |
| 与别的节点重叠处 | 取**最后绘制**的那个（选中/在前面的优先），与 Blender 的点击判定一致 |
| 折叠后的节点（`H`，只剩标题带） | **算**（按 `node_update_collapsed()` 的实际绘制矩形） |
| **帧节点 `NodeFrame`** | **不算**（它没有几何；编辑器自己也只认它的边框） |
| 编辑器**还没画过**的节点 | 不算（那时 Blender 自己也不知道它在哪） |
| 节点组里的**子组内部**节点 | 见"已知边界" |

判定的坐标系与缩放的关系见 `../09-MMB节点信息.md` §5.8：鼠标位置要换算到**节点空间**
（除以 `UI_SCALE_FAC`）才能和节点坐标比；UI 缩放的机器上曾经因此大面积点不中。

---

## 信息块里有什么

版式照 `../../../spec/node_info_ui_v3.png`（L0 给的参考图），一共两半：

### 上半：元素数量（永远都有，且是固定表格）

**列的是全部几何域的元素数**，一行放一个组件的那几个域。表格是**固定的**：不管这个节点
有没有这类几何，这些域永远按同一顺序出现，没有的就是 `0`——所以两个节点的计数半可以一行
一行对着看（L0 MMB-5："便于固定显示位置，方便用户比较"）。

**组件大类名（`Mesh` / `Curve` …）不画了**（L0："只保留domain子类，不需要mesh/curve等大类了"）：
组件现在只决定**换行**，也就是同一个组件的域排在同一行。唯一例外是实例——它没有子层，
所以行首就是它自己的名字（参考图里的 `Instances 0`）。

```
Vertex 441  Edge 840  Face 400  Face Corner 1600
Control Point 6  Spline 3
Point 0
Instances 0
Grid 0
```

| 行 | 域标签 |
| --- | --- |
| 网格 | `Vertex`（点）／`Edge`／`Face`／`Face Corner`（角） |
| 曲线 | `Control Point`（控制点）／`Spline` |
| 点云 | `Point` |
| 实例 | `Instances`（没有子层，用组件自己的名字当标签） |
| 体积 | `Grid`（体积的元素就是它的 grid） |

* **每个域有自己的颜色**（L0："每个domain子类设置不同的颜色"），**数字和它的域标签同色**；
  颜色都是首选项，见"首选项"。
* **数量的字比域标签大一号**（L0："数量的文字比domain子类文字稍大"）：标签与**属性组标题**
  都是正文号，数字是正文 ×1.25（照参考图量的 32px/26px）。`v3` 参考图把组标题缩回了正文号
  （`v2` 里它比正文大一档 41px），所以现在是**两档**字号：12 和 15。
* **`0` 不变暗**，就用它所属域的颜色：颜色说的是"这是哪个域"，不是"这里有没有东西"
  （L0 MMB-5 上一轮的"不需要按是否有元素区分颜色"仍然有效）。所以只在网格物体上输出曲线的
  节点，那一行 `Vertex 0  Edge 0  Face 0  Face Corner 0` 照常显示、照常有颜色——那是 Blender
  求值网格物体时补上的空 Mesh 组件，属于固定表格的一部分，不是这个节点的产物。
* **蜡笔（Grease Pencil）暂时不列**（L0："先不管蜡笔"），所以它既不在表格里，也不占行。
* 显示的是**域的元素数**：没有三角形数、没有 UV 图/颜色属性计数这类派生统计，
  **也没有世界包围盒和坐标哈希**（MMB-5 明确"不用加包围盒/坐标哈希信息"）。

### 下半：属性表（按域分组）

只列**有属性的域**；没有属性的域不出现（"下方属性没有对应域的就不显示"）。
**分组的顺序就是上半那个元素顺序**（`Vertex` → `Edge` → `Face` → `Face Corner`；曲线是
`Control Point` → `Spline`；网格的组永远排在曲线、点云之前）——上下两半永远同一个走向，
两个节点的块能一行行对着看。L0 `MMB-5` 的原话是"**下方属性表按域分组，组顺序与上方一致**"，
和上半"便于固定显示位置，方便用户比较"是同一条要求。

> 这一条是**改过的**：`mesh.attributes` 枚举的是 Blender 的**存储顺序**（层是按创建顺序
> 存的），而老实现按"第一次遇到的顺序"建组，于是在**属性层创建顺序被打乱**的模型上
> （比如先有 UV/颜色角属性、再有 `sharp_face`、最后才有顶点属性），下半会读成
> `Vertex / Face / Face Corner / Edge`，和上半正好错位——用户在现场就是这么发现的。
> 现在分组按 `COMPONENT_LAYOUT` 排序（`snapshot.py` 的 `_domain_rank`）。
> **组内**条目的顺序仍然是 Blender 的属性顺序（spreadsheet 那种顺序），只有**组之间**
> 走固定顺序。

每组标题是"域名 + Attributes"，而且**用该域在上半用的那个颜色**（L0："domain子类颜色要和
上方信息颜色保持一致"）；条目写成 `名称(类型)`，类型用缩写，**和名称同一个颜色**
（L0："属性颜色统一"）：

```
Vertex Attributes
position(Vec)  mask(Flt)  id(Int)
Control Point Attributes
position(Vec)
Spline Attributes
curve_type(Int)  cyclic(Bol)
```

* 类型缩写：`Flt Flt2 Int Int8 Int2 Bol Vec Col Quat Mat4 Str Menu Rot`；表外的类型原样大写。
* **`position` 会列出来**（MMB-5 点名要 spreadsheet 默认显示的属性）。
* **内部属性不列**（`.` 开头，如 `.corner_vert`、`.edge_verts`）。
* **元素数为 0 的属性不列**（L0 MMB-5："下方属性几何元素数量为 0 的就不显示"）。
  这条正好把**空组件**的属性挡在外面：只输出曲线的节点虽然有一行全 0 的网格域，
  但不会有 `Vertex Attributes` 组。整块属性表里一个组都没有时，下半就是空的。
* `名称(类型)` 是**一个整体**、中间没有空格，和参考图一致。
* 一组的条目超过 24 条时截断，末尾写"… N more"（信息块不做滚动分页）。
* 文本按信息块的宽度**自动换行**；把字号调大时会在编辑器宽度内换行，不会跑出区域。

### 标出"这个节点新增的属性"

节点**同时有几何输入和输出**时，**输出流里有、而它第一个几何输入里没有同名属性**的那些
属性，整个 `名称(类型)` 用**单独的颜色**画出（`Store Named Attribute` 新建的就是这一类）；
其余属性一律用"属性颜色"。

L0 MMB-5 的原文是"用**单独的颜色**标出（默认 `#FFB433`，可在首选项里改）"——默认值就是
`Attribute this node added` 那个取色器。

> **为什么不是"加粗"**（这一轮的历史）：L0 上一版写的是"新增的属性名颜色不变，只**加粗**"，
> 实测**做不到**：`blf` 没有 `font_load`（API 是 `blf.load()`），而 Blender 5.2 的
> `datafiles/fonts/` 里**只有 woff2**、没有任何 TTF/OTF、也没有 Bold 面，`blf.load()` 走
> Freetype 读不了 woff2；"真粗体"只剩"加载 `C:\Windows\Fonts\arialbd.ttf` 这类系统字体"
> （跨平台要退回），备选是"同串文字画两遍、偏移 1px"的假粗体。用户在确认时改选了颜色方案，
> 并把 L0 那句改成了现在这个说法——L0 与实现一致，这条不再有分歧。

**"第一个输入"是编辑器里最上面那一路**——按 `NodeLink.multi_input_sort_id` 最小的取，
不是 `socket.links[0]`（那是个**倒序**表：实测依次连 mesh、curve、points 后 sort id 是
`0,1,2`，而 `links` 读出来是 points、curve、mesh）。这条对 `Join Geometry` 这种
**合并**节点有直接后果：它后面几路输入带来的属性在第一个输入里没有，所以**会被标出来**。
这是 L0 明确要的行为（"多几何输入的节点如 join，新增只和第一个输入比"），不是漏判——
更早的实现拿"全部输入"作并集比，Join 于是什么都不标，那个行为已经被 L0 推翻。

**别的什么都不标**（L0 边界：标色只到属性表）：计数变了、几何位置变了都不标——
`Subdivision Surface` 把 8 个顶点变成 26 个、`Set Position` 带 2.5 的偏移，两者都**一个属性
都不标**（无头测试里就是这两条正对照）。

**两种显示形态**：

1. 有几何输出 → 画输出流（默认）；
2. **只有**几何输入（纯字段节点，如 `PDT Selection`）→ **默认什么都不显示**，只给一行提示；
   首选项 `Show input-only nodes` 打开后改画输入流。

一次读取的代价实测约 **2 ms**；读只发生在**触发那一刻**（L0 边界：块不跟随光标，
所以按住期间不重读）。

---

## 首选项

`Preferences → Add-ons → PDT Node Info`（插件目录名仍是 `pdt_mmb_info`）

MMB-8 允许**四类**可调项；面板顶部只印一句**提醒**（MMB-8 指定的那句，见 MMB-8 一节），
不再显示状态文字：

| 名字 | 默认 | 作用 |
| --- | --- | --- |
| `Show input-only nodes` | 关 | 只有几何输入的节点是否也显示 |
| `Layout · Text size` | 1.0（0.6–2.0） | **整块缩放**：标签、数字、标题、以及它们之间的所有间距一起放大缩小 |
| `Colours`（**可折叠分组**） | 见下 | 9 个 domain 子类的颜色 + 2 个属性颜色 |
| `Reset Colours`（按钮） | — | 把上面 11 个颜色一次恢复成默认配色（按钮不是"设置"，不占 MMB-8 的四类） |

**配色分组的 11 个取色器**（默认值是插件选的和谐配色，不是参考图的像素取样——用户明确说
参考图的颜色比较随意）：

| 取色器 | 默认 | 管什么 |
| --- | --- | --- |
| `Vertex` | `#86B4EA` 蓝 | 上半 `Vertex` 的标签与数字，下半 `Vertex Attributes` 标题 |
| `Edge` | `#EAB486` 橙 | 同上，`Edge` |
| `Face` | `#83D2E2` 青 | 同上，`Face` |
| `Face Corner` | `#A5D9A1` 绿 | 同上，`Face Corner` |
| `Control Point` | `#C7ACEC` 紫 | 同上，`Control Point` |
| `Spline` | `#EA99A0` 红 | 同上，`Spline` |
| `Point` | `#E6B3D3` 粉 | 同上，`Point` |
| `Instances` | `#CFBDB4` 暖灰 | 实例那一行 |
| `Grid` | `#E1D398` 黄 | 体积那一行（`Grid`） |
| `Attribute name & type` | `#C7C9D1` 浅灰白 | 下半**所有**属性的 `名称(类型)` |
| `Attribute this node added` | `#FFB433` 琥珀 | 这个节点新增的属性，整个 `名称(类型)` |

同一个 domain 在上下两半**一定同色**：不是两处各写一遍，而是都查同一张
`(组件, RNA 域) → slot` 表（`DOMAIN_SLOTS`），所以改一处就同时生效。

其余数值都是**固定常量**（不再是偏好）：正文基准字号 12、换行宽度 420 px、每组属性上限 24、
块偏移 18 px、控制台诊断关。要改这些就是改 L0/实现，不是改设置。

---

## 保证（规范性）

每条对应 `spec/node_info.md` 的一个 `MMB-n` 条目，并对应 `tools/pdt/tests/` 里的断言；
没有断言的保证算缺陷。

### MMB-1 只读：不改场景、不改选择、不留垃圾

读取几何靠"临时复制节点树 → 把目标输出接到组输出 → 用临时物体求值"，
**结束即回收**：临时物体、临时节点组、原对象的显示/隐藏状态都不留痕迹。
插件不改节点参数、不改几何、不改文件。

**这份求值器是本插件自己的**（`evaluate.py`，2026-09-14 起；此前复用 `pdt_geo_select`
拾取器的 5 个函数，其传递闭包 453 行里大半是拾取专属的转换与预算），所以
**不依赖任何其它插件**——拾取器禁用、甚至没装，信息块照样读得出同样的数字。
为什么不用 Blender 自己的 Viewer node / 求值日志（那份数据其实正是我们要的，但只在 C++ 里）
见 `../../09-MMB节点信息.md` §8.2。

*断言*：`gui_mmb_node_info.py`（无临时对象/临时节点组残留、原树节点清单不变）、
`test_mmb_node_info.py`（只读读取路径、**屏蔽拾取器后结果不变**、没有任何子模块 import 拾取器）。

### MMB-2 命中与编辑器自身一致

"光标在哪个节点上"必须与 Blender 自己的判定一致：同一批探针点，插件给出的节点
与 `bpy.ops.node.select(location=…)` 的结果**逐点相同**（含缩放不是 100% 的显示）。

*断言*：`gui_mmb_node_info.py` 在节点四周扫 9×9 网格与 oracle 逐点比对（失配 0），
并用"旧规则（视图空间光标比节点空间盒子）"作正对照必须变红；真窗口测试按 L0 验收
**跑在用户配置下**，所以它天然带着用户真实的 UI 缩放。

### MMB-3 折叠节点仍然可读

`H` 折叠后只剩标题带的节点**照样能读**（旧版本会整块跳过）。

*断言*：`test_mmb_node_info.py` 断言折叠盒子的中心规则；
`gui_mmb_node_info.py` 折叠 `Group Output` 后标题带上仍能读到它，且 oracle 同意。

### MMB-4 帧节点不抢命中

`NodeFrame` 永远不是命中结果（它没有几何），帧内部的普通节点按 MMB-2 正常命中。

*断言*：`test_mmb_node_info.py`（只有帧时返回 `None`；帧里的节点仍能命中）；
`gui_mmb_node_info.py`（编辑器认帧的边框、插件返回 `None`）。

### MMB-5 信息内容与"新增属性"

上半是**固定表格**：全部几何**域子类**的元素计数按同一顺序永远列出（不再画 `Mesh`/`Curve`
这类组件名，组件只决定换行），没有的写 `0`、`0` 用该域自己的颜色；每个域一种颜色、数字比
域标签大一号。下半是按域分组的属性表 `名称(类型)`，**组标题复用该域在上半的颜色**、名称与类型
同一个颜色，**分组的顺序就是上半的元素顺序**（`_domain_rank`，见"下半"一节），元素数为 0 的
属性和空组件的属性都不列。**没有包围盒、没有坐标哈希、
没有三角形/UV/颜色属性的派生计数**。同时有输入与输出时，**输出流里新增的属性**用单独的
颜色画出（L0 那句"颜色不变只加粗"正在被用户改成颜色方案，见"标出…"一节），
比对对象是**第一个几何输入**（多输入 socket 里 `multi_input_sort_id` 最小的那一路），别的什么都不标。

*断言*：`test_mmb_node_info.py`——计数半即使在一个只有几何输出的生成器上也报出
**完整的五行固定表格**（`Mesh` / `Curve` / `Point Cloud` / `Instances` / `Volume` 各自的域，
缺的是 `0`），且每一行的域都带 `DOMAIN_COLOR_SLOTS` 里的一个 slot；行里**没有**
`Triangles`/`UV Maps`/`Color Attributes`/`bounds`/`hash` 字样；两档字号满足
`12 < 15`、比例对上参考图量的 ×1.23，且**组标题与域标签同为正文号**（v3 把标题缩回来了）、
只有数字是 15；第一行**只有域标签和数字**、没有任何
组件名；标签与数字同色、`Edge` 用的是 `Edge` 的颜色；`Vertex Attributes` 与 `Face Attributes`
的标题颜色**分别等于**上半 `Vertex` / `Face` 的颜色；属性写成**一个** `position(Vec)` 单元格、
用统一的属性颜色，且没有任何单元格是 `name (Type)` 这种带空格的写法；只有新增属性用
`added` 颜色；只输出曲线的节点那一行全 0 的网格域**仍用各自的域颜色**（不等于
`_COLOR_MUTED`）；属性按 `Vertex Attributes` 分组、`position` 以 `Vec` 列出；内部属性
（`.` 开头）一条都不列；**分组的顺序是上半的元素顺序**——一个属性层按"角属性优先"创建、
存储顺序确实被打乱的网格上，块给出 `Vertex / Edge / Face / Face Corner`（正对照断言
"存储顺序 **不等于** 这个顺序"，所以这条断言分得清修复和缺陷），曲线的两个组是
`Control Point` → `Spline`，`COMPONENT_LAYOUT` 里没有的域排到最后而不是被丢掉、样条域的
两种拼写（`CURVE` / `SPLINE`）占同一个位置；`Store Named Attribute` 只标它新建的 `my_attr`；
`Subdivision Surface` 与带偏移的 `Set Position` 都**不许**标任何名字；**曲线**组件报
`Control Point` N / `Spline` M，域分组是 `Control Point Attributes` / `Spline Attributes`
（Blender 5.2 的 `Curves` **没有** `.splines`，见 `../09-MMB节点信息.md` §5.9），
且它补出来的空网格行**不产生** `Vertex Attributes` 组；`Join Geometry` 的比对对象是
**最上面那一路输入**（断言 `upstream_geometry_source()` 取到 `SetPositionZero` 而不是
`links[0]` 的 `CurveForJoin`），后面几路带来的属性**要被标出来**；一个组件读不出时其余部分
照常显示、失败写进块底部那一行。排版模型（`_panel_lines()`）也在无头测试里断言
（两半之间有空行、不换行时第一行就是网格的四个域）。

### MMB-6 只有几何输入的节点默认不显示

没有几何输出的纯字段节点默认整块不显示，只给一行提示；打开 `Show input-only nodes`
后改画输入流。

*断言*：`test_mmb_node_info.py`（首选项关：无组件且有原因；开：`stream == "input"` 且
计数是输入的 26 顶点）。

### MMB-7 手势：按住显示、松开消失（没有 Pin）

按住显示、松开消失；信息块停在触发位置**不跟随光标**，**拖动也不隐藏**
（"按住拖动时不移动，也不用让信息块消失"）；**没有钉住、没有关闭按钮**。

*断言*：`gui_mmb_node_info.py` 的模态生命周期段——80 px 的拖动后块的位置、所读节点、
以及"块还在"三者都必须成立；旧钉住键 `P` 与一次随机点击都必须什么都不做；
`test_mmb_node_info.py` 断言 `_CLASSES` 里没有 pin/close 操作符、`_Session` 里没有
钉住/拖动状态、模块里没有 `PIN_KEY`/`PAN_THRESHOLD_PX`。

### MMB-8 不绑快捷键：放一条未绑定条目，键由用户在 Keymap 里指定

加载项**不绑任何快捷键**，也**不碰用户的键位表**；它只做两件事：

1. 往**自己的 addon 键位表**的 `Node Editor` 里放**一条 `type = NONE` 的未绑定条目**
   （`keymap.py` 的 `register_addon_keymap()`，`unregister()` 时按原样撤回）——因为 Keymap
   编辑器的 `Search by Name` 只筛"已经存在的条目"，没有这条条目用户就**根本搜不到这个操作符**
   （工厂设置下的实测 bug，见"先绑定"一节）。addon 键位表是 Blender 合并到用户/默认键位表
   **之上**的那一层，**不写进 `userpref.blend`**，也不会像直接写用户键位表那样把内置键位表
   挡掉（`pdt_geo_select` 的注释记录了那次事故；它同样只在自己表里注册一条，只不过带默认键）；
2. 在首选项面板上印**一句**提醒（MMB-8 指定的那句，`PDT Node Info` 处替换成实际操作符名）：
   `Assign one shortcut yourself: Preferences>Keymap>PDT Node Info`——**不加别的提示文字**。

L0 `MMB-8` 这一轮把上面那条纪律写成了**三条**，实现与测试逐条对应：

| L0 的话 | 实现 | 断言 |
| --- | --- | --- |
| **keymap 新建未绑定条目** | `register_addon_keymap()` 建一条 `type=NONE`（重复注册不叠加） | 无头：注册项恰好一条且 `type == "NONE"`；真窗口：addon 表里恰好一条、且 `item_is_bound()` 为 False |
| **不写 default 表** | 只动 `keyconfigs.addon`，default/user 表一个字不写 | 真窗口：`our_items(keyconfigs.default) == []`；注册前后各 keymap 条目数快照相等 |
| **`unregister()` 不动用户绑的键** | `unregister_addon_keymap()` 只撤回自己那条 | 真窗口：`unregister()` 后用户在**自己**键位表里绑的键（`Alt+MMB`，`user_modified=True`）逐字段不变 |

操作符 `bl_options` 不含 `INTERNAL`，所以它在搜索与 Keymap 编辑器里**可见、可绑定**。
`keymap.py` 的 `binding_is_active()` 只读，只在启动日志里报"用户是否真的绑了一个键"；它**不把那条
未绑定条目算作已绑定**（`type == NONE` 不算），否则什么都没绑时状态会显示"好了"。滑块是**整块缩放**
（`text_scale`，0.6–2.0）：标签、数字、标题与所有间距一起缩放。

*断言*：`test_mmb_node_info.py` 断言注册项**恰好一条**且 `type == NONE`、重复注册不叠加、
`binding_is_active()` 对未绑定条目为 False 而对真绑了键的条目为 True；断言 `REMINDER` 就是
MMB-8 那句话（`PDT Node Info` 取自操作符自己的 `bl_label`）、面板印它、且**没有**别的提示
文字；断言没有绑定操作符、**没有任何一个子模块**定义 `_bind_in_user_keymap`、启动不挂绑定定时器。
`gui_mmb_node_info.py` 在**用户配置**下断言：addon 键位表里恰好一条我们的**未绑定**条目、
add-on **不往 default 表写**、`Search by Name` 的判据（照抄 `rna_keymap_ui.draw_filtered`）在
**用户键位表**里能搜到 `PDT Node Info`、`unregister()` 会把条目收回**且不动用户自己绑的键**
（用户配置里本来就有一条手工绑的 `Alt+MMB`：实测 `user_modified=True`，
见 `qa/mmb/mmb_binding_probe.py`）；`draw_panel()` 在 1.0 / 1.8 两档字号下都要在真实 GPU 上下文里
画出来（并断言换行宽度不超过区域宽度，见 MMB-5 的排版断言）。无头部分断言偏好集合**恰好等于**
`{show_input_only, text_scale}` ∪ `{color_*}`（MMB-8 的四类，多一个少一个都算改规范）、
11 个取色器都是 `COLOR`/`FLOAT`/3 分量、默认值等于 `DEFAULT_COLORS`、配色无重复、
`DOMAIN_COLOR_SLOTS` 与调色板一一对应；改过的颜色会进调色板，`Reset Colours`
（`pdt.mmb_reset_colors`，在 `mmb._CLASSES` 里）能把 11 个颜色一次还原；
draw 体里确实建了可折叠分组 `pdt_mmb_info_colors` 并带这个按钮。
真窗口的键位证据：`qa/mmb/mmb_keymap_probe.py`（启用前后 0 → 1 条，带正对照）与
`qa/mmb/mmb_keymap_startup_probe.py`（重启后条目仍在；用户绑键后 `binding_is_active()` 为 True，
重启后仍为 True）。

> 代码在 `scripts/addons_core/pdt_mmb_info/` 里是**一个文件一件事**（`preferences.py` /
> `interface.py` / `operators/` / `session.py` / `hit_test.py` / `geometry.py` /
> `evaluate.py` / `snapshot.py` / `keymap.py` / `utils/`），`__init__.py` 只留 `bl_info` 与注册；
> 文件地图与依赖方向见 `../../09-MMB节点信息.md` §8。

### MMB-9 失败可见，不静默

三种失败各有各的话，且**无论如何都写日志文件**（不依赖控制台）：

| 日志里看到 | 含义 |
| --- | --- |
| `keypress` → `no-node-under-cursor` | 按键到了，但光标不在节点上 |
| `node-under-cursor` → `read-FAILED` / `read-traceback` | 读几何时抛异常（带完整 traceback） |
| `session-started` 但没有块 | 只可能是绘制环节（`draw-FAILED`） |

日志路径：系统临时目录下的 `pdt_mmb.log`（环境变量 `PDT_MMB_LOG` 可改；插件注册时会把实际
路径打印到控制台），每次启动截断一次。

*断言*：读取失败被捕获并在 HUD 上给出原因（`test_mmb_node_info.py`：
非几何节点报原因而不是抛异常）。

### MMB-10 只报告当前编辑的树

子组内部不在范围内；只读 `space.edit_tree`（或 `node_tree`）里的节点。

*断言*：无独立断言（实现与文档一致即可），见"已知边界"第 1 条。

---

## 诊断：按键没反应时按这个顺序查

1. **先确认你绑了键**：`Preferences → Keymap`，搜索框输 `PDT Node Info`（或 `pdt`）——
   插件已经放了一条**未绑定**的条目在那里，点开它给个键。`bl_options` 里不能有 `INTERNAL`
   （有的话操作符在搜索与 Keymap 编辑器里消失，见 MMB-8）；条目必须存在，否则那个搜索框
   什么都搜不到（这就是工厂设置下报过的 bug，见"先绑定"一节）。
2. 绑定要落在**用户**键位表里（派发读的是它，addon 表会被合并进去）——你在 Keymap 编辑器里
   改的就是它；加载项自己不会替你绑键（MMB-8）。
3. 看那个 `pdt_mmb.log`：有没有 `keypress`（键位没到）→ 有没有 `node-under-cursor`
   （光标位置）→ 有没有 `session-started`（读到了）→ 有没有 `draw-FAILED`（画失败）。

---

## 已知边界

1. **节点组（子组）内部**没有接进来：读的是"当前编辑的树"里的节点（最外层树已实测）。
2. **多几何输出**的节点只报**第一个** enabled 的几何输出（面板不区分是哪一个）。
3. 节点上有**多个几何输入**时（`Join Geometry`、`Boolean`），只取**最上面那一路**
   （`multi_input_sort_id` 最小，即编辑器里排在最上面的连线；见 MMB-5）。
4. 上游是**实例**时临时树只能吃一个数据块：这条路径会报错并在 HUD 上写明原因。
5. **标色只到属性表**（MMB-5），不做逐属性数值统计、也不标计数的变化；
   **不显示包围盒与坐标哈希**（L0 明确不要）。
6. 标出的比对对象是**第一个**几何输入（L0 明确），不是全部输入的并集；
   `Join Geometry` 这类合并节点因此会把后几路输入带来的属性标出来——那是要求的行为。
7. **网格物体上读只有曲线的节点**会多出一行全 0 的网格域：Blender 给网格物体求值时
   总会补一个空 Mesh 组件。因为计数半是固定表格，这行本来就该在，而且**用网格各域自己的
   颜色**（不变暗）；它的属性和 0 元素属性都不列，所以不会多出一个空的属性组。
8. **信息块不跟随光标**、拖动也不消失（L0 边界 + MMB-7）；不做滚动分页，
   每组属性超过 24 条只显示"… N more"。
9. **没有 Pin**、没有关闭按钮（MMB-7）；`Esc` 仍可提前结束（实现细节）。
10. **帧节点不参与命中**（MMB-4）；**编辑器没画过的节点**命中不到。
11. `UI_SCALE_FAC` 取全局 `preferences.system.ui_scale`：跨不同 DPI 显示器、且某个窗口
    尚未重算 DPI 时，该窗口按全局值算。
12. `Volume` 的元素是它的 `grid`，已进固定表格；**蜡笔（Grease Pencil）暂不列**
    （L0："先不管蜡笔"），所以它不占行（见"信息块里有什么"的表格）。
13. **实例那一行的标签就是 `Instances`**：它没有 domain 子层，而 L0 又不要组件名，
    所以用组件自己的名字当标签（照参考图的 `Instances 0`）；下半对应的组标题也是
    `Instances Attributes`。
14. 某个 `(组件, 域)` **不在 `DOMAIN_SLOTS` 里**时（Blender 将来加了新域）：仍然会列出来，
    颜色退回统一的属性颜色——不会因为少一个颜色就把行吞掉。它在下半同样排不上固定顺序
    （`COMPONENT_LAYOUT` 里没有它），于是**排在最后**，几个这样的组之间保持先遇到的顺序。

---

## 自己验一遍

下面的命令都写**相对于 Blender 源码检出根目录**的路径（`tools\pdt\` 就在它下面）；
`$exe` 换成你自己的 `blender.exe`，或者先 `. tools\pdt\pdt_common.ps1` 用 `$PdtBlenderExe`。

```powershell
$exe = '<build>\bin\Release\blender.exe'
# 改完插件**必须**先同步进 build 目录：测试 import 的是已安装副本（整目录拷 + 真 Blender 里加载一次）
. tools\pdt\sync_addons.ps1
# 无头：固定表格与域配色、两档字号、属性分组与新增属性颜色、命中盒子的换算与顺序、偏好与调色板
& $exe -b --factory-startup --python-exit-code 1 --python tools\pdt\tests\test_mmb_node_info.py
# 真窗口：**不加 --factory-startup**（L0 验收要求用用户配置跑，UI 缩放的坑只有这样才现形）
& $exe --enable-event-simulate --python tools\pdt\tests\gui_mmb_node_info.py
```

开发期还有几个一次性探针（命中盒现场、版式截图、键位可见性与"重启后是否仍在"），
它们不是仓库的一部分，按上面的测试脚本自己搭即可。

设计与实测的完整记录（坐标系、键位六轮、每个坑的现场证据）是开发期文档，
不在本仓库里。
