#!/usr/bin/env python3
"""L0 minimal specs (`tools/pdt/spec/`) and whether L1 and the tests have kept up.

    python tools/pdt/pdt_spec.py list     # every L0 item and where it is covered
    python tools/pdt/pdt_spec.py check    # advisory report (exit 0 unless --strict)
    python tools/pdt/pdt_spec.py sync     # refresh the recorded L0 content hashes

The three-layer model (see ``docs/10-文档分层与协作规范.md``)::

    spec/<topic>.md     L0  the user's minimal spec - hand-written, agent read-only
    docs/manual/**      L1  detail norms; the tests execute their example tables
    docs/0X-*.md        L2  process evidence, no normative force

``check`` edits nothing and, by default, fails nothing: the user writes L0 on their own
schedule and a fresh decision should not look like a broken build. It reports the five
things that are mechanically knowable:

  * an L0 file without the ``<!-- owner: user; agent: read-only -->`` marker;
  * an L0 item with no row in ``spec/coverage.md``;
  * a coverage row pointing at an item that no longer exists;
  * a coverage row pointing at an L1 file, anchor or test that does not exist;
  * an L0 file whose content changed since the hash recorded in ``coverage.md``
    ("a decision changed - expand L1 and the tests, then run ``sync``").
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import time
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
SPEC = TOOLS / "spec"
TESTS = TOOLS / "tests"
COVERAGE = SPEC / "coverage.md"

#: Files in `spec/` that are *not* L0 specs (this layer's own bookkeeping).
OWNER_FILES = {"README.md", "coverage.md"}

OWNER_MARKER = "owner: user"
ITEM_RE = re.compile(r"^\s*[-*]\s+\*\*([A-Za-z][A-Za-z0-9]*-\d+)\*\*", re.MULTILINE)
ITEM_LINE_RE = re.compile(r"^\s*[-*]\s+\*\*([A-Za-z][A-Za-z0-9]*-\d+)\*\*(.*)$", re.MULTILINE)
#: `WR-31` is a label a human may renumber; `<!-- wr:id=point-read -->` is the identity the
#: machine reads (spec/README.md: 编号是给人看的标签，slug 只增不改). A label-keyed item still
#: works - the snapshot table below can bridge it - but a renumber then breaks the bridge.
SLUG_ANNOTATION_RE = re.compile(r"<!--\s*wr:id=([A-Za-z0-9][A-Za-z0-9._-]*)\s*-->")
SNAPSHOT_HEADING_RE = re.compile(r"^#{2,6}\s+.*编号快照.*$")
SNAPSHOT_ROW_RE = re.compile(
    r"^\|\s*([A-Za-z][A-Za-z0-9]*-\d+)\s*\|\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*\|\s*(.*?)\s*\|\s*$"
)
SECTION_RE = re.compile(r"^##\s+(\S+\.md)\s*$")
HASH_RE = re.compile(r"^l0_sha1:\s*(\S+)\s*$")
ROW_RE = re.compile(r"^\|\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*\|(.*)\|\s*$")
#: Table headers, not rows (`| slug | L1 章节 | … |`); the separator row `| --- |` never matches
#: ROW_RE because its first cell starts with a dash.
ROW_HEADERS = {"slug", "L0 条目", "编号", "item"}
HEADING_RE = re.compile(r"^#{1,6}\s+(.*?)\s*$")


def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def console_safe_output() -> None:
    """Print Chinese paths/statuses readably on a Windows console that is not UTF-8.

    A console at code page 936 decodes whatever it is given as 936, so a Python that
    writes UTF-8 (a Git-for-Windows python, for example) turns every path into mojibake.
    Best effort and never fatal; when it cannot be done, `STATUS_LABEL` at least keeps
    the status column ASCII.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes

        codepage = ctypes.windll.kernel32.GetConsoleOutputCP()
        if codepage:
            sys.stdout.reconfigure(encoding=f"cp{codepage}", errors="replace")
    except Exception:  # noqa: BLE001 - console cosmetics must never break the check
        pass


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def sha1_of(path: Path) -> str:
    """sha1 of the file's *text*, with line endings normalised to LF.

    Measured 2026-10-06: this clone has `core.autocrlf = true`, so the committed blob is LF
    while the working tree is CRLF - the raw bytes differ (`pdt_wrangle.md`: `a7397321…` in
    the index vs `a8bd98eb…` on disk). Hashing the raw bytes therefore records a
    platform-dependent baseline: on a machine with `autocrlf = false` (CI, Linux, a fresh
    clone with `core.eol = lf`) every recorded hash would mismatch and `check --strict` would
    report six false "the decision changed" warnings. Normalising here makes the baseline
    mean "this text", which is what the hash is actually for.
    """
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def slug(text: str) -> str:
    """A GitHub-ish heading slug, conservative enough for Chinese headings.

    Keeps ASCII letters/digits and CJK, turns whitespace into '-', drops everything else
    (so `保证（规范性）` -> `保证规范性`). Used only to catch a coverage row whose anchor
    no longer exists; a miss is a warning, never a hard failure.
    """
    keep = []
    for char in text.strip().lower():
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            keep.append(char)
        elif char.isspace() or char == "-":
            keep.append("-")
    return re.sub(r"-+", "-", "".join(keep)).strip("-")


