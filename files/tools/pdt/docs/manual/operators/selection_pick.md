# Selection Pick（视口拾取器）

> 操作符：`pdt.selection_pick_modal`（`pdt.selection_pick` 是同一个入口的旧 id）
> 默认快捷键：**没有**——键由你自己绑，见下面「打开」一节
> 决策层：`../../spec/selection_pick.md`（`PICK-1`…`PICK-6`）

在 3D 视口里用鼠标挑元素，然后把这批元素**写回** `PDT Selection` 节点的字符串参数。
写回去的就是一个普通的选择字符串（`0-1 4-5`），可以接着手改、也可以反过来当表达式写。

它把"手挑"和"写表达式"统一成同一种产物：**参数里存的永远是选择字符串**。

---

## 打开

触发的是**字符串参数本身**（`PICK-1`）：**socket、它的名称 label、或它的输入框**，三个里命中任意一个都算。
socket 只是连线的那个小点、很难瞄准，所以只要光标停在参数这一行上就行。

1. 在 `Preferences → Keymap` 里搜索 `Pick Selection`，给
   **`Pick Selection (Viewport)`**（`pdt.selection_pick_modal`）绑一个键。
   > 拾取器**不设默认快捷键**，也不替你写键位；它只在**自己的 addon 键位表**里放一条
   > **未绑定**条目，好让你在 Keymap 编辑器里搜到它（那个搜索框只筛"键位表里已有的条目"）。
   > 首选项里就一句提醒：`Assign one shortcut yourself: Preferences>Keymap>Pick Selection (Viewport)`。
2. 把光标停在那一行上（例：`PDT Selection` 的 `Selection` 名称 / 输入框），按键。
   * 光标**没停在字符串参数上** → 只给一行提示（没弹菜单）；
   * 命中的是**输出 socket** → 提示这是输出，拾取只写输入参数；
   * 那个节点**没有几何输入**（连一个 `Geometry` 输入 socket 都没有）→ 拒绝 + 提示；
   * 其它情况 → 弹**一级 pie menu**：选这次要拾取的**元素种类**（七类，和节点 `Domain` 一样）；
   * 选 `Instance` → 弹**二级 pie menu**：选 `Instance Mode`（三种）。
3. 于是"写哪个参数"由**你指着的那一行**决定，不用先选节点、也不存在"猜哪个参数"。

也可以从操作符搜索（`F3`）里执行 `Pick Selection (Viewport)`：那条路没有按键事件，
于是用**事件系统最后记录的鼠标位置**做命中判定——光标同样必须停在那行上。

> `pdt.selection_pick`（旧 id）跑的是同一个入口，行为完全一致：早先它只开会话、不带模态循环，
> 结果 Esc 和右键对它无效。

### 两个 pie menu 的排布

一级 pie 的七个切片按固定槽位铺开（Blender 的 `radial_dir_order`：**西、东、南、北**，再是四个斜角），
顺序就是节点 `Domain` 菜单的顺序：

| 槽位 | 西 | 东 | 南 | 北 | 西北 | 东北 | 西南 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 元素种类 | `Vertex` | `Edge` | `Face` | `Control Point` | `Spline` | `Point` | `Instance` |

二级 pie 只有三项，落在西 / 东 / 南：`Point Cloud`、`Point Cloud with Reference`、`Realized Instance`。
每个切片带**该种类自己的 Blender 图标**（`utils/constants.py` 的 `DOMAIN_ICONS` / `INSTANCE_MODE_ICONS`；
图标名逐个对着本构建的图标枚举断言，因为 Blender 不认识的图标名会在 `draw()` 里抛异常、整个菜单空白）。
图标取自 **Spreadsheet 窗口自己的域行**（`spreadsheet_dataset_draw.cc` 的 `Spreadsheet Data-Block` 面板），
所以同一元素在两处看起来一样：

