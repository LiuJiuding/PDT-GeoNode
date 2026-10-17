# PDT Blender 用户手册

本手册说明这个 Blender 分支**新增**给用户的东西：两个几何节点 `PDT Selection` 与 `PDT Wrangle`、
一个视口拾取操作符 `Selection Pick`，一个按住中键读节点几何的插件
`Node Info`（`Alt+MMB`），以及**上游行为的少数几处记录在案的改动**——目前是
Outliner 拖放设父级（[`operators/outliner_parent_drop.md`](operators/outliner_parent_drop.md)，
`OPD-1…9`）与 USD 导入器的两个选项（`../18-USD导入-PointInstancer自定义primvar.md`、
`../19-USD导入-静态几何也挂几何缓存.md`）。上游 Blender 的功能请查
[官方手册](https://docs.blender.org/manual/zh-hans/latest/)。

除上面这些记录在案的改动外，你的 Blender 和官方版一样。

---

## 这本手册的地位：它是**规范**，不是说明

> **本手册是顶层设计文档。** 节点的行为、socket 提示、面板文字与测试**都必须与它一致**。
> 两者不一致时，改的是实现，不是手册。

具体含义：

* **语义以 `reference/selection_syntax.md` 为准。** 那份文档里的表是规范性的；
  代码、测试期望值、UI 提示文字都从它推导。
* **每个"保证"都有测试兜底。** 下面的每一条保证都对应 `tools/pdt/tests/` 里的断言；
  如果某条保证没有测试，那是一个缺陷，应该补测试而不是删保证。
* **改行为 = 改手册 + 改测试 + 改实现，一次提交里完成。** 只改其中一两处，
  就是下一轮"文档说 A、程序做 B"的根源——这个仓库已经因为这种事付过学费
  （见 `../archive/04-视口选择.md` 的 P13 段：文件注释承诺 `@P` 能用，而它从来没生效过）。

---

## 目录

| 文档 | 内容 |
| --- | --- |
| `reference/selection_syntax.md` | **选择字符串的完整语法与求值语义**（规范性） |
| `reference/wrangle_functions.md` | **Wrangle 的语言、属性操作符与全部函数**（速查；写法参照 Houdini 的 VEX 文档） |
| `nodes/pdt_selection.md` | `PDT Selection` 节点：接口、行为、示例 |
| `nodes/pdt_wrangle.md` | **`PDT Wrangle`** 节点：在节点里写类 VEX 代码，语言速查、逐元素语义、保证 `WR-1`–`WR-14` |
| `operators/selection_pick.md` | 视口拾取器：怎么用、参数写到哪、边界在哪 |
| `operators/node_info.md` | **Node Info**：按住自定义快捷键读节点几何，命中判据、HUD 内容、保证 MMB-1–MMB-10 |
| `operators/viewer_overlay.md` | **Enhanced Viewer Overlay**：把 viewer 属性画成方向线段 / 旋转坐标轴，保证 EVO-1–EVO-7 |
| `operators/build_branding.md` | **构建标识**：分支怎么在标题栏/About 里报名字，文件怎么带标识，官方版打开时看到什么（BRAND-1–5） |
| `operators/outliner_parent_drop.md` | **Outliner 拖放设父级**：拖一个物体到另一个物体上设父级，默认档不写父级反矩阵、Alt 才写（OPD-1–OPD-9） |

开发/构建相关的文档在上一级目录（`../00-当前状态.md` 是新会话入口，
`../01-编译加速.md`、`../02-版本管理.md`、`../03-节点开发与测试.md`）。

---

## 它解决什么问题

Blender 的几何节点里，"按序号选中一部分元素"没有顺手的做法：
`Index Switch`、`Compare` 拼起来很长，而且没法把"选中的这批点"当作一个可复用的描述存下来。
Houdini 的做法是**一个选择字符串**（group syntax）：

```
0-10 12 15-99:2        ← 序号与区间
rim                    ← 组名
0-10 rim ^5            ← 混合，`^` 排除
```

本分支把这个习惯搬进 Blender：

* **`PDT Selection` 节点**——把选择字符串变成 `Boolean` 字段，接到 `Delete Geometry`、
  `Set Position`、`Selection` 输入……任何吃 Boolean 选择的地方；
* **视口拾取器**——在 3D 视口里直接用鼠标框选，然后把它**写回**节点上的字符串参数，
  于是"手挑"和"写表达式"产出的是同一种东西，可以互相接着改。

---

## 快速上手

**用表达式：**

1. `Shift+A` → `PDT` → `PDT Selection`。
2. `Geometry` 接上游，`Selection` 填 `0-1 4-5`，`Domain` 选 `Point`。
3. `Selection` 输出接到 `Delete Geometry` 的 `Selection`。
4. 也可以只把 `Selection` 接出去、`Geometry` 不接（见节点文档的"只接 Selection"一节）。

**用鼠标拾取：**

1. 先绑键（拾取器**不设默认快捷键**，`Preferences → Keymap` 搜 `pdt` → **Pick Selection (Viewport)**）。
2. 把光标停在目标节点（任何有几何输入的节点）的**字符串参数那一行**上——socket、名称或输入框都算，
   然后按你绑的那个键：弹出一级 pie menu，选**元素种类**；选 `Instance` 会再弹二级 pie menu
   选 `Instance Mode`。
3. 进编辑模式后在视口里选元素，**`Enter`**（或空格）写回，**`Esc`**（或右键）取消。
4. 写回的就是一个普通的选择字符串，可以继续手改。

> 光标没停在字符串参数上、或那个节点没有几何输入时，拾取器**不弹菜单**，只给一行提示说明原因。
> 烤哪个组件**只由 pie menu 的选择决定**——拾取器不读节点的 `Domain`。

**用中键读一个节点流出了什么几何：**

1. 先绑键：`Preferences → Keymap` 搜 `pdt` → **PDT Node Info** → 给它一个键 + 鼠标中键
   （加载项**不会**替你写键位）。
2. 光标停在节点上，按住那个键：出现信息块（标题、参数行、空白都算）。
3. 按住时按 **`P`** 钉住，点 **`×`** 或按 **`Esc`** 关闭。详见
   [Node Info](operators/node_info.md)。

---

## 保证（规范性）

这些是**必须永远成立**的性质。每一条都在 `tools/pdt/tests/` 里有对应断言。

### G1 安全失败：写错语法不会毁掉场景

选择字符串有语法错时，节点**不报错崩溃、也不清空几何**：

* `Geometry` 输出**原样直通**；
* `Selection` 输出**全 false**（什么都不选）；
* 节点上显示一条 **Error**，并把出错的那段原文带上。

理由：下游接 `Delete Geometry` 时，节点若改成"默认全选"，一个笔误就会把模型删光。

### G2 警告但不失败：引用不存在的东西

引用了**不存在的组**或**不存在的属性**时，只给 **Warning**，其余部分照常工作：

* `0-10 typo` 仍然选中 `0-10`；
* `typo*` 单独出现时什么都不选（但**不是**错误）。

理由：选择字符串是给人手写的，写了个还没建的组不应该让整条链路变红。
这与 G1 的分界是**语法 vs 引用**：**语法错是 Error，引用缺失是 Warning。**

### G3 拾取器的预选只来自序号

**只算序号与 `*`**：节点的求值支持组、通配、`@属性`，但视口拾取器在预选时不解析它们。

* 参数里写 `A`，节点会选中那批点，但**视口不会**预先高亮它们；
* 写 `0-1 A`，视口只预选 `{0,1}`；
* 组名与 `@属性` 记号**不参与预选**，也不出现在任何拾取器自己的文本里（拾取器没有面板摘要）。

理由见操作符文档；**不变量是"预选集合 ⊆ 节点选择集合"**，这条有交叉测试兜底
（`test_selection_parser.py` 对 14 个组/属性表达式断言包含关系，并单独把 Python 的答案钉死）。
拾取器算不出来的记号一律**往安全的方向**算：加到集合里时贡献"零"，
被 `^` 减掉或被 `!` 取补时当作"它可能选了全部"——所以 `!A`、`^A` 预选为空，
而不是（旧实现的错误行为）预选全部。

### G4 两个解析器对同一个字符串的判断一致

C++ 节点和 Python 插件各自实现一份解析器（Python 那份用于预选与面板摘要）。
**同一个字符串，两边必须都接受或都拒绝。** `test_selection_parser.py` 拿同一批表达式
分别喂给真节点和 Python 解析器，逐集合比对。

### G5 绝不改写用户键位，也不替用户决定快捷键

拾取器的快捷键只注册进 **addon 自己的 keyconfig**，永远不往用户的 keyconfig 里写项；
而且注册进去的是一条**未绑定**条目（`type=NONE`），**没有默认快捷键**——
键由你在 Keymap 编辑器里自己绑（`Preferences → Keymap` 搜 `Pick Selection`），
取消加载项时也只收回那一条，不动你绑的键。
历史上有一次事故：往用户的空壳 keymap 里写项，把内置那张整表顶掉，3D 视口的导航和
左键点选全废（详见 `../archive/04-视口选择.md` 的 P6.2）。启动时的键位修复也只会**清理自己的残留**。

### G6 手册里**声称求值结果**的表达式全部在被测的那张表里

`reference/selection_syntax.md` §5 是一张"表达式 → 选中集合"的表，
`tests/test_manual_examples.py` **逐行执行**它。手册其它页面里凡是**断言结果**的示例，
都取自这张表；§5 之外不写结果断言。（介绍 Houdini 语法的纯示意，如
`0-10 rim ^5`，不算——它描述的是 Houdini，不是本节点。）

要加新示例：先加进 §5，再补进测试，最后才在页面里引用它。

**Node Info 的保证记作 MMB-1…MMB-10**（[该页](operators/node_info.md#保证规范性)），
编号与决定层 `spec/node_info.md` 的条目**一一对应**（旧的 `G7–G13` 编号已统一成 `MMB-n`），
与这里的 G1–G6 同等效力、同样每条都有断言。其中 **MMB-8 与 G5 是同一个方向的两条**：
拾取器与 Node Info **都不写用户键位**——后者的快捷键由你自己在 Keymap 编辑器里指定，
加载项只提醒、只读回状态。

**PDT Wrangle 的保证记作 `WR-1`…`WR-37`**（[该页](nodes/pdt_wrangle.md)），
同样与 `spec/pdt_wrangle.md` 一条一条对应。与上面几条不同，它的断言分两处：
节点侧是 `tools/pdt/tests/test_pdt_wrangle.py`（`W-1`…`W-4` 是接口与持久化，
`E-1`…`E-34` 是逐元素语义，行号写在页面里每条保证的"判据"一行），
引擎侧是 `tools/pdt/tests/wrangle_engine_selftest.cpp`（语言、类型检查与错误信息；
零 Blender 依赖，`tests/build_engine_selftest.ps1` 秒级独立编译，`run_tests.ps1` 已纳入门禁）。
那一页**不复述一份"代码 → 结果"的表**：可执行的断言只有这两处，避免手册与实现各说一套。

反例：`@P.y>0` 曾在文件头注释和 6 处文档里被当作范例，而 `@P` **从来不是** `position`
的别名，节点只会报 `attribute 'P' not found`——这个错误活了两个版本没人发现。
更糟的是同一形状还出现了两次：glob 的 `[a-z]`/`[^abc]` 与 `=`/`!=` 的值列表
**都实现了、都被文档承诺、都因为校验层而完全不可达**。
（写这份手册时，逐行执行 §5 当场就把值列表那条挖出来了。）

---

## 已知的有意边界

这些不是缺陷，是设计决定；详见各页与 `../archive/04-视口选择.md` 的"已知限制"表。

* **没有 Houdini 的 `@P` / `@x` / `@y` / `@z` 简写**。要读位置就写 `@position.x`。
* **没有"任意点元素"的域**：`Vertex`（网格）、`Control Point`（曲线）、`Point`（点云）
  是三种不同的元素种类，一个节点只作用在其中一个组件上；要同时选网格顶点和曲线控制点
  就串两个节点。
* **不支持字符串属性**：`@name="text"` 直接报语法错，而不是静默不匹配。
* **一个几何集合里可以同时有网格、曲线、点云和实例**（`Join Geometry` / `Instance on Points`
  之后很常见）。选择字符串**只在 `Domain` 对应的那个组件上求值**（L0 PDS-8/PDS-9）：
  `Vertex`/`Edge`/`Face` 是网格元素、`Control Point`/`Spline` 是曲线元素、`Point` 是点云元素、
  `Instance` 是顶层实例；别的组件恒为假（不是错误）。想同时命中两种组件就用两个节点。
  节点侧 `Domain` 是**节点属性**（和 `Delete Geometry` 的 `Domain` 一样，画在节点体上、
  不能连线）；视口拾取**由一级 pie menu 选出的元素种类**决定临时对象：网格进点编辑模式、
  曲线进曲线编辑模式（序号 = 扁平控制点序号）、点云进点云编辑模式（序号 = 点序号，
  选中状态是点云自己的 `.selection` 属性）、实例按二级 pie menu 选的 `Instance Mode`
  进点云编辑模式（每个实例一个点）或网格/曲线编辑模式（realize 后选真几何）。
  详见 [节点页](nodes/pdt_selection.md#domain元素种类) 与 `../archive/07-曲线支持评估.md` §13。
* **节点的实例域可用，实例的拾取有三种方式**：`Domain = Instance` 选的是**顶层实例序号**，
  `group:`/`@属性`/`@position` 都能用；二级 pie menu 给三个 `Instance Mode`——
  `Point Cloud`（每个实例一个点，点在实例原点，点序号 = 实例序号，源对象隐藏）、
  `Point Cloud with Reference`（同上，但源对象留在视口里当参考）、
  `Realized Instance`（把实例展开成真几何，点选真元素再折回实例序号，带 100 万元素预算闸门）。
  三种方式都由 pie 的选择决定，**拾取器不读节点的 `Domain`**（写回去的实例序号要用得上，
  节点自己的 `Domain` 得是 `Instance`，那是用户的事）。见 `../archive/08-实例支持评估.md`。
* **目标修改器之下的修改器不参与拾取**：索引对齐的是"进入该修改器的几何"。
* **索引是声明式的**：参数里存的是序号，上游拓扑一变含义就漂。
* **拾取器不预选组与属性**（G3）。