def anchors(path: Path) -> set[str]:
    try:
        text = read(path)
    except OSError:
        return set()
    found = set()
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if match:
            found.add(slug(match.group(1)))
    return found


def l0_files() -> list[Path]:
    return sorted(p for p in SPEC.glob("*.md") if p.name not in OWNER_FILES)


def items_of(path: Path) -> list[str]:
    return [match.group(1) for match in ITEM_RE.finditer(read(path))]


def item_records(path: Path) -> list[dict]:
    """`[{label, slug, explicit, line, text}]` in file order.

    `slug` is the machine identity: the inline `<!-- wr:id=… -->` when present, otherwise the
    fallback `<file>-<label>` (flagged `explicit=False`, so `slugs` can list what still needs
    pasting and `resolve_slugs` can prefer the snapshot bridge).
    """
    records = []
    for number, line in enumerate(read(path).splitlines(), start=1):
        match = ITEM_LINE_RE.match(line)
        if not match:
            continue
        label = match.group(1)
        found = SLUG_ANNOTATION_RE.search(match.group(2))
        records.append(
            {
                "label": label,
                "slug": found.group(1) if found else f"{path.stem}-{label.lower()}",
                "explicit": bool(found),
                "line": number,
                "text": match.group(0).strip(),
            }
        )
    return records


def parse_snapshot(text: str) -> dict:
    """The `## 编号快照` tables of coverage.md: how labels map to identities over time.

    Returns `{"by_label", "current_by_label", "by_slug", "retired"}`. `by_label` holds every mapping
    ever recorded (that is what makes old text decodable); `current_by_label` only holds the labels a
    snapshot names as *current* ("现行 `WR-18`"). A live item may only be compared against the latter:
    a retired number reused by a brand-new item must not read as a mispasted annotation.
    """
    by_label: dict[str, str] = {}
    current_by_label: dict[str, str] = {}
    by_slug: dict[str, str] = {}
    retired: dict[str, str] = {}
    inside = False
    for line in text.splitlines():
        if SNAPSHOT_HEADING_RE.match(line):
            inside = True
            current_by_label.clear()  # a newer section supersedes the previous one's "current" map
            continue
        if inside and line.startswith("## ") and not SNAPSHOT_HEADING_RE.match(line):
            inside = False
            continue
        if not inside:
            continue
        row = SNAPSHOT_ROW_RE.match(line)
        if not row:
            continue
        label, slug, note = row.group(1), row.group(2), row.group(3)
        by_label[label] = slug
        by_slug[slug] = label
        if any(word in note for word in ("退役", "已并入", "已删除", "已删")):
            retired[slug] = note
            continue
        # A renumber snapshot also names the label the item carries *now* ("现行 `WR-18`"); later
        # sections are read last, so the newest snapshot's current labels win over older ones that
        # reused the same number for a different rule.
        current = re.search(r"`([A-Za-z][A-Za-z0-9]*-\d+)`", note)
        if current:
            by_label[current.group(1)] = slug
            current_by_label[current.group(1)] = slug
    return {"by_label": by_label, "current_by_label": current_by_label, "by_slug": by_slug,
            "retired": retired}