| 切片 | 图标 | Spreadsheet 里的出处 |
| --- | --- | --- |
| `Vertex` / `Edge` / `Face` | `VERTEXSEL` / `EDGESEL` / `FACESEL` | `mesh_domain_to_icon()` |
| `Control Point` | `CURVE_BEZCIRCLE` | `curves_domain_to_icon()`（`AttrDomain::Point` 在曲线上） |
| `Spline` | `CURVE_PATH` | `curves_domain_to_icon()`（`AttrDomain::Curve`） |
| `Point` | `POINTCLOUD_POINT` | `PointsViewItem`（Point Cloud 下的 `Point` 行） |
| `Instance` | `EMPTY_AXIS` | `InstancesViewItem`（`Instances` 行） |

注意曲线两类**不用**对象数据图标（`CURVE_BEZCURVE` / `OUTLINER_DATA_CURVES`），实例也**不用**
修改器图标（`MOD_INSTANCE`）——那三个是 Spreadsheet 里不出现的画法，测试专门钉住这一点。
二级 pie 是 `POINTCLOUD_DATA` / `OUTLINER_OB_EMPTY` / `PARTICLE_POINT`（`Point Cloud` 组件行与实例参考行）。

**这份几何里没有的种类不出现**（L0 `PICK-1`：当前节点上没有的类型不出现），但**槽位表照旧**：
七个位置仍然按上表占住，空出来的那个位置画成一个 `SPACE` 分隔符——它不画任何像素、
也点不到（`button_contains_point_px()` 只认按钮），所以你看不到那一项，而**其它种类不会挪位置**。
槽位分配是"按 pie 里的项目个数依次发"（`LayoutRadial::resolve_impl()`），少一个项目就会把后面的
都挪一格（`Point` 会跑到西边去），所以缺的种类用占位符顶住而不是直接删掉。
判定与实现在 `menus.py` 的 `domain_pie_slots()`（纯数据，测试可以不用窗口就断言槽位表）。

---

## 操作

进入拾取后，原对象被隐藏、进入编辑模式，你在视口里正常选择：

| 操作 | 结果 |
| --- | --- |
| 左键点击 / 框选 / `Ctrl`+点击 … | **照常传给视口**，就是你平时选择元素的操作 |
| 鼠标中键 / 滚轮 | **照常传给视口**，导航不受影响 |
| **`Enter`** / 小键盘 `Enter` / **空格** | **确定**：把当前选择写回参数 |
| **`Esc`** / **右键** | **取消**：参数保持原样，恢复现场 |

除上面这些按键之外的所有事件都**原样透传**给 Blender（`PASS_THROUGH`），
所以拾取期间视口操作和平时一样。状态显示在窗口状态行与视口左下角的一行浮字里
（拾取种类 / 已选数量 / `Enter apply · Esc cancel`）。

> 拾取器**没有节点编辑器侧栏面板**（L0「明确不做」）。所有选择都在拾取过程中问：
> socket 由光标决定、种类与实例模式由 pie menu 决定，面板只会重复这些状态。
> 少数仍然需要入口的操作（`Apply Selection` / `Cancel Selection` / `Clear Selection Parameter` /
> `Clean Up Leftovers`）留在注册表里，用 `F3` 搜得到。

---

## 参数写到哪

拾取器**只写字符串参数**，写的就是**光标所在的那一行**。

* 光标在字符串参数的 socket / 名称 / 输入框上 → 写它；
* 光标在别的参数（`Geometry` 等）上 → 拒绝并说明那是什么类型；
* 光标在**输出 socket** 上 → 拒绝并说明拾取只写输入参数；
* 光标不在任何一行上（节点标题、空白、节点外）→ 拒绝并提示把光标放到参数那行上。

从 `F3` 进来（没有按键坐标）时用的是同一套判据，只是位置来自事件系统。
如果节点上有多个字符串参数、而你没有指着某一个，旧入口（`choose_string_target()`）
会**拒绝而不是猜**：

```
'PDT Selection.001' has 2 string parameters; put the cursor on the socket to pick into and press the shortcut
```

