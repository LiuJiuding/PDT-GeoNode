<!-- 待补 L0：`spec/branding.md`（见 tools/pdt/docs/00-当前状态.md 的"建议 L0 增补"）。 -->
# 构建标识（Build Branding）：说清"这个文件是哪个 Blender 存的"

> **状态**：L1 已落地，L0 = `../../spec/branding.md`（`BRAND-1…6`）。本文里每条"保证"都有
> 测试兜底：BRAND-1..4 在 `tools/pdt/tests/test_branding.py`，BRAND-5 在
> `tools/pdt/tests/gui_branding_title.py`（真窗口），BRAND-6 由发布流程那一步
> `tools/pdt/version.ps1 -Action verify` 核对（它不属于 `run_tests.ps1`：开发中的检出
> 本来就该停在上一个发布 tag 上）。

这个分支不是官方 Blender：它多了一个节点、两个插件，也多了几处 DNA。官方版打开
本分支存的文件时，**自定义节点会被丢掉**（实测：文件能读开，节点树里的
`PDT Selection` 直接消失，只有官方的
`File written by newer Blender binary (502.45), expect loss of data!` 一行提示）。
所以这个分支要回答两个问题：

1. **我在用哪个 Blender？** —— 标题栏、状态栏、About 里直接写出来。
2. **这个文件是哪个 Blender 存的？** —— 文件里存了标识，本分支读得懂并会提示。

---

## 1. 品牌是什么

只有一个来源：`source/blender/blenkernel/BKE_blender_version.h`。品牌由**两段**组成，
族名固定、版本号跟发布 tag 走：

```c
#define BLENDER_BRAND_FAMILY "PDT-GN"
#define BLENDER_BRAND_VERSION "v0.1.0"                     /* <- version.ps1 -Action stamp 写这里 */
#define BLENDER_BRAND_ID BLENDER_BRAND_FAMILY " " BLENDER_BRAND_VERSION
```

它决定下面所有出现品牌的地方。**改版本号 = 只改 `BLENDER_BRAND_VERSION` 这一行**
（用 `version.ps1 -Action stamp -Tag v0.1.0`，别手改），**重新编译后生效**——
它是编译期常量，因为发布的"changes only"仓库没有 git 历史与 tag，构建期取不到。

它是 ASCII、**不超过 15 字节**，因为要写进文件的 `FileGlobal.build_hash`（`char[16]`）。
族名 6 字节 + 空格 1 字节，所以版本号那一段**最多 8 个字符**：`v0.1.0` 可以（6），
`v10.20.30`（9）和 `v0.1.0-rc1`（10）不行。`test_branding.py` 有断言守着这条。

### BRAND-1 分支给自己命名，但不冒充上游的版本号

* `bpy.app.brand_id` → `"PDT-GN v0.1.0"`（官方版是空串）。
* `bpy.app.version_string_branded` → `"5.2.3 LTS (rc) (PDT-GN v0.1.0)"`。
* `bpy.app.version_string` **保持上游形状**（`"5.2.3 LTS (rc)"`），因为这一串会被写进
  导出的文件头（OBJ / PLY / USD / Alembic）并被上游测试断言。
  品牌只出现在**界面**上：标题栏、状态栏、启动图、`About`。

### BRAND-6 品牌里的版本号就是发布 tag

对外发布的 tag 是 `vMAJOR.MINOR.PATCH`（公开的 changes-only 仓库），fork 里同一个号加
`pdt-` 前缀（因为 `v*` 在这个 fork 里已经是**上游 Blender** 的命名空间，`version.ps1`
正是用它找上游基线）。两者与 `BLENDER_BRAND_VERSION` 必须一致，不一致就是发布缺陷。

```powershell
. tools\pdt\version.ps1 -Action stamp -Tag v0.2.0   # 改 header（写盘）→ 提交 → 重编 → 打 tag
. tools\pdt\version.ps1 -Action verify              # 品牌 = pdt-v<版本> = 产物仓 v<版本>
```

版本号的语义映射（MAJOR/MINOR/PATCH 各管什么）与完整发布清单在
`../02-版本管理.md` §标签策略。