def resolve_slugs(path: Path, records: list[dict], snapshot: dict, problems: list[str]) -> dict:
    """`{label: slug}` for one L0 file, preferring the explicit annotation, then the snapshot."""
    resolved: dict[str, str] = {}
    seen: dict[str, str] = {}
    for record in records:
        label = record["label"]
        slug = record["slug"]
        if not record["explicit"]:
            bridged = snapshot["current_by_label"].get(label) or snapshot["by_label"].get(label)
            if bridged:
                slug = bridged
        resolved[label] = slug
        if slug in seen:
            problems.append(f"{slug}: slug used twice in {path.name} ({seen[slug]} and {label})")
        seen[slug] = label
        if slug in snapshot["retired"]:
            problems.append(f"{slug} ({label}): retired slug is live again in {path.name}")
    return resolved


def parse_coverage(text: str) -> dict[str, dict]:
    """`{L0 file name: {"hash": str, "rows": {key: (l1 cell, tests cell, status)}}}`.

    A section heading may be written either `## node_info.md` or `## spec/node_info.md`;
    both name the same L0 file. A row key is the item's **slug** when it has one (the label is
    then free to move), and its label otherwise. Any other `## ` heading ends the section, so
    tables that are not coverage rows (the 编号快照, notes) are never swallowed into one.
    """
    sections: dict[str, dict] = {}
    current: dict | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            heading = SECTION_RE.match(line)
            current = None
            if heading:
                name = heading.group(1)
                name = name[len("spec/"):] if name.startswith("spec/") else name
                current = sections.setdefault(name, {"hash": "", "rows": {}})
            continue
        if current is None:
            continue
        hashed = HASH_RE.match(line)
        if hashed:
            current["hash"] = hashed.group(1)
            continue
        row = ROW_RE.match(line)
        if row and row.group(1) not in ROW_HEADERS:
            cells = [cell.strip() for cell in row.group(2).split("|")]
            if len(cells) < 3:
                continue
            current["rows"][row.group(1)] = (cells[0], cells[1], cells[2])
    return sections


def split_cell(cell: str) -> list[str]:
    return [part.strip() for part in cell.split(",") if part.strip() and part.strip() != "—"]


def check_paths(item: str, l1_cell: str, test_cell: str, problems: list[str]) -> None:
    for entry in split_cell(l1_cell):
        rel, _, anchor = entry.partition("#")
        target = TOOLS / rel
        if not target.exists():
            problems.append(f"{item}: L1 path does not exist: {rel}")
        elif anchor and slug(anchor) not in anchors(target):
            problems.append(f"{item}: L1 anchor not found: {rel}#{anchor}")
    for name in split_cell(test_cell):
        target = TESTS / Path(name).name
        if not target.exists():
            problems.append(f"{item}: test does not exist: {name}")


