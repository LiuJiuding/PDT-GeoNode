# Enhanced Viewer Overlay（把 viewer 属性画成方向线段 / 旋转坐标轴）

> 位置：3D 视口 ▸ 叠加层（`Overlay`）▸ **Viewer Node** 面板
> 作用对象：**活动 Viewer 节点**在视口里显示的那份快照几何（和"颜色"overlay、"Attribute Text"完全同域）
>
> **规范来源**：`../../../spec/viewer_overlay.md`（你手写的 `EVO-1…EVO-6`）。
> 本页是该决定层的细则展开，**编号与 L0 条目一一对应**；两者冲突时以 L0 为准。
> 实现与实测记录在 `../../11-Viewer属性可视化评估.md`。

活动 viewer 显示一个属性时，本分支除了原有的"把值当颜色刷在面上"，还能把它画成引导线：

| 属性类型 | 画成 | 长度 | 颜色 |
| --- | --- | --- | --- |
| `Float3`（UI：Vector） | 从元素位置出发的**方向线段** | 模式 `Vector`：`|v| × Vector Scale`；模式 `Direction`：`1 × Vector Scale`（世界单位） | `Vector Color`（默认黄 `#FFD940`），可调 |
| `Quaternion`（UI：Rotation） | 三轴 tripod | 三轴等长 = `1 × Axis Scale`（世界单位） | 主题轴色 `TH_AXIS_X/Y/Z`（红/绿/蓝） |
| `Float4x4`（UI：Matrix） | 三轴 tripod | 每轴 = `\|分解出的该轴 scale\| × Axis Scale`（世界单位） | 同上 |

其它类型（`Float`/`Int`/`Bool`/`Float2`/颜色）没有线段表达，**行为与今天完全一样**（照旧只有颜色 overlay）。

---

## 面板（EVO-2）

`Overlay ▸ Viewer Node`：

| 控件 | 默认 | 说明 |
| --- | --- | --- |
| **Vectors & Axes**（开关） | 关 | 打开后按上表画线；关掉后一切回到今天的行为 |
| 模式（`Direction` / `Vector`） | `Vector` | 只影响 `Float3`：`Vector` 长度随模长，`Direction` 全部等长（只看朝向） |
| 颜色块 | 黄 | `Float3` 线段的颜色（`Quaternion`/`Float4x4` 固定用主题轴色，不受它影响） |
| **Vector Scale** | `1.0` | `Float3` 的长度乘数（世界单位，与对象的缩放无关） |
| **Axis Scale** | `1.0` | `Quaternion`/`Float4x4` 的轴长乘数（世界单位） |

**旧文件里的默认值**：这些字段是后加的，所以**加载任何旧 .blend（含出厂 startup）时由版本迁移补齐**
（`blenloader/intern/versioning_520.cc`，随 `BLENDER_FILE_SUBVERSION` 45 一起）：模式 `Vector`、
两个缩放都 `1.0`、颜色黄。也就是说打开旧文件时不会出现"缩放为 0、线段画不出来"的假故障；
保存过的新文件按你设的值走。

`Color Opacity` 同时作用于线段与三轴的不透明度；`Attribute Text` 不受影响，可以同时打开。

**EVO-3 的颜色抑制**：开关打开、且当前属性是 `Float3` 时，**不再**把该属性刷成表面颜色
（线段替代颜色，避免"彩虹面 + 黄线"叠在一起）。`Quaternion`/`Float4x4` 本来就没有颜色 overlay。
开关关闭时，颜色 overlay 的行为与今天逐位一致。

---

## 作用域与元素位置（EVO-1）

只画**活动 viewer 的快照几何**——也就是"Color Opacity"和"Attribute Text"看的那份数据，
所以 mid-tree（viewer 后面还有 Join / Delete / Boolean）、组内 viewer、实例域全都适用。

起点位置按属性的**域**取，与 `Attribute Text` 同一套规则：

