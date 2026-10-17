<!-- owner: user; agent: read-only -->
<!-- 条目格式见同目录 README.md：不变量一行一条，ID 写在行首粗体里。
     前缀 OPD = Outliner Parent Drop。 -->

# Outliner 拖放设父级 最小规范

> 决定层。细则与例子在 `../docs/manual/operators/outliner_parent_drop.md`（待写），
> 实测矩阵对照、边界与历史在 `../docs/20-Outliner拖放父级-无inverse.md`；
> 那两份不具有推翻本文件的效力。

## 目标

在 Outliner 里把一个物体拖到另一个物体上松开就能设父级：**默认**保持世界变换且**不引入父级反矩阵偏移**（最常用那一档），需要旧行为时按住 Alt；两档的结果与 `Object ▸ Parent ▸ Set Parent To` 里对应的同名两项一致。

## 契约

- **触发**：拖动物体行到另一个物体行上松开。View Layer 模式下 Shift 仍是拖放生效的修饰键（上游规则，不属本规范）；关掉 `Filter ▸ Collections` 后不带修饰键也生效。
- **模式选择**：不带 Alt = `Keep Transform Without Inverse`；带 Alt = `Keep Transform`。Alt 只选模式，不改变"是否保持世界变换"。
- **输入**：被拖物体（可多选）+ 目标物体（父级）；库链接 / 覆盖不可编辑的物体不改。
- **输出**：父子关系、被拖物体的局部变换与父级反矩阵；物体不移动。
- **持久化**：与手工设父级完全一样（存盘后关系与两个矩阵不变）；不新增持久化字段。

## 不变量

- **OPD-1** 世界变换不变：拖放设父级不改变被拖物体的世界变换（两档都是，误差在浮点误差内）。 <!-- wr:id=keep-world-transform -->
- **OPD-2** 默认档不写偏移：不带 Alt 结束后，父级反矩阵（`matrix_parent_inverse`）是单位阵，父级偏移全部落在被拖物体自己的局部变换里。 <!-- wr:id=default-no-inverse -->
- **OPD-3** Alt 档写偏移：带 Alt 结束后，父级反矩阵非单位（承载父级偏移），且世界变换照 OPD-1 不变。 <!-- wr:id=alt-keeps-inverse -->
- **OPD-4** 与菜单同义：两档的结果分别与 `Set Parent To` 里的 `Object (Keep Transform Without Inverse)` / `Object (Keep Transform)` 逐位相同——同一语义，不另立一套。 <!-- wr:id=same-as-menu -->
- **OPD-5** 多选：一次拖多个物体时逐个处理，每个物体各自满足 OPD-1/2/3。 <!-- wr:id=multi-object -->
- **OPD-6** 拒绝成环：会造成父子环的拖放不生效，两边的父子关系与变换都不变（允许一行提示）。 <!-- wr:id=refuse-loop -->
- **OPD-7** 拒绝不可编辑：库链接 / 覆盖且不可编辑的物体不被改动（允许一行提示）。 <!-- wr:id=refuse-non-editable -->
- **OPD-8** 模式无关：`outlinevis` 取任何值（View Layer / Scenes / …）时 Alt 的含义都与 OPD-2/3 相同。 <!-- wr:id=mode-independent -->
- **OPD-9** 目标必须是物体：拖到非物体行（集合、场景、其他数据类型）上不建立父子关系。 <!-- wr:id=object-row-only -->

## 验收

- 无头：`tools\pdt\tests\test_outliner_parent_drop.py` 全绿（两档矩阵语义 + 接线守卫）。
- 真窗口（必须；拖放事件脚本造不出来）：`qa\outliner_parent\checklist.md` 逐条通过，场景由 `qa\outliner_parent\demo_scene.py` 起。
- 现场判据（读回物体即可）：世界矩阵与拖放前逐位相同；默认档父级反矩阵为单位阵，Alt 档非单位。

## 明确不做

- 不做 plain `Object`（不保持变换）的拖放档位——它只留在菜单里。
- 不改 `Set Parent To` 菜单本身（四项照旧），也不改 `Object ▸ Parent` 的其它项。
- 不改清父级拖放（Alt 在那里仍表示"连变换一起清"）。
- 不新增修饰键组合；不改 Shift 触发条件、拖动阈值与拖放高亮规则（上游行为）。
- 不给拖放加确认弹窗、状态提示条或撤销步骤之外的东西。
- 不改物体 `Location/Rotation/Scale`、delta 变换与动画曲线的语义。