def collect() -> tuple[list[Path], dict[str, dict], list[str]]:
    if not COVERAGE.exists():
        die(f"missing {COVERAGE}")
    files = l0_files()
    coverage_text = read(COVERAGE)
    sections = parse_coverage(coverage_text)
    snapshot = parse_snapshot(coverage_text)
    problems: list[str] = []
    for path in files:
        text = read(path)
        if OWNER_MARKER not in "\n".join(text.splitlines()[:3]):
            problems.append(
                f"{path.name}: the first three lines must carry <!-- {OWNER_MARKER}; "
                "agent: read-only -->"
            )
        section = sections.get(path.name)
        if section is None:
            problems.append(f"{path.name}: no '## spec/{path.name}' section in coverage.md")
            continue
        records = item_records(path)
        if not records:
            problems.append(f"{path.name}: no '**ID**' items found (see spec/README.md)")
        recorded = section["hash"]
        if recorded not in {"", "(unset)"} and recorded != sha1_of(path):
            problems.append(
                f"{path.name}: changed since coverage was written (recorded {recorded[:12]}, "
                f"now {sha1_of(path)[:12]}) - expand L1 + tests, then run 'sync'"
            )
        slugs = resolve_slugs(path, records, snapshot, problems)
        for record in records:
            if not record["explicit"]:
                continue
            expected = snapshot["current_by_label"].get(record["label"])
            if expected and expected != record["slug"]:
                problems.append(
                    f"{record['label']}: the annotation says '{record['slug']}' but the snapshot "
                    f"records '{expected}' for this label - a mispasted annotation, or a slug "
                    "rename that still needs its snapshot row updated"
                )
        live_keys = set(slugs.values()) | {record["label"] for record in records}
        for record in records:
            label = record["label"]
            slug = slugs[label]
            row = section["rows"].get(slug) or section["rows"].get(label)
            if row is None:
                problems.append(f"{slug} ({label}): no row in coverage.md")
                continue
            if row[2] == "待补":
                problems.append(f"{slug} ({label}): still a gap - no test covers it yet")
            check_paths(f"{slug} ({label})", row[0], row[1], problems)
        for key in section["rows"]:
            if key in live_keys:
                continue
            if key in snapshot["retired"]:
                problems.append(
                    f"{key}: coverage.md still has a live row for a retired slug "
                    "(the snapshot table is where it belongs)"
                )
                continue
            problems.append(f"{key}: coverage.md has a row but {path.name} has no such item")
    explicit = [record["slug"] for path in files for record in item_records(path)
                if record["explicit"]]
    duplicates = {slug for slug in explicit if explicit.count(slug) > 1}
    for slug in sorted(duplicates):
        problems.append(f"{slug}: slug used by more than one item (spec/README.md: 全局唯一)")
    return files, sections, problems


#: Console-safe labels: the coverage table is written in Chinese, but a Windows console at
#: code page 936 would print the UTF-8 bytes as mojibake.
STATUS_LABEL = {"已覆盖": "covered", "仅文档": "docs-only", "待补": "TODO"}


def cmd_list(_args: argparse.Namespace) -> int:
    coverage_text = read(COVERAGE)
    sections = parse_coverage(coverage_text)
    snapshot = parse_snapshot(coverage_text)
    for path in l0_files():
        print(f"{path.name}:")
        section = sections.get(path.name, {"rows": {}})
        records = item_records(path)
        slugs = resolve_slugs(path, records, snapshot, [])
        for record in records:
            label = record["label"]
            slug = slugs[label]
            l1, tests, status = section["rows"].get(slug) or section["rows"].get(label, ("?", "?", "?"))
            print(f"  {label:8s} {slug:28s} {STATUS_LABEL.get(status, status):9s} {l1}  [{tests}]")
    return 0


def cmd_slugs(args: argparse.Namespace) -> int:
    """Every item's identity, and (with --patch) the annotation still to paste into the L0."""
    coverage_text = read(COVERAGE)
    snapshot = parse_snapshot(coverage_text)
    missing = 0
    for path in l0_files():
        records = item_records(path)
        if not records:
            continue
        slugs = resolve_slugs(path, records, snapshot, [])
        print(f"{path.name}:")
        for record in records:
            label = record["label"]
            slug = slugs[label]
            if record["explicit"]:
                source = "written"
            elif slug in snapshot["by_slug"]:
                source = "bridged by the snapshot"
            else:
                source = "FALLBACK - needs an annotation"
                missing += 1
            print(f"  {label:8s} -> {slug:28s} ({source})")
            if args.patch and not record["explicit"]:
                print(f"      line {record['line']}: append  <!-- wr:id={slug} -->")
    print(f"{missing} item(s) without an explicit slug"
          + ("" if missing == 0 else " - paste the annotations above, then re-run"))
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    files, _sections, problems = collect()
    for problem in problems:
        print(f"WARN  {problem}")
    count = sum(len(items_of(path)) for path in files)
    print(f"checked {count} L0 item(s) in {len(files)} file(s): {len(problems)} warning(s)")
    if problems and args.strict:
        print("(--strict: warnings are failures)")
        return 1
    return 0


