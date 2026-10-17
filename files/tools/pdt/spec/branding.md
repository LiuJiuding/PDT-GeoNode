<!-- owner: user; agent: read-only -->
<!-- 条目格式见同目录 README.md：不变量一行一条，ID 写在行首粗体里。
     本文件初稿由 用户 依 docs/10-文档分层与协作规范.md 的示例落成（2026-09-15），
     之后由用户维护；agent 只在回复里提建议，不直接改。
     写作参考：tools/pdt/templates/L0-参考.md -->

# Branding(自编译版本标记) 最小规范

> 决定层。细则与例子在 `../docs/manual/<路径>.md`，
> 实测、坑与历史在 `../docs/0X-<...>.md`；那两份不具有推翻本文件的效力。

## 目标

让用户与文件都知道这是哪个 Blender。

## 契约

品牌来自 `BLENDER_BRAND_ID`，随标题栏/状态栏/About 显示，并写进每个存盘文件的 `FileGlobal.build_hash`

## 不变量

- **BRAND-1** 品牌一个来源；`version_string` 保持上游形状 <!-- wr:id=one-brand-source -->
- **BRAND-2** 保存的文件带品牌且自己读得回来、不误报 <!-- wr:id=file-carries-brand -->
- **BRAND-3** 官方版读到文件里的标识；官方改写后署名变成官方 <!-- wr:id=official-sees-brand -->
- **BRAND-4** 记录着**本族之外**（`PDT-GN` 前缀之外）的构建 → 原生通道警告；没署名的老文件、本族其它版本 → 静默 <!-- wr:id=warn-foreign-build -->
- **BRAND-5** 真窗口标题栏带 `(PDT-GN <版本>)` 后缀 <!-- wr:id=window-title-brand -->
- **BRAND-6** 品牌里的版本号就是对外发布的 tag：形如 `vMAJOR.MINOR.PATCH`，不带预发布后缀；fork 内同名加 `pdt-` 前缀；两者不一致算发布缺陷 <!-- wr:id=version-follows-tag -->

## 验收

test_branding.py + gui_branding_title.py

## 明确不做


- 不冒充上游版本号、 不加 minversion 硬拒、不新增 DNA 字段、不拿版本号当"外来构建"的判据