| 域 | 线段起点 |
| --- | --- |
| `Point` | 顶点位置 |
| `Edge` | 边的中点 |
| `Face` | 面的中心 |
| `Corner` | 该 corner 所在顶点（**不做**文字那样的错位偏移，线段从角点本身出发） |
| `Instance` | **对象原点**：实例域每个实例只有一个值，画的是"当前预览实例"的那一个值，位置取该实例对象的原点（与文字 overlay 完全一致） |

**实例域**：即使 viewer 看的是 `Float3`/`Quaternion`/`Float4x4`，也只在实例对象原点画**一组**
（不是每个实例一组）——这与文字 overlay 的实例分支一致。想要每个实例各一组，
用 `Realize Instances` 把实例变成真几何后再看。

**实例域的值按"基对象空间"解释**（2026-09-16 修）：预览实例的对象矩阵是 `基对象 × 实例变换`，
而 `Instance` 域的属性（`instance_transform`、`position`、`rotation`…）本身写在基对象空间里，
所以画线前先用 `to_local_directions()` 把**方向**换算回预览对象的局部空间。
不换算就会把实例变换叠两次——`test_matrix.blend` 报的就是这个：viewer 看 `instance_transform`、
拖 `Instance on Points` 的 Rotation Z，视口里的坐标轴转得比实例**快一倍**（实测 35° → 显示 70°）。
换算只动方向：起点仍是实例原点，轴长仍按值的分解 scale 走（EVO-5）；
基对象自己的旋转照常生效（轴线跟着基对象一起转），实例自身的缩放只通过"值本身的 scale"体现。

Viewer 下面这些情况照旧由上游负责，本 overlay 不参与：修改器关闭 / 节点组没有输出时
viewer 不工作（节点上仍会显示上游的那两行提示）。

---

## 旋转与矩阵（EVO-4 / EVO-5）

* **`Quaternion`**：先归一化，再把 X/Y/Z 三个坐标轴各画一条等长线段。
  零四元数按单位四元数处理。
* **`Float4x4`**：先把矩阵**分解成 loc/rot/scale**（`to_loc_rot_scale_safe`，与 `Attribute Text`
  显示矩阵用的是同一个函数），再按分解出的**每轴 scale** 决定该轴长度。因此即使矩阵带切变（shear），
  三轴仍然互相垂直——这是"看起来最接近的旋转 + 缩放"，而不是矩阵的原始列向量。
* **平移被忽略**：tripod 画在元素位置上，只表达朝向与缩放，不把矩阵的平移量加进去。
* 负 scale 取绝对值（长度不能为负）；某轴 scale 为 0 时该轴不画。
* **实例域**：三轴朝向 = 值在**基对象空间**里的旋转（= 实例实际显示出来的朝向），
  实例自己的变换不会被叠第二次（见"作用域与元素位置"一节的 `to_local_directions()`）。
  实例自身缩放不影响**方向**；轴长仍只由值的分解 scale 与 `Axis Scale` 决定。

---

## 规模上限（EVO-6）

元素数超过 **1,000,000** 时，本 overlay **什么都不画**（不截断、不降采样、不静默半画），
并在对象原点旁用视口文字说明原因：

```
Vector overlay: 4000000 elements (limit 1000000), not drawn
```

理由：每帧为每个元素建两个顶点会直接把视口拖死（实测过卡死）。上限值与文字都是实现细节，
需要改时改 `VIEWER_OVERLAY_MAX_ELEMENTS`（`draw/engines/overlay/overlay_attribute_viewer.hh`），
同时更新本节与 `gui_viewer_overlay.py` 的断言。

---

## 绘制样式（EVO-7）

> 来源：`../../../spec/viewer_overlay.md`# EVO-7（用户手写的决定层）。
> 这三条原来是"实现时定下的"，2026-09-14 由用户升进 L0，因此这里从"其它决定"改成规范条目。