def cmd_sync(_args: argparse.Namespace) -> int:
    text = read(COVERAGE)
    lines = text.splitlines()
    out: list[str] = []
    current_file: Path | None = None
    written: set[str] = set()
    for line in lines:
        heading = SECTION_RE.match(line)
        if heading:
            name = heading.group(1)
            current_file = SPEC / Path(name).name
            written.add(Path(name).name)
        hashed = HASH_RE.match(line)
        if hashed and current_file is not None and current_file.exists():
            out.append(f"l0_sha1: {sha1_of(current_file)}")
            continue
        out.append(line)
    missing = [p.name for p in l0_files() if p.name not in written]
    changed = "\n".join(out) + ("\n" if text.endswith("\n") else "")
    if missing:
        for name in missing:
            changed += f"\n## spec/{name}\nl0_sha1: {sha1_of(SPEC / name)}\n\n| L0 条目 | L1 章节 | 测试 | 状态 |\n| --- | --- | --- | --- |\n"
    if changed != text:
        COVERAGE.write_text(changed, encoding="utf-8")
        print(f"updated {COVERAGE.name}")
    else:
        print(f"{COVERAGE.name} unchanged")
    for name in missing:
        print(f"NOTE  added an empty section for spec/{name} - fill in its rows")
    return 0


# --------------------------------------------------------------------------- renumber
#: Every label token in a line: the definition `**WR-31**` and an inline mention `见 WR-31` alike.
LABEL_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9-])([A-Za-z][A-Za-z0-9]*-\d+)(?![0-9])")
LABEL_PARTS_RE = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-(\d+)$")
#: Where a stale label in a *live* comment hurts (history is allowed to keep its own labels).
CITING_ROOTS = ("tools", "source/blender/nodes/geometry/nodes/custom")


def read_raw(path: Path) -> str:
    """Read without newline translation, so a rewrite keeps CRLF as it was."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


def write_raw(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def prefix_of(path: Path, records: list[dict]) -> str:
    counts: dict[str, int] = {}
    for record in records:
        parts = LABEL_PARTS_RE.match(record["label"])
        if parts:
            counts[parts.group(1)] = counts.get(parts.group(1), 0) + 1
    if not counts:
        die(f"{path.name}: no '<PREFIX>-<number>' label to renumber")
    return max(counts, key=lambda key: (counts[key], key))


def citing_files(labels: set[str]) -> list[tuple[str, int]]:
    """Live files outside spec/ that cite one of these labels: `[(path, hits)]`, busiest first."""
    repo = TOOLS.parent.parent
    hits: dict[str, int] = {}
    for root in CITING_ROOTS:
        base = TOOLS / root if root == "tools" else repo / root
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or path.suffix not in {".md", ".py", ".cc", ".hh", ".ps1"}:
                continue
            if SPEC in path.parents:
                continue
            try:
                text = read(path)
            except (OSError, UnicodeDecodeError):
                continue
            count = sum(1 for found in LABEL_TOKEN_RE.finditer(text) if found.group(1) in labels)
            if count:
                hits[str(path.relative_to(repo)).replace("\\", "/")] = count
    return sorted(hits.items(), key=lambda entry: (-entry[1], entry[0]))


def append_snapshot(rows: list[tuple[str, str, str]], title: str, note: str) -> None:
    """Append one frozen `编号快照` section, so the labels used before this pass stay decodable."""
    text = read_raw(COVERAGE)
    block = [f"## {title}", "", note, "",
             "| 当时编号 | slug | 现状 |", "| --- | --- | --- |"]
    block += [f"| {label} | {slug} | {state} |" for label, slug, state in rows]
    write_raw(COVERAGE, text.rstrip("\n") + "\n\n" + "\n".join(block) + "\n")


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Append a frozen `编号快照` section recording the current label -> slug map.

    `renumber` writes one automatically when it moves labels; this covers the other case - a revision
    that only adds or removes items - so the newest snapshot stays a complete map of the current
    labels, which is what `check` compares annotations against.
    """
    paths = [Path(args.file).resolve()] if args.file else l0_files()
    today = time.strftime("%Y-%m-%d")
    rows: list[tuple[str, str, str]] = []
    for path in paths:
        if not path.exists():
            die(f"no such file: {path}")
        for record in item_records(path):
            rows.append((record["label"], record["slug"], f"现行 `{record['label']}`"))
    if not rows:
        die("nothing to record: no items found")
    title = f"编号快照（{today} {args.note}）" if args.note else f"编号快照（{today}）"
    note = args.note or "记录这一版的编号 → slug 映射。"
    append_snapshot(rows, title, note)
    print(f"appended {len(rows)} row(s) to {COVERAGE.name} as '{title}'")
    return 0


