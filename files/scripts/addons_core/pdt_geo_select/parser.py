# SPDX-FileCopyrightText: 2026 PDT Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""The selection-string grammar, and what the picker may pre-select from it.

The syntax exists twice - C++ evaluates it for the node, Python needs it to pre-select an
existing value in the viewport - and `tests/test_selection_parser.py` cross-checks both on
the same expressions, so drift fails the suite. Only indices and `*` are evaluated here
(L0 `PICK-2`): group names and `@attribute` tokens produce no indices, and an operator on
one of them stays on the safe side of the unknown (`resolve_indices()`), which is what
keeps `预选集合 ⊆ 节点选择集合` true."""

from __future__ import annotations

import re
import math

from .utils.constants import MESH_POINT_DOMAIN, blender_attr_domain

def encode_indices(indices) -> str:
    """Compress sorted/unordered indices into Houdini group syntax."""
    values = sorted(set(int(i) for i in indices))
    if not values:
        return ""
    parts = []
    run_start = values[0]
    run_prev = values[0]
    step = None
    for value in values[1:]:
        delta = value - run_prev
        if step is None:
            step = delta
            run_prev = value
            continue
        if delta == step:
            run_prev = value
            continue
        parts.append(_format_run(run_start, run_prev, step))
        run_start = run_prev = value
        step = None
    parts.append(_format_run(run_start, run_prev, step if step is not None else 1))
    return " ".join(parts)


def _format_run(first: int, last: int, step: int) -> str:
    if first == last:
        return str(first)
    if step == 1:
        return f"{first}-{last}"
    return f"{first}-{last}:{step}"


class SelectionSpec:
    """Mirror of the C++ `Program`: the operations in source order plus the start
    value.

    Keeping operations (instead of a materialized index set) means `*` stays cheap
    and out-of-range indices simply never match. Group and attribute patterns keep
    their *text*: only the node resolves them (see `resolve_indices`), which is why
    the parser does not turn them into indices.
    """

    __slots__ = ("default_all", "operations")

    def __init__(self, default_all, operations):
        self.default_all = bool(default_all)
        self.operations = tuple(operations)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"SelectionSpec(default_all={self.default_all}, operations={self.operations!r})"

    def patterns(self):
        """Every pattern in the expression, sub-expressions included."""
        found = []
        for operation in self.operations:
            pattern = operation["pattern"]
            if pattern["type"] == "sub":
                continue
            found.append(pattern)
        return found


#: `@attr`, `@attr.x`, `@attr[2]`, `@intrinsic:name`. The name ends at the first
#: component selector or comparison operator, like the C++ parser.
_ATTRIBUTE_NAME_RE = re.compile(r"@([A-Za-z_][A-Za-z0-9_:]*)(?:\[(\d+)\]|\.([xruygvbzw]))?")


_ATTRIBUTE_OPS = ("==", "!=", ">=", "<=", "=", ">", "<", "&")


#: Component suffix -> index, mirroring .x/.y/.z, .r/.g/.b and .u/.v/.w.
_COMPONENT_INDEX = {
    "x": 0, "y": 1, "z": 2,
    "r": 0, "g": 1, "b": 2,
    "u": 0, "v": 1, "w": 2,
}


#: Index operations that only touch one index.
_ADD = "add"


_SUBTRACT = "subtract"


_INTERSECT = "intersect"


def _code_points(text: str) -> list[int]:
    """The characters of `text` as code points, like the C++ parser's bytes.

    The C++ side iterates `StringRef` (bytes, so ASCII), so anything above ASCII
    has to be rejected on both sides instead of being read as a letter.
    """
    return [ord(c) for c in text]


def _ascii_alpha_code(code: int) -> bool:
    return (65 <= code <= 90) or (97 <= code <= 122)


def _group_name_start_code(code: int) -> bool:
    # `?` and `[` may start a group token (`?*`, `[abc]*`): they can only be
    # wildcards, never an index, so there is nothing to disambiguate. A bare `*` is
    # handled before this (it is "all"), so `*` is deliberately not a start.
    return _ascii_alpha_code(code) or code in (95, 63, 91)  # '_', '?', '['


def _group_name_char_code(code: int) -> bool:
    return _group_name_start_code(code) or (48 <= code <= 57) or code in (42, 63, 91, 93)


def _ascii_group_token(body: str) -> bool:
    """True when every code point is allowed in a group token (see the C++ side).

    Inside a `[...]` character class the glob matcher supports ranges (`[a-z]`) and
    negation (`[^abc]` / `[!abc]`), so `-`, `^` and `!` are valid *there* - forbidding
    them made both features unreachable and turned `arm[0-9]` into a syntax error.
    Outside a class they stay invalid, so `a-b` is still rejected. Classes also have
    to be balanced, otherwise `arm[` is a typo that silently matches nothing.
    """
    codes = _code_points(body)
    if not codes or not _group_name_start_code(codes[0]):
        return False
    in_class = False
    for code in codes:
        if in_class:
            if code == 93:  # ']'
                in_class = False
            elif code == 91:  # '['
                return False
            elif not _group_name_char_code(code) and code not in (45, 94, 33):
                return False
            continue
        if not _group_name_char_code(code):
            return False
        if code == 91:  # '['
            in_class = True
    return not in_class


def _tokenize_selection(text: str):
    """Split into tokens, keeping braces separate and quotes inside a token.

    Mirrors tokenize() in node_geo_pdt_selection.cc; returns None on an
    unterminated quote or unbalanced brace.
    """
    tokens: list[dict] = []
    current: list[str] = []
    in_quotes = False
    has_quotes = False
    open_braces = 0

    def flush():
        nonlocal has_quotes
        if current or has_quotes:
            tokens.append({"text": "".join(current), "quoted": has_quotes})
            current.clear()
            has_quotes = False

    for char in text:
        if in_quotes:
            if char == '"':
                in_quotes = False
            else:
                current.append(char)
            continue
        if char == '"':
            in_quotes = True
            has_quotes = True
            continue
        if char == "{":
            flush()
            open_braces += 1
            tokens.append({"text": "{", "quoted": False})
            continue
        if char == "}":
            flush()
            if open_braces <= 0:
                return None
            open_braces -= 1
            tokens.append({"text": "}", "quoted": False})
            continue
        if char in " ,\t\n\r":
            flush()
            continue
        current.append(char)
    if in_quotes:
        return None
    flush()
    if open_braces > 0:
        return None
    return tokens


def _merge_brace_operators(tokens):
    """Attach `!`/`^`/`&` to the `{` that the tokenizer split off."""
    merged = []
    index = 0
    while index < len(tokens):
        token = tokens[index]["text"]
        if index + 1 < len(tokens) and tokens[index + 1]["text"] == "{" and token in ("!", "^", "&"):
            merged.append({"text": token + "{", "quoted": False})
            index += 2
            continue
        merged.append(tokens[index])
        index += 1
    return merged


def _parse_numeric_pattern(body: str):
    """`5` / `0-10` / `0-100:2` -> (first, last, step), or None."""
    step = 1
    digits = body
    if ":" in body:
        digits, _, step_text = body.partition(":")
        if not step_text.isascii() or not step_text.isdigit() or int(step_text) < 1:
            return None
        step = int(step_text)
    if "-" in digits:
        first_text, _, last_text = digits.partition("-")
        if not first_text.isascii() or not first_text.isdigit():
            return None
        if not last_text.isascii() or not last_text.isdigit():
            return None
        first, last = int(first_text), int(last_text)
        if last < first:
            return None
    else:
        if not digits.isascii() or not digits.isdigit():
            return None
        first = last = int(digits)
    return (first, last, step)


def _value_is_list(value: str) -> bool:
    """True when the value is an integer range / space separated list (`0-4 78`).

    Mirrors value_is_list() in node_geo_pdt_selection.cc. No negative numbers, like the
    C++ side: `-` only ever separates a range.
    """
    parts = value.split()
    return bool(parts) and all(re.fullmatch(r"\d+(?:-\d+)?", part) for part in parts)


def _parse_attribute_pattern(body: str, quoted: bool):
    """Parse `@attr[comp]<op><value>`, returning a pattern dict or None."""
    match = _ATTRIBUTE_NAME_RE.match("@" + body)
    if match is None:
        return None
    name = match.group(1)
    if match.group(2) is not None:
        component = int(match.group(2))
    elif match.group(3) is not None:
        component = _COMPONENT_INDEX[match.group(3)]
    else:
        component = -1
    rest = body[match.end() - 1:]
    op = next((candidate for candidate in _ATTRIBUTE_OPS if rest.startswith(candidate)), None)
    if op is None:
        return None
    value = rest[len(op):]
    if value == "":
        return None
    compare_op = {"==": "="}.get(op, op)

    # `=` / `!=` also take a value list, and quoting is the only way to write one that
    # contains a space (the tokenizer splits on whitespace). So a quoted value that
    # really is a numeric list is a value list, not a string.
    list_ok = compare_op in ("=", "!=") and _value_is_list(value)
    if quoted and not list_ok:
        # A quoted non-list is how a string attribute value would be written, and
        # strings are not supported: reject rather than guess.
        return None
    try:
        number = float(value)
    except ValueError:
        if not list_ok:
            # `>` with a non-number, or `=` with something that is not a list - a typo
            # that could never match. Reject it instead of silently selecting nothing.
            return None
        number = None
    if number is not None and not math.isfinite(number):
        return None
    return {
        "type": "attr",
        "attribute": name,
        "op": compare_op,
        "component": component,
        "value": number,
        "text": value,
    }


def _parse_pattern(token: dict):
    """Parse one token (no leading operator) into a pattern dict, or None."""
    body = token["text"]
    if body == "*":
        return {"type": "all"}
    if not body:
        return None
    if body.startswith("@"):
        return _parse_attribute_pattern(body[1:], token["quoted"])
    if _group_name_start_code(_code_points(body)[0]):
        return {"type": "group", "name": body} if _ascii_group_token(body) else None
    if body[0].isascii() and body[0].isdigit():
        numeric = _parse_numeric_pattern(body)
        if numeric is None:
            return None
        return {"type": "range", "first": numeric[0], "last": numeric[1], "step": numeric[2]}
    return None


def _parse_tokens(tokens, pos, top_level: bool):
    """Parse from `pos` into (operations, default_all, next_pos), or None.

    Operations are a flat list, so a `{ ... }` sub-expression is just the index
    range of its own operations - exactly like the C++ `OperationList`.
    """
    operations: list[dict] = []
    saw_add = False
    while pos < len(tokens):
        token = tokens[pos]
        body = token["text"]
        if body == "}":
            if top_level:
                return None
            pos += 1
            break

        kind = _ADD
        negate = False
        brace_operand = False
        if body.startswith("!{"):
            negate = True
            brace_operand = True
        elif body.startswith("^{"):
            kind = _SUBTRACT
            brace_operand = True
        elif body.startswith("&{"):
            kind = _INTERSECT
            brace_operand = True
        elif body == "{":
            # A *bare* sub-expression: `_tokenize_selection` emits `{` as its own
            # token and `_merge_brace_operators` only glues an operator onto it, so
            # without this branch `{0-3}` reached `_parse_pattern()` and failed.
            brace_operand = True
        elif body.startswith("^"):
            kind = _SUBTRACT
            body = body[1:]
            if not body:
                return None
        elif body.startswith("&"):
            kind = _INTERSECT
            body = body[1:]
            if not body:
                return None
        elif body.startswith("!"):
            negate = True
            body = body[1:]
            if not body:
                return None

        if brace_operand:
            pos += 1
            # The operation that *consumes* the sub-expression goes into the list
            # before its operand, so `_apply_operations` can evaluate the operand as
            # a unit and then skip its range. With the operand first, the outer loop
            # applied it a second time as well: harmless for a plain add (`{arm*}`
            # unions the same set twice) but wrong for `!{arm*}`, which unioned
            # `arm*` and `!arm*` into "everything". Mirrors parse_tokens() in
            # node_geo_pdt_selection.cc.
            if kind == _ADD:
                saw_add = True
            operation = {
                "kind": kind,
                "negate": negate,
                "pattern": {"type": "sub", "default_all": False, "start": 0, "end": 0},
            }
            operations.append(operation)
            sub_start = len(operations)
            sub = _parse_tokens(tokens, pos, top_level=False)
            if sub is None:
                return None
            sub_operations, sub_default_all, pos = sub
            operations.extend(sub_operations)
            # `{}` leaves the range empty, which matches nothing - no special case.
            operation["pattern"]["default_all"] = sub_default_all
            operation["pattern"]["start"] = sub_start
            operation["pattern"]["end"] = len(operations)
            continue

        pattern = _parse_pattern({"text": body, "quoted": token["quoted"]})
        if pattern is None:
            return None
        operations.append({"kind": kind, "negate": negate, "pattern": pattern})
        if kind == _ADD:
            # `P`, `*` and `!P` all start an "add" run; only `!P` adds a complement.
            # Counting `!P` here is what makes a leading `!A` start from *nothing* and
            # yield the complement of A instead of degenerating to "everything".
            saw_add = True
        pos += 1

    if not top_level and pos > len(tokens):
        return None
    # Applies at every level, so `{^arm3*}` is also "all but arm3".
    default_all = not saw_add and bool(operations)
    return (operations, default_all, pos)


def parse_selection(text: str):
    """Parse Houdini group syntax into a SelectionSpec, or None when malformed.

    The syntax is implemented twice - C++ parses for evaluation, Python needs it
    to validate names and to pre-select an existing value in the viewport - so
    tools/pdt/tests/test_selection_parser.py cross-checks both implementations and
    drift fails the test suite.

    Grammar (Houdini group patterns), evaluated left to right over one set:

    * ``token`` unions the pattern into the accumulated set;
    * ``^token`` subtracts it; ``&token`` intersects it;
    * ``!token`` adds the complement of the pattern;
    * ``{ ... }`` is a sub-expression with its own accumulator;
    * a list that starts with an operator starts from *everything*.

    Patterns are an index (``5``), a range (``0-10``, ``0-100:2``), ``*``, a group
    name or wildcard (``A``, ``arm*``), or an attribute pattern (``@position.y>0``).

    Python only *evaluates* index patterns (``resolve_indices``): group and
    attribute tokens are parsed here for validation and for the panel summary, but
    they are never expanded into indices, because the picker writes indices back
    and the node owns the group/attribute semantics.
    """
    tokens = _tokenize_selection(text)
    if tokens is None:
        return None
    parsed = _parse_tokens(_merge_brace_operators(tokens), 0, top_level=True)
    if parsed is None:
        return None
    operations, default_all, _pos = parsed
    return SelectionSpec(default_all, operations)


def _indices_for_pattern(pattern, limit: int, mesh, domain: str) -> set[int]:
    """The indices `pattern` is **known** to select, within a domain of `limit` elements.

    Only index ranges and `*` are evaluated (L0 PICK-2). A group or attribute pattern is
    deliberately not expanded - the node owns those semantics, and reimplementing the
    wildcard matching, `adapt_domain` and attribute rules here would drift - so the picker
    knows only that it selects *something*, never *what*: the empty set. See
    `_operation_indices()` for what that implies for `^`, `&` and `!`.
    """
    kind = pattern["type"]
    if kind == "all":
        return set(range(limit))
    if kind == "range":
        first, last, step = pattern["first"], pattern["last"], pattern["step"]
        if first >= limit:
            return set()
        return set(range(first, min(last, limit - 1) + 1, step))
    return set()


def _pattern_may_select(pattern, limit: int, mesh, domain: str) -> set[int]:
    """Every index `pattern` *might* select: exact for indices and `*`, the whole domain for
    a group or attribute pattern, whose expansion only the node knows (PICK-2)."""
    known = _indices_for_pattern(pattern, limit, mesh, domain)
    return known if pattern["type"] in {"all", "range"} else set(range(limit))


def resolve_indices(spec, limit: int, mesh=None, domain: str = "POINT") -> set[int]:
    """Indices certainly selected by `spec` inside a domain of `limit` elements.

    Mirrors the node's left-to-right accumulation: every operation applies to the result of
    the previous one, and a list that starts with an operator starts from everything (so
    `^0-3` is "all but the first four").

    What comes back is always a **subset** of what the node selects, never more: only index
    patterns and `*` are evaluated, and an operator on a group/attribute pattern stays on the
    safe side of the unknown (see `_operation_indices`). ``mesh``/``domain`` are accepted for
    that evaluation; the node owns group semantics and the picker writes indices back.
    """
    if spec is None:
        return set()
    if not spec.operations:
        return set()
    selected = set(range(limit)) if spec.default_all else set()
    may_select = set(selected)
    _apply_operations(
        spec.operations, range(len(spec.operations)), limit, mesh, domain, selected, may_select
    )
    return selected


def _apply_operations(operations, scope, limit, mesh, domain, target: set[int], may: set[int]) -> None:
    """Apply `scope` of `operations` to `target`, keeping `may` as its upper bound.

    A sub-expression's operations live further down the same flat list and the operation that
    consumes them comes first, so after evaluating one, jump past its range - otherwise the
    operand would be applied a second time by this loop. `!{...}` needs the operand's upper
    bound as well: complementing a set that is only known to be a subset could claim elements
    the node does not select.
    """
    skip_until = scope.start
    for position in scope:
        if position < skip_until:
            continue
        operation = operations[position]
        kind, negate, pattern = operation["kind"], operation["negate"], operation["pattern"]
        if pattern["type"] == "sub":
            sub_known = set(range(limit)) if pattern["default_all"] else set()
            sub_may = set(sub_known)
            _apply_operations(
                operations,
                range(pattern["start"], pattern["end"]),
                limit,
                mesh,
                domain,
                sub_known,
                sub_may,
            )
            skip_until = pattern["end"]
        else:
            sub_known = _indices_for_pattern(pattern, limit, mesh, domain)
            sub_may = _pattern_may_select(pattern, limit, mesh, domain)
        if negate:
            # The complement of the *upper* bound is the lower bound, and the other way round.
            sub_known, sub_may = (
                {index for index in range(limit) if index not in sub_may},
                {index for index in range(limit) if index not in sub_known},
            )
        if kind == _ADD:
            target |= sub_known
            may |= sub_may
        elif kind == _SUBTRACT:
            target -= sub_may
            may -= sub_known
        else:
            target &= sub_known
            may &= sub_may
    return None


def preselect_indices(text: str, limit: int, mesh=None, domain: str = MESH_POINT_DOMAIN) -> set[int]:
    """Indices of an existing parameter value, clamped to the temporary mesh.

    `domain` is the session's element kind; the attribute lookups inside use the
    matching `AttrDomain`.
    """
    return resolve_indices(
        parse_selection(text), limit, mesh, blender_attr_domain(domain)
    )