* **不做深度遮挡**：引导线永远画在几何之上（line 子通道用 `DRW_STATE_DEPTH_ALWAYS`）。这是"读数据"的显示，
  被自己的几何挡掉就失去意义；与 `Attribute Text` 一致。要改成被遮挡，把那个状态换掉即可。
* **退化值不画**：`|v| = 0` 的向量（`Direction` 模式没有方向、`Vector` 模式长度为零），以及某轴 scale = 0 的
  tripod 轴，都跳过该元素，不产生退化线段。
* **起点小点不画**：参考图（`vector.jpg`）里线段起点的青色小点是 Houdini 自己的点显示；
  Blender 视口本来就有点 overlay 在管这件事，本 overlay 不重复画。

**覆盖情况**（对 `spec/coverage.md`；两条都在 `gui_viewer_overlay.py` 的 `phase_style` 里）：

* **不做深度遮挡**：测试在相机与 viewer 对象之间放一个不透明立方体（`make_occluder`，放在两者连线上
  45% 处），**先用对照证明它确实挡住了对象**（颜色 overlay 的彩色像素掉到可见时的 40% 以下），
  再断言引导线在遮挡物后面的差分像素数仍 > 300。把 `DRW_STATE_DEPTH_ALWAYS` 换成深度测试，
  这条会立刻变红（这就是正对照的意义）。
* **退化值与起点小点**：`|v| = 0` 的场景 + `Vector Scale = 0.4`，改缩放的差分像素必须 ≈ 0
  （< 噪声地板）。起点小点也逃不过这条 —— 任何不随长度缩放的痕迹都会产生差分。
  实现侧也不再依赖驱动：`append_vector_line` 在"方向 × 缩放"为零时直接跳过，不生成零长线段。

---

## 其它（L1 细节，非 L0 条目）

* **旧文件的默认值由版本迁移补齐**（见"面板"一节）：DNA 新增字段在旧文件里是 0，光靠 DNA 默认值不管用 ——
  这是验收第一遍实测到的真问题（模式变成 `DIRECTION`、缩放 0、颜色全黑），所以补了
  `versioning_520.cc` 的一段迁移 + `BLENDER_FILE_SUBVERSION` 44→45。
* 线段是 1 px 细线（与 Blender 其它 overlay 线一致）；`Vector Scale` / `Axis Scale` 都是**世界单位**，
  与对象缩放无关。
* **EVO-6 的那一行提示文字**：超限时引导线全部不画，只在对象原点旁画一行
  `Vector overlay: N elements (limit 1000000), not drawn`（L0 的 EVO-6 明确"只允许一行提示文字"）。
  它是 `DRW_text_cache` 的文字，不是引导线批次，也不随缩放变化。

---

## 验收（规范性）

判据写在这里，测试必须与本节一致。

```powershell
# 无头：属性存在性、默认值（含旧文件迁移）、枚举、DNA 往返
blender.exe -b --factory-startup --python-exit-code 1 --python tools\pdt\tests\test_viewer_overlay.py
# 真窗口：把每条不变量数成像素（截图落在 blender.exe 同级的 viewer_overlay_shots\）
blender.exe --enable-event-simulate --factory-startup --python tools\pdt\tests\gui_viewer_overlay.py
# 一起跑（含上面两条）
. tools\pdt\run_tests.ps1 -Gui
# 只跑"实例域朝向"那一段（`phase_instance_orientation`），改这个功能时省掉整套 GUI 的时间
# （探针在工作区根目录的 qa\ 下，不在仓库里）
blender.exe --enable-event-simulate --factory-startup --python qa\viewer\viewer_matrix_angle_probe.py
```