def cmd_renumber(args: argparse.Namespace) -> int:
    """Renumber the labels of every item in file order, keeping slugs (the identity) untouched.

    Labels are display; slugs are identity (spec/README.md). Renumbering therefore edits only the
    L0 file itself - inline references inside it are rewritten too, and `coverage.md` gets a frozen
    snapshot so the labels that were just replaced stay decodable. Nothing else needs touching.
    """
    paths = [Path(args.file).resolve()] if args.file else l0_files()
    today = time.strftime("%Y-%m-%d")
    jobs: list[dict] = []
    moved_total = 0
    for path in paths:
        if not path.exists():
            die(f"no such file: {path}")
        text = read_raw(path)
        records = item_records(path)
        if not records:
            print(f"{path.name}: no items, skipped")
            continue
        missing = [record["label"] for record in records if not record["explicit"]]
        if missing and args.write and not args.force:
            die(f"{path.name}: {len(missing)} item(s) without <!-- wr:id=… --> "
                f"({', '.join(missing[:6])}{'…' if len(missing) > 6 else ''}) - an un-annotated item "
                "loses its identity when its number moves; annotate them (see 'slugs --patch') or "
                "pass --force to renumber anyway")
        prefix = args.prefix or prefix_of(path, records)
        # One new label per *item*, by position: a `{label: new}` dict would let a duplicated label
        # (a half-finished edit) collapse two items onto one number.
        by_line = {record["line"]: f"{prefix}-{args.start + index}"
                   for index, record in enumerate(records)}
        line_starts = [0]
        for line in text.splitlines(keepends=True):
            line_starts.append(line_starts[-1] + len(line))
        definition_at: dict[int, str] = {}
        for record in records:
            line = text.splitlines(keepends=True)[record["line"] - 1]
            column = line.find(f"**{record['label']}**")
            if column >= 0:
                definition_at[line_starts[record["line"] - 1] + column + 2] = by_line[record["line"]]
        counts: dict[str, int] = {}
        for record in records:
            counts[record["label"]] = counts.get(record["label"], 0) + 1
        duplicates = sorted(label for label, count in counts.items() if count > 1)
        # Inline mentions (`见 WR-31`) may only be rewritten for a label that names exactly one item.
        inline_map = {record["label"]: by_line[record["line"]]
                      for record in records if counts[record["label"]] == 1}
        if duplicates and args.write and not args.force:
            die(f"{path.name}: duplicate label(s) {', '.join(duplicates)} - renumbering would leave "
                "their inline mentions pointing at the wrong item; fix the duplicate (or --force)")

        def rewrite(found: re.Match) -> str:
            if found.start() in definition_at:
                return definition_at[found.start()]
            return inline_map.get(found.group(1), found.group(1))

        new_text = LABEL_TOKEN_RE.sub(rewrite, text)
        moved = [record for record in records if by_line[record["line"]] != record["label"]]
        moved_total += len(moved)
        width = max(len(record["label"]) for record in records)
        print(f"{path.name}: prefix={prefix}, {len(records)} item(s), "
              f"{len(moved)} label(s) move, {len(records) - len(moved)} stay")
        for record in records:
            new = by_line[record["line"]]
            mark = "->" if new != record["label"] else "  "
            print(f"  {record['label']:<{width}} {mark} {new:<{width}}  {record['slug']}"
                  f"{'' if record['explicit'] else '   (no slug annotation!)'}")
        if duplicates:
            print(f"  WARN  duplicate label(s): {', '.join(duplicates)} - their inline mentions are "
                  "left alone (fix the duplicate, then re-run)")
        if missing:
            print(f"  WARN  {len(missing)} item(s) have no slug annotation - their identity is now "
                  "only the number, so a later edit can lose track of them")
        if new_text != text:
            jobs.append({"path": path, "text": new_text,
                         "rows": [(record["label"], record["slug"],
                                   f"现行 `{by_line[record['line']]}`")
                                  for record in records]})
        elif args.write:
            print("  already numbered 1..N in file order, nothing to write")
    if not jobs:
        print("\nnothing to write: every file is already numbered 1..N in file order")
        return 0
    rows = [row for job in jobs for row in job["rows"]]
    if not args.write:
        print(f"\n(dry run) {len(jobs)} file(s) / {moved_total} label(s) would change; pass --write "
              "to apply (a numbered snapshot is appended to coverage.md first)")
        return 0
    if rows:
        if not COVERAGE.exists():
            die(f"cannot record the snapshot: {COVERAGE} does not exist (see spec/README.md). "
                "Nothing was written.")
        append_snapshot(rows, f"编号快照（{today} 重编号）",
                        f"`pdt_spec.py renumber` 于 {today} 按文件顺序重排了编号；"
                        "左列是重排前的编号，读旧文本时照它查。")
        print(f"\nappended a snapshot to {COVERAGE.name} ({len(rows)} row(s))")
    for job in jobs:
        write_raw(job["path"], job["text"])
        print(f"wrote {job['path']}")
    print("next: python tools\\pdt\\pdt_spec.py sync   # refresh the recorded L0 hashes")
    stale = citing_files({label for label, _slug, _state in rows})
    if stale:
        print("still citing these labels outside spec/ (history may keep them; a live comment "
              "should cite the slug or the concept instead):")
        for name, count in stale[:12]:
            print(f"  {count:4d}  {name}")
    return 0