**节点上不留任何记录**（L0 `明确不做`：不记录操作过的字符串参数用了什么选择模式）：
拾取只在节点上写**目标字符串参数**本身（以及长选择时的 `@prop`），不会记下"上次用的是哪个参数"。
早先版本会在节点上写一个 `pdt_pick_target` ID 属性记住上次的选择，那**已经删掉**——
每次拾取都重新由光标决定（PICK-1），没有光标时就是上面那条拒绝。

选择太长、字符串 socket 装不下时，完整内容会存进**节点自己的 ID 属性**，
socket 里只留 `@prop`；这一层对使用者透明（见[节点文档](../nodes/pdt_selection.md)）。

### 元素种类（Element Kind）

一级 pie menu 给的是**和节点 `Domain` 一样的 7 类**
（`Vertex` / `Edge` / `Face` / `Control Point` / `Spline` / `Point` / `Instance`），
它决定这次拾取**烤哪个组件**、进哪种编辑模式、写回去的是谁的序号。

* **它不改节点，拾取器也不读节点的 `Domain`**（L0「明确不做」：不读取被识别节点的 domain 属性）：
  烤什么**完全由你在这里选的那一项**决定。写回去的序号会被节点按**它自己的 `Domain`** 解释，
  所以要么两边一致、要么接受这个含义（例如你只想用拾取器挑一批序号）。
* **有没有那个节点属性都给全 7 类**：老节点（或任何被拾取器指向的树，例如一个组节点）
  没有 `Domain` 属性，菜单照样列 7 项。
* **选中了几何里没有的种类会被拒绝**，并说明这份几何有什么（例如
  `cannot pick Face elements: this geometry has no Mesh component (it has: Curve)`）——
  这也是在混合几何里**临时烤另一个组件**的正路（节点 `Domain` 不动）。
  菜单里那几项**根本不出现**（空槽位是看不见也点不到的 `SPACE` 占位符），
  所以正常路径下选不到它们；`execute()` 里那道闸门是给"绕过菜单"的调用留的
  （快捷键、脚本、`F3` 搜索）。
* 实例还要多问一句：选 `Instance` → **二级 pie menu** 选 `Instance Mode`
  （`Point Cloud` / `Point Cloud with Reference` / `Realized Instance`，见下面的限制表）。

---

## 拾取的是"哪份几何"

拾取时需要一份**真实求值后的几何**，才谈得上"挑第几个点"。所以：

* 从**目标修改器之上**的所有修改器先求值（`Array`、`Mirror`、别的几何节点…），
  临时对象拿到的就是**该修改器真正收到的几何**；
* 目标修改器**之下**的修改器不参与——索引对齐的就是"进入该修改器的几何"；
* 同一节点组被多个对象使用时，**看当前活动对象**：用了这棵树的那个对象（它在树上的第一个
  几何节点修改器）就是拾取源；整棵树只被**唯一一对**引用时不必先激活对象；活动对象不匹配
  而候选有多个时**明确拒绝并列出候选**，绝不默默换一个对象烤；
* 节点上的**组输入覆盖**（例：`Offset Z` 被这个对象改成 3）会一起带到临时对象上，
  所以"拾取看到的几何"就是你在这个对象上看到的几何；
* 节点位于**子组内部**时也支持：按面包屑逐级复制节点树，在组里选的点也能写回内层节点。

### 预选：只认序号（保证 G3）

打开拾取时，参数里已有的内容会被**预选**出来，好让你"接着上次改"。但：

> **视口只预选序号与 `*`。组（`A`、`arm*`）与属性模式（`@position.z>0`）不参与预选，
> 也不出现在拾取器的任何文本里。**

也就是说参数写 `A` 时，节点**会**选中 `group:A` 那批元素，但视口**不会**预先高亮它们；
写 `0-1 A` 时只预选 `{0,1}`。

理由：在 Python 里复刻通配匹配、跨域转换和属性求值，很容易预选出"节点其实并不选"的东西。
现在的契约是**预选集合 ⊆ 节点选择集合**，跨语言测试直接断言这条包含关系。

拾取器算不出来的记号一律**往安全方向**算：