> 截图/临时 .blend 都写在**运行中的 blender.exe 同级目录**（`viewer_overlay_shots\`、
> `viewer_overlay_scratch\`），不落在仓库里；dsh 沙箱禁写系统临时目录，所以测试不用 `%TEMP%`。

**判据是差分测量**：同一帧用"长度 0"和"测试长度"各渲一次，两张截图**不同的像素就是画出来的几何** ——
与视图变换、主题、背景无关（实测教训：AgX 会把线性黄 `(1.0, 0.85, 0.25)` 变成屏幕上的 `(1.0, 0.9, 0.5)`，
所以第一版按固定颜色阈值去数"黄像素"其实是在量视图变换）。渲染前关掉视口 AA
（`scene.display.viewport_aa = "OFF"`），并在**每个场景里先量"同一状态渲两次"的噪声地板**，
所有"什么都没画"的断言都与噪声地板比较，而不是与 0 比较。

| 条目 | 判据（`gui_viewer_overlay.py`，括号里是本轮实测值） |
| --- | --- |
| EVO-1 | 同一条节点树被两个对象共用时，**只有 viewer 的那个对象**有线段（左半 > 300 px，实测 1965），另一个对象**一个像素都不变**（< 噪声地板；实测右半 59 px vs 地板 271，且 < 左半的 1/5） |
| EVO-2 | 开关打开/关掉时"长度 0 vs 1"的像素差：开 = 有线段，关 ≈ 0（< 噪声地板 ×3）；关掉后颜色 overlay 精确回到打开前的水平（±20%，实测 5434 → 5434） |
| EVO-3 | `Direction` 与 `Vector` 两种模式都画出线段（实测 673 / 2024 px），且 `Vector > Direction × 1.8`（`\|v\| = 3`、`Vector Scale = 0.4` ⇒ 实测 **3.0×**）；开线段时**非线段的彩色像素回到基线**（实测 1587 = 什么都不画；颜色 overlay 在时是 5434） |
| EVO-4 | 三轴共画出（实测 40479 px），且红/绿/蓝**主导通道**像素各 > 150（实测 14171 / 15330 / 9903），三者最长/最短 < 3（等长在透视投影下的上界） |
| EVO-4 / EVO-5 **实例域**（2026-09-16 加） | 单点实例 + `instance_transform` → viewer，俯视正交视图下量红/绿轴**屏幕角**（PCA）并与视图矩阵算出的期望值比：`Instance on Points` Rotation Z = 35° 时实测 **35.0018°**（翻倍会是 70.0°）；基对象再转 20° 时实测 **45.0°**（基对象旋转照常生效）；轴长用 `location_3d_to_region_2d` 换回世界单位 = **0.998**（值的 scale 为 1）；**Matrix 与 Quaternion 两种取值都测**（同一段换算代码）。正对照：修前同一场景实测 **70.0009°** |
| EVO-5 | 同一矩阵属性，分解出的 scale 从 0.3 提到 1.2 时轴像素增加 > 30%（实测 17150 → 40865，**2.4×**） |
| EVO-6 | 同场景低于预算（1,681 点）确实画线（实测 85152 彩色像素）；1,004,004 点时**不画**（像素差 = 该场景自身噪声 1257 px），且该帧仍在 60 s 内画完（实测 **0.63 s**） |
| EVO-7 | **深度不遮挡**：相机与对象之间放不透明遮挡物，先用对照证明对象被挡住（彩色像素 < 40%），再断言引导线的差分像素 > 300；**退化值与起点小点**：`\|v\| = 0` 且 `Vector Scale = 0.4` 时差分 ≈ 0（< 噪声地板） |
| 设置/持久化 | `test_viewer_overlay.py`：属性与枚举、**旧文件默认值（版本迁移）**、save/load 往返 |

---

## 边界

* **只读显示**：不能拖拽、不能编辑属性值、不写任何数据。
* **不自研 viewer 节点**：本功能改的是上游 overlay，节点类型与 viewer 语义不变。
* **不进入渲染 / 导出**：这是视口叠加层，渲染结果与导出文件不受影响。
* **不改现有 overlay 语义**：颜色 overlay 与 `Attribute Text` 的行为只在"`Float3` 且开关打开"时被替代。