def main() -> int:
    console_safe_output()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="list L0 items and where they are covered").set_defaults(func=cmd_list)
    slugs = sub.add_parser("slugs", help="list every item's stable slug (and what still needs pasting)")
    slugs.add_argument("--patch", action="store_true",
                       help="also print the annotation and line number for each un-annotated item")
    slugs.set_defaults(func=cmd_slugs)
    check = sub.add_parser("check", help="advisory report on L0 -> L1 -> tests")
    check.add_argument("--strict", action="store_true", help="exit 1 when there are warnings")
    check.set_defaults(func=cmd_check)
    sub.add_parser("sync", help="refresh the recorded L0 hashes").set_defaults(func=cmd_sync)
    snap = sub.add_parser("snapshot", help="record the current label -> slug map as a new snapshot")
    snap.add_argument("--file", help="one L0 file (default: every file in spec/)")
    snap.add_argument("--note", help="what this revision is (goes into the section title)")
    snap.set_defaults(func=cmd_snapshot)
    ren = sub.add_parser("renumber", help="renumber every item in file order (labels are display)")
    ren.add_argument("--file", help="one L0 file (default: every file in spec/)")
    ren.add_argument("--prefix", help="label prefix (default: the file's most common one)")
    ren.add_argument("--start", type=int, default=1, help="first number (default 1)")
    ren.add_argument("--write", action="store_true", help="apply (default is a dry run)")
    ren.add_argument("--force", action="store_true",
                     help="renumber even when items have no slug annotation")
    ren.set_defaults(func=cmd_renumber)
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