---

## 2. 界面上看得到的地方

| 位置 | 内容 |
| --- | --- |
| **标题栏 / 任务栏** | `xxx.blend - Blender 5.2.3 LTS (rc) (PDT-GN v0.1.0)`（`* ` 未保存标记、路径部分照旧） |
| **状态栏**（右下版本号） | 同上（`编辑 → 偏好设置 → 界面 → 状态栏 → 版本`，默认开） |
| **About**（`帮助 → 关于 Blender`） | 多一行 `Brand: PDT-GN v0.1.0`；官方版没有这一行 |
| **启动图** | 用官方扩展点：环境变量 `BLENDER_CUSTOM_SPLASH_BANNER=<带 alpha 的 png>` 叠一个角标（不改代码；见 `wm_splash_screen.cc` 的注释） |

> 只在标题栏加后缀，不改"Blender"这个名字，也不改版本号本身；官方版的界面与本分支
> 在除了上面这几行的任何地方都一致（这是有意的：兼容性问题容易定位）。

### BRAND-5 标题栏的标识在真实窗口里成立

标题栏字符串由 C++ 生成，所以判据取自**真窗口**：`gui_branding_title.py` 起一个真实窗口，
用 Win32 读回这个进程的窗口标题，断言它以 ` - Blender ` 开头并以 `(PDT-GN <版本>)` 结尾
（空场景与有数据的场景各一次）。版本号从 header 读，所以 `stamp` 之后这个测试自动跟走。

---

## 3. 文件上的标识

### BRAND-2 本分支存的文件带品牌，且自己读得回来

保存时，品牌被写进文件 `FileGlobal` 块的 `build_hash` 字段（**不新增 DNA 字段、
不动 `BLENDER_FILE_SUBVERSION`**）。重新打开后：

* `bpy.app.file_build_hash` → `b"PDT-GN v0.1.0"`（这是**文件**记录的构建，不是正在运行的
  二进制；它带着写文件那个发布的版本号）；
* 本分支打开**自己的**文件不会弹任何外来构建提示。

### BRAND-3 文件里的标识能穿过官方版，但"改写"会改署名

这条是**实测出来的**，不是设计意图（`test_branding.py` 用官方版把我们的文件再存一次，
再读回来）：

* 官方版**打开**我们的文件：它读同一张 DNA 表，`bpy.app.build_hash`、`About` 的 `Hash:`
  一行、`系统信息` 的 `hash:` 都会显示 `PDT-GN v0.1.0` —— 它不需要认识我们。
* 官方版**再存一次**这个文件：`build_hash` 被换成**官方自己的** hash（实测 `9e2066aef7ef`），
  而那个只能本分支求值的节点降级成 `NodeUndefined`（节点还在树里，storage 没了）。

所以"文件属于哪个构建"= **最后写它的那个构建**。这不是缺陷，是 `build_hash` 的定义：
它记录写文件的人。

### BRAND-4 外来构建会被本分支提示（按"族"判，不按版本）

打开一个文件后，如果文件里记录的构建**不属于本族**（不是以 `PDT-GN` 开头），本分支会通过
Blender 原生的报告通道给出警告（Info 编辑器 + 警告横幅）：

```
This file was saved by a different Blender build (otherbuild-2.0); this one is PDT-GN v0.1.0.
Custom PDT nodes are only evaluated by a PDT build, and may show up empty or as Undefined
nodes here.
```

判据是**前缀**：`STRPREFIX(build_hash, BLENDER_BRAND_FAMILY)`。

* 本分支自己的文件（**任何版本**存下来的）→ 静默；
* 官方版存的文件（署名官方 hash）、别的自编译版的文件（署名它自己的牌子）→ 提示；
* 官方改写过的我们的文件（署名官方 hash）→ 提示 —— 这正是该提示的情况，因为里面的
  自定义节点已经降级了；
* **没有**构建名的老文件（本次改动之前存的所有文件）→ 静默。