| 记号 | 拾取器的处理 | 例 |
| --- | --- | --- |
| 加进集合（`A`、`0-1 A`） | 贡献"零" | `0-1 A` → 预选 `{0,1}` |
| 取交集（`0-7 &A`） | 交集里也贡献"零" | `0-7 &A` → 预选空 |
| 被减（`0-1 ^A`） | 当作"它可能选了全部" | `0-1 ^A` → 预选空，而不是 `{0,1}` |
| 取补（`!A`、`^A`） | 补集算不出来 → 空 | `!A` → 预选空，而不是全部 |

> ⚠️ 后两行是这一版**修掉的**行为：`!A` 和 `^A` 以前会把整个几何预选上，
> 违反"预选 ⊆ 节点选择"（节点选的是"非 A"）。别改回去。

组属性本身**照旧会穿过烘焙**，因为节点要用它。

---

## 现场保护与异常处理

拾取过程中会临时隐藏原对象、建临时对象与临时节点组。以下情况都有明确行为，**不会留下烂摊子**：

| 情况 | 行为 |
| --- | --- |
| 中途退出编辑模式 | 会话**暂停**，计数仍在；可以回编辑模式继续，或直接 `Apply Selection`（`F3`） |
| 编辑了网格（拓扑变了） | **拒绝**写回，并说明原因——索引已经对不上了 |
| 切到了别的对象 | **拒绝**，提示是哪个对象 |
| 拾取对象/网格被删除 | 会话标记为 `missing`，下一次操作自动清理 |
| Blender 崩溃 / 强退 | 残留的临时对象由 **`Clean Up Leftovers`**（`F3`，或 `pdt.selection_cleanup`）清理；启动时也会收拾一次 |

修改器栈原样恢复：拾取不会改动原对象的修改器、也不会留下临时修改器。

---

## 可用的操作符（`F3`）

| 操作符 | 作用 |
| --- | --- |
| **`Pick Selection (Viewport)`**（`pdt.selection_pick_modal`） | 触发拾取：命中字符串参数 → pie menu → 编辑模式 |
| **`Apply Selection`**（`pdt.selection_apply`） | 把当前选择写回参数（暂停状态下用它收尾） |
| **`Cancel Selection`**（`pdt.selection_cancel`） | 丢掉会话、恢复现场 |
| **`Clear Selection Parameter`**（`pdt.selection_clear`） | 清空当前节点（光标下的节点，否则选中的节点）的选择参数（连同 `@prop` 里存的长选择） |
| **`Clean Up Leftovers`**（`pdt.selection_cleanup`） | 清理被中断的会话留下的临时数据块 |

---

## 限制

