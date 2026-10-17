# Outliner 拖放设父级（Set Parent To）

> **状态**：L0 = `../../spec/outliner_parent_drop.md`（`OPD-1…9`），本文是它的细则层。
> 矩阵语义与源码接线在 `tools/pdt/tests/test_outliner_parent_drop.py`（headless，29 项），
> **拖放事件本身只能在真窗口验收**（`qa/outliner_parent/checklist.md`，脚本造不出
> `EVT_DATA_DRAGDROP`）；实现沿革、四个菜单模式的矩阵对照与边界在
> `../20-Outliner拖放父级-无inverse.md`。

在 Outliner 的 **View Layer** 模式里，把一个物体行拖到另一个物体行上松开，就能把前者设为后者的子级。
本分支改的只有一处：**默认那档不写"父级反矩阵"（parent inverse）**，
所以拖完之后物体停在原地、`matrix_parent_inverse` 保持单位阵，局部变换自己承担父级偏移。

> 与官方 Blender 的差别（有意）：官方是 `Shift` = 保持变换但**写**反矩阵、
> `Shift+Alt` = 不保持变换（物体会跳到父级原点）。本分支两档**都保持世界变换**，
> 用 Alt 决定"偏移存哪里"，并且**默认那档不写反矩阵**。

---

## 怎么用

1. Outliner 切到 **View Layer**（`Display Mode ▸ View Layer`）。
2. 按住 **`Shift`**（View Layer 模式下必须；把 outliner 的 `Filter ▸ Collections` 关掉后可以不按）
   拖动 `child` 那一行。
3. 拖到 `parent` 那一行上——该行会高亮成"拖入"——松开。

| 松开时按住 | 结果 | 等于菜单里的哪一项 |
| --- | --- | --- |
| `Shift`（默认） | 世界变换不变，父级反矩阵 = **单位阵**，偏移落在局部变换里 | `Object (Keep Transform Without Inverse)` |
| `Shift+Alt` | 世界变换不变，父级反矩阵 = **非单位**（承载父级偏移） | `Object (Keep Transform)` |

`Object ▸ Parent ▸ Clear Parent Inverse` 与 `... and Keep Transformation` 照旧可用，
撤销（`Ctrl+Z`）是一次拖放一条记录。

---

## 世界变换不变（OPD-1）

拖放设父级**不移动物体**：拖放前后的世界矩阵逐位相同（浮点误差之内）。
两档都是——Alt 只改变"偏移存在哪"，不改变"物体在世界的哪里"。

判据（headless，`test_outliner_parent_drop.py`）：无父级物体、以及**已经有父级**的物体各拖一次，
比较 `matrix_parent_inverse` 与世界矩阵。已知例外（四个菜单模式共有，不是本改动引入）：
被拖物体自身世界矩阵**带剪切**时无法用父级反矩阵复原，见
`../20-Outliner拖放父级-无inverse.md` 的边界第 7 条。

---

## 默认档不写偏移 / Alt 档写偏移（OPD-2 / OPD-3）

| 判据 | 默认档（只 Shift） | Alt 档（Shift+Alt） |
| --- | --- | --- |
| `matrix_parent_inverse` | 单位阵（逐元素 1e-5 内） | 非单位，平移分量非零 |
| 局部变换 | 吸收全部父级偏移（`location` 不再是拖之前那个值） | 照旧是"世界 − 父级"那一套 |

默认档的这条是它与官方 `Keep Transform` 的真正区别：官方把偏移写进反矩阵（读起来像"物体没动，
但反矩阵有值"），这里反矩阵是干净的，谁读 `matrix_parent_inverse` 都不会看到父级偏移。

---

## 与菜单同义（OPD-4）

拖放两档分别与 `Object ▸ Parent ▸ Set Parent To` 里的
`Object (Keep Transform Without Inverse)` / `Object (Keep Transform)` **逐位相同**：
它们走的是同一份实现（`parent_no_inverse_set()`），拖放没有另立一套语义。
所以菜单里能做的、能用键盘/搜索触发的操作，与拖放的结果可以逐元素比对。

---

## 多选（OPD-5）

一次拖多个被选中的物体时逐个处理，每个物体各自满足 OPD-1/2/3——
不会因为"拖了一批"就换一套规则，也不会只处理第一个。

---

## 成环与不可编辑（OPD-6 / OPD-7）

* **成环**：把 `parent` 拖到它自己的孩子上（`A→B→A`）时，拖动过程中目标行**不高亮**；
  若真走到执行那一步，状态栏报 `Loop in parents`，**两边的父子关系与变换都不变**。
* **不可编辑**：库链接（Library Linked）或覆盖且不可编辑的物体**不改动**，
  状态栏报 `Cannot edit library linked or non-editable override object(s)`；
  同一次拖放里可编辑的那些物体照常处理。

两条都只提示一行，不弹窗、不中断其它物体。

---

## 模式无关（OPD-8）

`outlinevis` 取任何值（`View Layer` / `Scenes` / …）时 Alt 的含义都与 OPD-2/3 相同：
**不带 Alt = 不写反矩阵**。`Scenes` 模式不需要 Shift，于是那里"不按修饰键"就是默认档、
单按 Alt 才写反矩阵。

---

## 目标必须是物体（OPD-9）

拖到非物体行上不建立父子关系：集合行、场景行、修改器/约束行、
以及其它数据类型（材质、图像……）都只是**没有效果**，不是错误。
父级只对"物体行"成立——这也是官方原有的判定（`te->idcode == ID_OB`）。

---

## 保证汇总（可判定）

| ID | 保证 | 判据 |
| --- | --- | --- |
| OPD-1 | 世界变换不变（两档） | `test_outliner_parent_drop.py`（含"已有父级再拖"） |
| OPD-2 | 默认档父级反矩阵是单位阵 | 同上 |
| OPD-3 | Alt 档父级反矩阵非单位 | 同上 |
| OPD-4 | 两档与菜单对应项一致（同一实现） | 同上（源码接线断言） |
| OPD-5 | 多选逐个处理 | 源码接线断言（每个拖动 ID 各走一遍）+ 现场 |
| OPD-6 | 成环不生效 | 同上（`Loop in parents` 且关系不变） |
| OPD-7 | 不可编辑物体不改动 | 源码接线断言（`BKE_id_is_editable` 分支）+ 现场 |
| OPD-8 | 所有 outliner 模式语义一致 | 源码接线断言（不按 `outlinevis` 分支）+ 现场 |
| OPD-9 | 非物体行不建立父子关系 | 官方既有判定（`parent_drop_allowed`）+ 现场 |

```powershell
# 矩阵语义与接线（headless）
& $exe -b --factory-startup --python-exit-code 1 --python tools\pdt\tests\test_outliner_parent_drop.py
```

真窗口那半只能手做：起 `qa\outliner_parent\demo_scene.py` 的场景，按
`qa\outliner_parent\checklist.md` 的两档拖放各做一次，读回 `matrix_parent_inverse`。

---

## 明确不做

* 不做 plain `Object`（不保持变换）的拖放档位——它只留在菜单里。
* 不改 `Set Parent To` 菜单本身，也不改 `Object ▸ Parent` 的其它项。
* 不改清父级拖放（Alt 在那里仍表示"连变换一起清"）。
* 不新增修饰键组合，不改 Shift 触发条件、拖动阈值与拖放高亮规则（上游行为）。
* 不给拖放加确认弹窗、状态提示条或撤销步骤之外的东西。
* 不改物体 `Location/Rotation/Scale`、delta 变换与动画曲线的语义。