**为什么是"族"而不是"精确相等"**：品牌现在带版本号，精确比较会让**升级自身版本之后的每一个
旧文件**都弹一次"外来构建"——那是误报，而且会让 BRAND-2 的"不误报"自相矛盾。真正值得提醒的是
**别的构建族**：只有它们的文件可能带着本二进制不认识的自定义节点。判据的两侧都有断言
（`test_branding.py`：别的族 → 警告；同族不同版本 → 静默）。

**为什么不做"只看更新的文件"**：那种"未来文件"根本装不进来——本二进制拒绝打开
子版本比自己新的文件（实测：用逐字节打补丁造出来的 sub 46 文件在读取阶段就报
`Missing DNA block`），所以那条分支永远测不到、也永远碰不到；而"不同构建"才是真正
会让人踩坑的情况。

---

## 4. 那官方版打开我们的文件呢？

**官方版里没有我们的代码，所以它不会说"PDT"三个字**——这是物理限制，不是没做。
它已有的机制会这样表现（实测）：

| 官方版里的现象 | 说明 |
| --- | --- |
| `File written by newer Blender binary (502.45), expect loss of data!`（Info / 控制台） | 本分支的文件子版本比官方 5.2.1 的 44 新，官方自己就会警告 |
| `bpy.app.build_hash` / About 的 `Hash:` 显示 `PDT-GN v0.1.0` | 我们在文件里留下的标识（官方一改写就变回它自己的 hash） |
| 自定义节点降级成 `NodeUndefined` | 官方不认识 `PDT Selection` / `PDT Wrangle` 的 node storage（节点还在树里，节点类型没了；实测） |

本分支**故意不做**"让官方版打不开"这件事（那需要用 `minversion` 硬拒）：打开看一眼
比彻底拒绝更有用。知道风险来自上面三行就够了。

---

## 5. 保证汇总（可判定）

| ID | 保证 | 测试 |
| --- | --- | --- |
| BRAND-1 | 品牌只有一个来源（族名 + 版本号）；`version_string` 保持上游形状；品牌 ASCII 且 ≤ 15 字节 | `test_branding.py` |
| BRAND-2 | 保存的文件带品牌，自己能读回，且不误报 | `test_branding.py` |
| BRAND-3 | 官方版能读到文件里的标识；官方改写后署名变成官方 | `test_branding.py`（需要 `PDT_REFERENCE_BLENDER`） |
| BRAND-4 | 记录着**族外**的构建 → 原生报告通道警告；同族其它版本、没署名的老文件 → 静默 | `test_branding.py` |
| BRAND-5 | 真窗口标题栏带 `(PDT-GN <版本>)` 后缀 | `gui_branding_title.py` |
| BRAND-6 | 品牌版本 = 发布 tag（`pdt-v*` / 公开 `v*`） | `version.ps1 -Action verify`（发布时） |

```powershell
# headless 四条
& $exe -b --factory-startup --python-exit-code 1 --python tools\pdt\tests\test_branding.py
# 真窗口那条
& $exe --python tools\pdt\tests\gui_branding_title.py
```

`test_branding.py` 自己会再拉起两个 Blender（本二进制 + 官方参考版），所以它比其它
`test_*.py` 慢一点；官方版没设 `PDT_REFERENCE_BLENDER` 时 BRAND-3 记 SKIP。

---

## 明确不做

* **不冒充上游版本号**：`bpy.app.version_string` 与写入导出文件头的那一串保持官方形状。
* **不硬拒官方版**：不加 `minversion`，官方版仍可打开（节点会丢）。
* **不新增 DNA 字段、不动 `BLENDER_FILE_SUBVERSION`**：文件结构与官方一致，
  只借用一个官方本来就会读的字段（`FileGlobal.build_hash`）。
* **不在打开普通文件时打扰用户**：只在文件写着"族外的构建"时提示；没署名（本次改动之前存的
  老文件）、同族其它版本、以及本分支自己存的，都静默。
* **不用预发布后缀**（`-rc` / `-beta`）：`build_hash` 只有 15 字节可用，版本段最多 8 个字符；
  要预演就打一个开发 tag（`wrangle-m7` 那种），别动品牌版本号。