| 限制 | 说明 |
| --- | --- |
| 拾取的是**节点处的几何**，可以是 **mesh / curve / point cloud / instances 四种组件** | 一个几何集合里同时有多种组件时，**一级 pie menu 选中的元素种类**决定这次拾取编辑哪个组件：`Vertex`/`Edge`/`Face` 进 `EDIT_MESH`、`Control Point`/`Spline` 进 `EDIT_CURVE`、`Point` 进 `EDIT_POINTCLOUD`、`Instance` 进 `EDIT_POINTCLOUD`（每个实例一个点）。选中的种类这份几何里没有就**拒绝**，并列出它有什么。 |
| **实例：二级 pie menu 的三种方式**（L0 `PICK-3`） | **`Point Cloud`**：把 `GeometrySet.instances_pointcloud()`（每实例一个点，点在**实例原点**上，并带走全部实例属性）复制成一个临时点云，在 `EDIT_POINTCLOUD` 里点选——**写回的点序号就是实例序号**；源对象**隐藏**。实例再多也不卡（10 万实例 ~0.13 s / 峰值 214 MB），但视口里看到的是**原点**而不是实例几何。**`Point Cloud with Reference`**：同样的点，但**源对象保持可见**当视觉参考（默认方式，只有这套原点时很难分清谁是谁）。**`Realized Instance`**：把实例展开成真几何再选（见下一行），源对象同样保留。 |
| **`Realized Instance`：选真几何，折回实例序号** | 拾取器在**自己的临时树副本**里插两个节点——`Store Named Attribute`（`pdt_instance_index` = `Index + 1`，`Instance` 域）与 `Realize Instances`——用户的树一个字节都不动。**用户点选真几何的元素**（网格顶点、曲线控制点），写回时按属性折成**实例序号**；不是实例生出来的元素带着哨兵值 0，会被丢掉（`Join Geometry` 混进别的几何时也不会误算）。你点的是展开后几何自带的那种元素（网格 → 顶点，曲线 → 控制点）；在视口里临时切到边/面子模式选的边面，按其顶点折算。⚠️ 编辑模式要复制几何，所以有**预算闸门**（默认 100 万元素，实测 4.82 M 顶点 = 1.74 s / 峰值 5.5 GB），超了就拒绝并提示改用 `Point Cloud`。 |
| 实例拾取**不看节点的 `Domain`** | 三个实例模式都由 pie 的选择决定（L0「不做」：不读取被识别节点的 domain 属性），写回的是**实例序号**。节点的 `Domain` 若还是别的种类，节点会把这些序号按它自己的种类解释——要用得上就把节点的 `Domain` 改成 `Instance`，拾取器不会替你改、也不会因此拒绝。 |
| 如果几何是**上游修改器**产生的实例，目前不能拾取 | 临时树的 Group Input 只能喂数据块，实例不是数据块：会给出明确原因并提示加 `Realize Instances`（`../../archive/08-实例支持评估.md` §11.4）。 |
| **曲线的 `Spline` 域：选点 → 写回样条序号** | 曲线编辑模式**没有**按样条选择的功能，所以拾取器反过来做：你在视口里选**控制点**，写回的是这些点**所在的样条**序号；预选一个样条就是把它的**全部控制点**选上（所以往返一致）。只要一根样条上**有一个**点被选中，就算选中了这根样条。 |
| 拾取元素种类与几何组件**必须对得上** | `Vertex`/`Edge`/`Face` 是网格元素、`Control Point`/`Spline` 是曲线元素、`Point` 是点云元素、`Instance` 是实例。种类指向的组件这份几何没有时（比如 `Edge` 但上游只有曲线），拾取会**拒绝**并说明这份几何有什么。 |
| 点云与曲线编辑模式没有"点/边/面"子模式 | 只有网格有 `mesh_select_mode`；曲线、点云与实例拾取都按"点"处理。点云的选中状态就是它自己的 `.selection` 属性（缺失 = 全选），Blender 的 `Point Cloud ▸ Select All` 与视口点选都写它。 |
| 必须从**对象模式**开始 | 编辑模式套编辑模式没有意义，会直接拒绝并提示。 |
| 不能嵌套会话 | 已经有一个拾取会话时，再触发会拒绝（先 `Apply`/`Cancel`）。 |
| 预选不含组与属性 | 见上面的 G3。 |
| 临时对象只有一种类型 | 同一时刻只把选中的那个组件建成临时对象，其它组件不会显示出来（可作为后续增强）。 |
| 索引是声明式的 | 写回的是序号，上游拓扑一变含义就漂。 |

---

## 代码结构

> 代码在 `scripts/addons_core/pdt_geo_select/` 里是**一个文件一件事**的多文件包（与 `pdt_mmb_info`
> 同一套约定）：`parser.py` / `targets.py`（文法与写哪个参数）、`hit_test.py`（触发预检）、
> `geometry.py` / `bake.py`（节点处的几何 → 临时对象）、`selection.py` / `instances.py`（临时选择与
> 三种实例模式）、`session.py` / `state.py`（会话与共享状态）、`overlay.py` / `menus.py`（状态行/overlay 与两个 pie）、
> `operators/`（触发器与模态循环、`F3` 里的四个动作）、`preferences.py` / `keymap.py`（`PICK-4` 的提醒与
> 那条未绑定键位）、`utils/constants.py`（词汇表与尺寸）。
> `__init__.py` 只留 `bl_info` + 文件地图 + 注册；文件地图与依赖方向见
> `../../12-拾取器重构交接.md` §11。

