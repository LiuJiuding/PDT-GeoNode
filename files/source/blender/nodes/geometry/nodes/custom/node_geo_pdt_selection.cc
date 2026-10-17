/* SPDX-FileCopyrightText: 2026 PDT Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo_nodes
 *
 * PDT Selection: pick geometry elements by index, Houdini group style, and
 * expose the result as a boolean field.
 *
 * The parameter is a Houdini group pattern list, evaluated **left to right over
 * one accumulated set** - the same model Houdini uses. Every token starts from
 * the result of the previous one:
 *
 *   pattern        union - add the pattern's elements
 *   ^pattern       subtract - remove the pattern's elements
 *   &pattern       intersect - keep only the elements the pattern matches
 *   !pattern       complement - add every element the pattern does *not* match
 *   { ... }        a sub-expression with its own accumulator: `{arm* ^arm3*}`
 *
 * Patterns are:
 *
 *   *              everything
 *   5 / 0-10       one index / an inclusive run
 *   0-100:2        a run with a step
 *   A              the boolean attribute `group:A`
 *   arm* / ?*      every group whose *name* matches the wildcard
 *   @position.y>0 an attribute comparison (see below)
 *
 * The first token decides what an operator applies to. A list that starts with `^`
 * or `&` starts from "everything", so `^A` means "all but the members of A" -
 * "nothing" is never what the user meant. A list that starts with `!` starts from
 * "nothing" and *adds a complement*, so `!A` is also "all but A", but by a different
 * route. `0-10 rim ^5` therefore evaluates as (({0..10} | rim) - {5}).
 *
 * `!P` is not a per-pattern inversion and it does not toggle a "negated" state: it
 * *adds the complement* of `P` to the accumulated set, so `A !B` is `A | !B` and
 * `!A !B` is `!A | !B` = NOT(A & B). `^` and `&` always apply to the accumulated set
 * in source order, which is why `A &B 0` is `(A & B) | {0}` and not `A & (B | {0})`.
 *
 * Attribute patterns (`@attr<op><value>`): `<op>` is `=`, `==`, `!=`, `>`, `<`,
 * `>=`, `<=` or `&` (integer bitwise-and). A component can be named with `[n]`
 * or `.x`/`.y`/`.z`/`.r`/`.g`/`.b`/`.u`/`.v`/`.w`; without one the first
 * component is used, exactly like Houdini. `@position.y>0` reads the point
 * position, `@ptnum` / `@elemnum` read the element number, and everything
 * else is a real numeric geometry attribute (`@mass>0`, `@id=5-10`). A value may
 * be a range or a space separated list for `=` / `!=`: `@id=0-4`, or `@id="0-4 78"`
 * once the list has more than one part, because the tokenizer splits on whitespace.
 * String attributes are not implemented (Blender keeps them
 * outside the normal attribute accessor), so a quoted value is reported as a
 * syntax error instead of silently selecting nothing.
 *
 * Groups live in the attribute namespace `group:<name>` (the one the PDT picker
 * writes); a wildcard (`arm*`, `?*`, `arm[0-9]`) unions every matching group. They
 * cannot be resolved while the node declares its sockets: the geometry input does
 * not have to be connected, and the attributes only exist on the geometry the field
 * is evaluated against. Evaluation therefore happens lazily in
 * `PDTSelectionFieldInput::get_varray_for_context()`. A group pattern that matches
 * nothing, or that only matches non-boolean attributes, contributes no members:
 * `node_geo_exec()` reports that as a *warning* whenever it can see the geometry,
 * because `0-10 typo` must keep selecting `0-10`. Syntax errors are errors and fail
 * safe (geometry passes through, nothing is selected).
 *
 * A geometry set can carry several components at once (a mesh and a curve in the
 * same stream), and the field is evaluated once per component. `Domain` therefore
 * names an **element kind**, not a plain attribute domain: `Vertex` / `Edge` / `Face`
 * address the mesh, `Control Point` / `Spline` the curve, `Point` the point cloud and
 * `Instance` the top level instances. Blender's own `AttrDomain` cannot express this -
 * mesh verts, curve control points and point cloud points are all `AttrDomain::Point` -
 * so the kinds are a node-specific enum (`IndexDomain`) whose value also decides the
 * component the node addresses; every other component selects nothing. Measurements of
 * the mixed case are in tools/pdt/docs/archive/07-曲线支持评估.md §13, the instance
 * domain in tools/pdt/docs/archive/08-实例支持评估.md.
 *
 * The kind is a **node property** (RNA enum over `NodeGeometryPDTSelection`, the way
 * `Delete Geometry` carries its `Domain`), not an input socket: there is nothing a menu
 * socket could be driven by, and a property cannot be wired at all. It is drawn in the
 * node body by `node_layout()`.
 *
 * NOTE: the instance domain is **isolated** in Blender's field system: it is not part
 * of the point/edge/face/corner propagation chain, `adapt_domain()` returns an empty
 * array for every cross-domain conversion. `Domain = Instance` wired into a
 * point-domain consumer therefore selects *nothing* (no interpolation, no error) -
 * the same way `Domain = Point` selects nothing on instances, which is what this node
 * did before the instance domain existed (measured, tools/pdt/docs/archive/08-实例支持评估.md
 * §2.1).
 *
 * The syntax is implemented twice: here for evaluation, and in Python
 * (`scripts/addons_core/pdt_geo_select`) for syntax validation and for the panel
 * summary. The picker only *evaluates* index patterns - it writes indices back -
 * so the two implementations are cross-checked on the index subset by
 * `tools/pdt/tests/test_selection_parser.py`, and the group/attribute behaviour is
 * pinned down against this node by `tools/pdt/tests/test_selection_patterns.py`.
 *
 * NOTE: the parsed expression participates in `hash_unique()`. Without that the
 * field cache would treat two different selections as the same field. Group and
 * attribute *names* are part of the hash for the same reason, even though their
 * values come from the geometry.
 */

#include <algorithm>
#include <string>
#include <variant>

#include "BKE_attribute.hh"
#include "BKE_attribute_enums.hh"
#include "BKE_geometry_fields.hh"
#include "BKE_geometry_set.hh"
#include "BKE_idprop.hh"
#include "BKE_instances.hh"

#include "BLI_array.hh"
#include "BLI_function_ref.hh"
#include "BLI_math_base.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_span.hh"
#include "BLI_string_ref.hh"
#include "BLI_unique_hash.hh"
#include "BLI_vector.hh"

#include "NOD_rna_define.hh"

#include "UI_interface_layout.hh"
#include "UI_resources.hh"

#include "node_geometry_util.hh"

namespace blender::nodes::node_geo_pdt_selection_cc {

NODE_STORAGE_FUNCS(NodeGeometryPDTSelection)

/* ------------------------------------------------------------------------- */
/* pattern model                                                             */
/* ------------------------------------------------------------------------- */

/** One `first-last:step` run. `step` is always >= 1 and `last >= first`. */
struct Slot {
  int64_t first = 0;
  int64_t last = 0;
  int64_t step = 1;

  bool contains(const int64_t index) const
  {
    if (index < first || index > last) {
      return false;
    }
    return ((index - first) % step) == 0;
  }
};

struct AllPattern {};

/** A group reference: `name` is a literal group name, or a wildcard pattern when
 * it contains `*`, `?` or `[...]` (then it matches every `group:<name>`). */
struct GroupPattern {
  std::string name;
};

enum class CompareOp : int8_t { Eq, Ne, Lt, Le, Gt, Ge, BitAnd };

struct ComparePattern {
  std::string attribute;
  CompareOp op = CompareOp::Eq;
  std::string value;
  /** -1 means "the first component", like Houdini. */
  int component = -1;
  bool value_is_quoted = false;
};

enum class OpKind : int8_t { Add, Subtract, Intersect };

/**
 * One `{ ... }` sub-expression: the operations live in `Program::operations`, at
 * `[start, end)`, so that `Operation` stays complete when the variant is
 * instantiated (a `std::variant` alternative has to be a complete type - a
 * self-referential one does not compile).
 *
 * Being flat also means the braces nest without allocating per level, and the
 * evaluator walks it with a plain index range.
 */
struct OperationList {
  bool default_all = false;
  int64_t start = 0;
  int64_t end = 0;

  IndexRange range() const
  {
    return IndexRange(start, end - start);
  }
};

struct Operation {
  OpKind kind = OpKind::Add;
  bool negate = false;
  std::variant<AllPattern, Slot, GroupPattern, ComparePattern, OperationList> pattern;

  bool is_plain_add() const
  {
    return kind == OpKind::Add && !negate;
  }

  /** `P`, `*` and `!P` all start an "add" run; only `!P` adds a complement.
   * This is what decides `default_all`, so a leading `!A` starts from *nothing*
   * and yields the complement of `A` instead of degenerating to "everything". */
  bool counts_as_add() const
  {
    return kind == OpKind::Add;
  }
};

/** One parsed pattern, without the operator that precedes it. */
using PatternValue = decltype(Operation::pattern);

/**
 * A parsed selection: a flat operation list plus the starting value.
 *
 * `default_all` implements "a list that starts with an operator starts from
 * everything" (see the file comment). Evaluation applies the operations left to
 * right over one accumulated set; a sub-expression is an index range inside the
 * same list.
 */
struct Program {
  bool default_all = false;
  Vector<Operation> operations;
};

/* ------------------------------------------------------------------------- */
/* small helpers                                                             */
/* ------------------------------------------------------------------------- */

static bool is_ascii_digit(const char c)
{
  return c >= '0' && c <= '9';
}

static bool is_ascii_alpha(const char c)
{
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z');
}

/** A group name is an identifier, which is what tells it apart from an index or
 * a range. `?` and `[` may also *start* one (`?*`, `[abc]*`): they can only be
 * wildcards, never an index, so there is nothing to disambiguate. A bare `*` is
 * handled before this (it is `AllPattern`), so `*` is deliberately not a start. */
static bool is_group_name_start(const char c)
{
  return is_ascii_alpha(c) || ELEM(c, '_', '?', '[');
}

static bool is_group_name_char(const char c)
{
  return is_group_name_start(c) || is_ascii_digit(c) || ELEM(c, '*', '?', '[', ']');
}

/** Houdini group names may use `*`/`?`/`[ ]` to match several groups at once. */
static bool group_name_has_wildcard(const StringRef name)
{
  return name.find_first_of("*?[") != StringRef::not_found;
}

/** Glob matcher for group names: `*` (any run), `?` (one character) and
 * `[abc]` / `[a-z]` / `[^abc]` character classes. */
static bool glob_match(const StringRef pattern, const StringRef text)
{
  int64_t p = 0;
  int64_t t = 0;
  int64_t star_p = -1;
  int64_t star_t = 0;
  while (t < text.size()) {
    if (p < pattern.size() && pattern[p] == '*') {
      star_p = p++;
      star_t = t;
      continue;
    }
    bool matched = false;
    int64_t next_p = p;
    if (p < pattern.size() && pattern[p] == '?') {
      matched = true;
      next_p = p + 1;
    }
    else if (p < pattern.size() && pattern[p] == '[') {
      int64_t cursor = p + 1;
      bool negated = false;
      if (cursor < pattern.size() && ELEM(pattern[cursor], '^', '!')) {
        negated = true;
        cursor++;
      }
      bool in_class = false;
      bool closed = false;
      while (cursor < pattern.size()) {
        if (pattern[cursor] == ']') {
          closed = true;
          cursor++;
          break;
        }
        if (cursor + 2 < pattern.size() && pattern[cursor + 1] == '-' &&
            pattern[cursor + 2] != ']')
        {
          if (text[t] >= pattern[cursor] && text[t] <= pattern[cursor + 2]) {
            in_class = true;
          }
          cursor += 3;
          continue;
        }
        if (pattern[cursor] == text[t]) {
          in_class = true;
        }
        cursor++;
      }
      if (closed) {
        matched = in_class != negated;
        next_p = cursor;
      }
      else {
        /* An unterminated class is a literal '[', which is friendlier than
         * matching nothing at all. */
        matched = text[t] == '[';
        next_p = p + 1;
      }
    }
    else if (p < pattern.size() && pattern[p] == text[t]) {
      matched = true;
      next_p = p + 1;
    }
    if (matched) {
      p = next_p;
      t++;
      continue;
    }
    if (star_p < 0) {
      return false;
    }
    p = star_p + 1;
    t = ++star_t;
  }
  while (p < pattern.size() && pattern[p] == '*') {
    p++;
  }
  return p == pattern.size();
}

static std::string as_std_string(const StringRef text)
{
  return std::string(text);
}

static bool parse_non_negative_int(const StringRef text, int64_t &r_value)
{
  if (text.is_empty()) {
    return false;
  }
  int64_t value = 0;
  for (const char c : text) {
    if (!is_ascii_digit(c)) {
      return false;
    }
    value = value * 10 + (c - '0');
    if (value > (int64_t(1) << 40)) {
      return false;
    }
  }
  r_value = value;
  return true;
}

/** Parse an unsigned integer starting at `start`; advances `r_pos` past it. */
static bool parse_uint_at(const StringRef text,
                          const int64_t start,
                          int64_t &r_value,
                          int64_t &r_pos)
{
  int64_t pos = start;
  int64_t value = 0;
  bool any = false;
  while (pos < text.size() && is_ascii_digit(text[pos])) {
    value = value * 10 + (text[pos] - '0');
    if (value > (int64_t(1) << 40)) {
      return false;
    }
    pos++;
    any = true;
  }
  if (!any) {
    return false;
  }
  r_value = value;
  r_pos = pos;
  return true;
}

/** Parse a (possibly signed) integer starting at `start`; advances `r_pos`. */
static bool parse_int_at(const StringRef text,
                         const int64_t start,
                         int64_t &r_value,
                         int64_t &r_pos)
{
  int64_t pos = start;
  bool negative = false;
  if (pos < text.size() && ELEM(text[pos], '+', '-')) {
    negative = text[pos] == '-';
    pos++;
  }
  int64_t value = 0;
  if (!parse_uint_at(text, pos, value, pos)) {
    return false;
  }
  r_value = negative ? -value : value;
  r_pos = pos;
  return true;
}

/**
 * Parse a decimal number.
 *
 * Hand-rolled on purpose: `StringRef` has no numeric conversion, and
 * `std::from_chars` for floating point is not available everywhere this fork is
 * built (measured: `io/common/intern/string_utils.cc` falls back to `fast_float`
 * for exactly that reason). Accepts `[-+]digits[.digits][eE[-+]digits]` and
 * requires at least one digit, so `@x>` or `@x=abc` fail instead of reading 0.
 */
static bool parse_double(const StringRef text, double &r_value)
{
  int64_t pos = 0;
  bool negative = false;
  if (pos < text.size() && ELEM(text[pos], '+', '-')) {
    negative = text[pos] == '-';
    pos++;
  }
  bool any_digit = false;
  double value = 0.0;
  while (pos < text.size() && is_ascii_digit(text[pos])) {
    value = value * 10.0 + double(text[pos] - '0');
    pos++;
    any_digit = true;
  }
  if (pos < text.size() && text[pos] == '.') {
    pos++;
    double scale = 0.1;
    while (pos < text.size() && is_ascii_digit(text[pos])) {
      value += double(text[pos] - '0') * scale;
      scale *= 0.1;
      pos++;
      any_digit = true;
    }
  }
  if (!any_digit) {
    return false;
  }
  if (pos < text.size() && ELEM(text[pos], 'e', 'E')) {
    pos++;
    bool exponent_negative = false;
    if (pos < text.size() && ELEM(text[pos], '+', '-')) {
      exponent_negative = text[pos] == '-';
      pos++;
    }
    if (pos >= text.size() || !is_ascii_digit(text[pos])) {
      return false;
    }
    int64_t exponent = 0;
    while (pos < text.size() && is_ascii_digit(text[pos])) {
      exponent = std::min<int64_t>(exponent * 10 + (text[pos] - '0'), 400);
      pos++;
    }
    double factor = 1.0;
    for (int64_t i = 0; i < exponent; i++) {
      factor *= 10.0;
    }
    value = exponent_negative ? value / factor : value * factor;
  }
  if (pos != text.size()) {
    return false;
  }
  r_value = negative ? -value : value;
  return true;
}

/* ------------------------------------------------------------------------- */
/* tokenizer                                                                 */
/* ------------------------------------------------------------------------- */

struct Token {
  std::string text;
  bool quoted = false;
};

/**
 * Split on whitespace, commas and braces. Braces keep their own token so the
 * parser can recurse, and a quoted run stays part of the current token (without
 * its quotes) so that attribute values may contain spaces.
 */
static bool tokenize(const StringRef text, Vector<Token> &r_tokens, std::string &r_error)
{
  std::string current;
  bool in_quotes = false;
  bool has_quotes = false;
  int64_t open_braces = 0;

  const auto flush = [&]() {
    if (!current.empty() || has_quotes) {
      r_tokens.append(Token{current, has_quotes});
      current.clear();
      has_quotes = false;
    }
  };

  for (const char c : text) {
    if (in_quotes) {
      if (c == '"') {
        in_quotes = false;
      }
      else {
        current.push_back(c);
      }
      continue;
    }
    if (c == '"') {
      in_quotes = true;
      has_quotes = true;
      continue;
    }
    if (c == '{') {
      flush();
      open_braces++;
      r_tokens.append(Token{"{", false});
      continue;
    }
    if (c == '}') {
      flush();
      if (open_braces <= 0) {
        r_error = "unexpected '}'";
        return false;
      }
      open_braces--;
      r_tokens.append(Token{"}", false});
      continue;
    }
    if (ELEM(c, ' ', ',', '\t', '\n', '\r')) {
      flush();
      continue;
    }
    current.push_back(c);
  }
  if (in_quotes) {
    r_error = "unterminated quoted value";
    return false;
  }
  flush();
  if (open_braces > 0) {
    r_error = "unclosed '{'";
    return false;
  }
  return true;
}

/** Attach a leading operator to the following `{`, which the tokenizer split off. */
static void merge_brace_operators(const Vector<Token> &tokens, Vector<Token> &r_merged)
{
  for (int64_t i = 0; i < tokens.size(); i++) {
    const StringRef token = tokens[i].text;
    if (i + 1 < tokens.size() && tokens[i + 1].text == "{" && ELEM(token, "!", "^", "&")) {
      r_merged.append(Token{as_std_string(token) + "{", false});
      i++;
      continue;
    }
    r_merged.append(tokens[i]);
  }
}

/* ------------------------------------------------------------------------- */
/* patterns                                                                  */
/* ------------------------------------------------------------------------- */

static bool parse_numeric_pattern(const StringRef body,
                                  PatternValue &r_pattern,
                                  std::string &r_error)
{
  int64_t step = 1;
  StringRef digits = body;
  const int64_t colon = body.find_first_of(":");
  if (colon != StringRef::not_found) {
    if (!parse_non_negative_int(body.drop_prefix(colon + 1), step) || step < 1) {
      r_error = "invalid step";
      return false;
    }
    digits = body.substr(0, colon);
  }

  int64_t first = 0;
  int64_t last = 0;
  const int64_t dash = digits.find_first_of("-");
  if (dash == StringRef::not_found) {
    if (!parse_non_negative_int(digits, first)) {
      r_error = "invalid index";
      return false;
    }
    last = first;
  }
  else {
    if (!parse_non_negative_int(digits.substr(0, dash), first) ||
        !parse_non_negative_int(digits.drop_prefix(dash + 1), last))
    {
      r_error = "invalid range";
      return false;
    }
    if (last < first) {
      r_error = "range end is smaller than its start";
      return false;
    }
  }

  Slot slot;
  slot.first = first;
  slot.last = last;
  slot.step = step;
  r_pattern = slot;
  return true;
}

static bool valid_group_name_chars(const StringRef name)
{
  /* `[...]` character classes have to be balanced, otherwise `arm[` is a typo that
   * would silently become a glob matching nothing. Inside a class the glob matcher
   * supports ranges (`[a-z]`) and negation (`[^abc]` / `[!abc]`), so `-`, `^` and `!`
   * are valid there; forbidding them made both features unreachable and turned
   * `arm[0-9]` into a syntax error. Outside a class they stay invalid, so `a-b` is
   * still rejected. */
  bool in_class = false;
  for (const char c : name) {
    if (in_class) {
      if (c == ']') {
        in_class = false;
      }
      else if (c == '[') {
        return false;
      }
      else if (!is_group_name_char(c) && !ELEM(c, '-', '^', '!')) {
        return false;
      }
      continue;
    }
    if (!is_group_name_char(c)) {
      return false;
    }
    if (c == '[') {
      in_class = true;
    }
  }
  return !in_class;
}

/** Defined next to the value-list matcher below; the parser validates with it. */
static bool value_is_list(const StringRef value);

/**
 * Parse `@attr[comp]<op><value>`.
 *
 * The attribute name ends at the first component selector or comparison
 * operator, so `@position.y>0`, `@Cd[2]==1` and `@id=5-10` all work.
 */
static bool parse_attribute_pattern(const StringRef body,
                                    const bool value_is_quoted,
                                    PatternValue &r_pattern,
                                    std::string &r_error)
{
  if (body.is_empty()) {
    r_error = "expected an attribute name after '@'";
    return false;
  }

  int64_t cursor = 0;
  while (cursor < body.size()) {
    const char c = body[cursor];
    if (c == '[' || c == '.' || ELEM(c, '=', '!', '>', '<', '&')) {
      break;
    }
    if (c == ':') {
      /* Intrinsic names such as `intrinsic:indexorder` contain a colon. */
      if (cursor + 1 < body.size() && is_ascii_alpha(body[cursor + 1])) {
        cursor++;
        continue;
      }
      break;
    }
    if (!is_group_name_char(c)) {
      break;
    }
    cursor++;
  }
  const StringRef name = body.substr(0, cursor);
  if (name.is_empty()) {
    r_error = "expected an attribute name after '@'";
    return false;
  }

  int component = -1;
  if (cursor < body.size() && body[cursor] == '[') {
    const int64_t close = body.find(']', cursor);
    if (close == StringRef::not_found) {
      r_error = "unclosed '[' in the attribute component";
      return false;
    }
    int64_t value = 0;
    if (!parse_non_negative_int(body.substr(cursor + 1, close - cursor - 1), value)) {
      r_error = "invalid attribute component index";
      return false;
    }
    component = int(value);
    cursor = close + 1;
  }
  else if (cursor < body.size() && body[cursor] == '.') {
    if (cursor + 1 >= body.size()) {
      r_error = "expected a component name after '.'";
      return false;
    }
    switch (body[cursor + 1]) {
      case 'x':
      case 'r':
      case 'u':
        component = 0;
        break;
      case 'y':
      case 'g':
      case 'v':
        component = 1;
        break;
      case 'z':
      case 'b':
      case 'w':
        component = 2;
        break;
      default:
        r_error = "unknown component name, expected x/y/z, r/g/b or u/v/w";
        return false;
    }
    cursor += 2;
  }

  ComparePattern compare;
  compare.attribute = as_std_string(name);
  compare.component = component;
  compare.value_is_quoted = value_is_quoted;

  if (cursor >= body.size()) {
    r_error = "expected a comparison after the attribute name";
    return false;
  }
  const char c = body[cursor];
  bool two_chars = false;
  if (c == '=') {
    compare.op = CompareOp::Eq;
    two_chars = cursor + 1 < body.size() && body[cursor + 1] == '=';
  }
  else if (c == '!') {
    if (cursor + 1 >= body.size() || body[cursor + 1] != '=') {
      r_error = "expected '!=' after the attribute name";
      return false;
    }
    compare.op = CompareOp::Ne;
    two_chars = true;
  }
  else if (c == '>') {
    compare.op = CompareOp::Gt;
    two_chars = cursor + 1 < body.size() && body[cursor + 1] == '=';
    if (two_chars) {
      compare.op = CompareOp::Ge;
    }
  }
  else if (c == '<') {
    compare.op = CompareOp::Lt;
    two_chars = cursor + 1 < body.size() && body[cursor + 1] == '=';
    if (two_chars) {
      compare.op = CompareOp::Le;
    }
  }
  else if (c == '&') {
    compare.op = CompareOp::BitAnd;
  }
  else {
    r_error = "unknown comparison operator, expected =, ==, !=, >, <, >=, <= or &";
    return false;
  }
  cursor += two_chars ? 2 : 1;

  if (cursor >= body.size()) {
    /* `@position.y>` is a truncated pattern, not a pattern with an empty value: without
     * this it parsed fine and then quietly matched nothing. */
    r_error = "expected a value after the comparison operator";
    return false;
  }
  compare.value = as_std_string(body.substr(cursor));

  /* `=` / `!=` also take a *value list* (`@id=0-4`, `@id="0-4 78"`), which is not a
   * single number, so it cannot be validated as one. */
  const bool list_allowed = ELEM(compare.op, CompareOp::Eq, CompareOp::Ne) &&
                            value_is_list(compare.value);

  if (value_is_quoted && !list_allowed) {
    /* Quoting is how a *string* attribute value would be written, and string attributes
     * are not supported. But quoting is also the only way to write a value list that
     * contains a space, because the tokenizer splits on whitespace - so a quoted value
     * that really is a numeric list is accepted, and anything else is reported as the
     * unsupported string it probably is. Before this, `@id="0-4 78"` was rejected while
     * the unquoted form split into two operands, which made value lists unreachable. */
    r_error = "string attribute values are not supported yet (quote at '@" +
              compare.attribute + "')";
    return false;
  }

  if (!list_allowed) {
    /* Every other operator compares against one number, and a value that is not one can
     * never match: accepting it would turn a typo such as `@position.y>abc` into
     * "selects nothing" instead of a syntax error. The Python parser rejects it for the
     * same reason - see docs/manual/reference/selection_syntax.md. */
    double probe = 0.0;
    if (!parse_double(compare.value, probe)) {
      r_error = "expected a number after the comparison operator, got '" + compare.value + "'";
      return false;
    }
  }

  r_pattern = std::move(compare);
  return true;
}

/**
 * Parse one pattern body, *without* its leading operator, into a pattern.
 *
 * The caller strips the operator, so this takes the body and not the token: passing
 * the token here silently re-introduced the operator and made every `^`/`&`/`!`
 * prefixed token a syntax error (P13's one hard bug).
 */
static bool parse_pattern(const StringRef body,
                          const bool quoted,
                          PatternValue &r_pattern,
                          std::string &r_error)
{
  if (body == "*") {
    r_pattern = AllPattern{};
    return true;
  }
  if (body.is_empty()) {
    r_error = "empty pattern";
    return false;
  }
  if (body[0] == '@') {
    return parse_attribute_pattern(body.drop_prefix(1), quoted, r_pattern, r_error);
  }
  if (is_group_name_start(body[0])) {
    if (!valid_group_name_chars(body)) {
      r_error = "invalid group pattern '" + as_std_string(body) +
                "' (letters, digits, '_' and the wildcards '*', '?', '[...]')";
      return false;
    }
    GroupPattern group;
    group.name = as_std_string(body);
    r_pattern = std::move(group);
    return true;
  }
  if (is_ascii_digit(body[0])) {
    return parse_numeric_pattern(body, r_pattern, r_error);
  }
  r_error = "expected an index, range, group name, wildcard or '@attribute', got '" +
            as_std_string(body) + "'";
  return false;
}

/* ------------------------------------------------------------------------- */
/* expression parser                                                         */
/* ------------------------------------------------------------------------- */

/**
 * Parse a token list into `r_program.operations` from `r_pos`.
 *
 * Operations are appended to one flat list, so a `{ ... }` sub-expression is just
 * an index range of that list. The operation that *consumes* the sub-expression is
 * appended first and the range is filled in once its operand has been parsed: the
 * evaluator needs the consumer before the operand so it can skip the operand after
 * treating it as a unit (see `evaluate_range`).
 */
static bool parse_tokens(const Span<Token> tokens,
                         int64_t &r_pos,
                         const bool top_level,
                         Program &r_program,
                         std::string &r_error)
{
  bool saw_add = false;
  const int64_t list_start = r_program.operations.size();
  while (r_pos < tokens.size()) {
    const Token &token = tokens[r_pos];
    if (token.text == "}") {
      if (top_level) {
        r_error = "unexpected '}'";
        return false;
      }
      r_pos++;
      break;
    }

    OpKind kind = OpKind::Add;
    bool negate = false;
    StringRef body = token.text;
    bool brace_operand = false;
    if (body.startswith("!{")) {
      negate = true;
      brace_operand = true;
    }
    else if (body.startswith("^{")) {
      kind = OpKind::Subtract;
      brace_operand = true;
    }
    else if (body.startswith("&{")) {
      kind = OpKind::Intersect;
      brace_operand = true;
    }
    else if (body == "{") {
      /* A *bare* sub-expression: the tokenizer emits `{` as its own token and
       * `merge_brace_operators()` only glues an operator onto it, so without this
       * branch `{arm* ^arm2}` reached `parse_pattern()` and failed on "{". It is a
       * plain add, which is what the recursive "operand" path below expects. */
      brace_operand = true;
    }
    else if (body.startswith("^")) {
      kind = OpKind::Subtract;
      body = body.drop_prefix(1);
      if (body.is_empty()) {
        r_error = "expected a pattern after '^'";
        return false;
      }
    }
    else if (body.startswith("&")) {
      kind = OpKind::Intersect;
      body = body.drop_prefix(1);
      if (body.is_empty()) {
        r_error = "expected a pattern after '&'";
        return false;
      }
    }
    else if (body.startswith("!")) {
      negate = true;
      body = body.drop_prefix(1);
      if (body.is_empty()) {
        r_error = "expected a pattern after '!'";
        return false;
      }
    }

    /* A sub-expression is one operand for the outer accumulator: its members are
     * added, subtracted or intersected as a unit. Inside it the accumulator
     * starts from *empty* with its own "starts with an operator -> everything"
     * rule, which is what makes `{arm* ^arm3*}` mean "arm groups except arm3". */
    if (brace_operand) {
      r_pos++;
      /* The consuming operation goes into the list *before* its operand, so that
       * the evaluator can process the sub-expression and then skip past the range.
       * With the operand first, the outer loop also applied it directly: that is
       * harmless for a plain add (`{arm*}` unions the same set twice) but wrong for
       * `!{arm*}`, which unioned `arm*` and `!arm*` into "everything". The range is
       * filled in once the operand has been parsed. */
      Operation op;
      op.kind = kind;
      op.negate = negate;
      op.pattern = OperationList{};
      const int64_t operation_index = r_program.operations.size();
      if (op.counts_as_add()) {
        saw_add = true;
      }
      r_program.operations.append(std::move(op));

      const int64_t operand_start = r_program.operations.size();
      Program sub;
      if (!parse_tokens(tokens, r_pos, false, sub, r_error)) {
        return false;
      }
      for (Operation &operation : sub.operations) {
        r_program.operations.append(std::move(operation));
      }
      OperationList &expression = std::get<OperationList>(
          r_program.operations[operation_index].pattern);
      expression.default_all = sub.default_all;
      expression.start = operand_start;
      expression.end = r_program.operations.size();
      continue;
    }

    PatternValue pattern;
    if (!parse_pattern(body, token.quoted, pattern, r_error)) {
      return false;
    }
    Operation op;
    op.kind = kind;
    op.negate = negate;
    op.pattern = std::move(pattern);
    if (op.counts_as_add()) {
      saw_add = true;
    }
    r_program.operations.append(std::move(op));
    r_pos++;
  }
  (void)list_start;

  if (!top_level && r_pos > tokens.size()) {
    /* Unreachable while the tokenizer checks brace balance, but a parse that ran
     * off the end must never be mistaken for a complete one. */
    r_error = "unclosed '{'";
    return false;
  }
  /* A list that starts with `^`/`&` starts from everything: `^A` is "all but A"
   * and `&A` is "A". A list that starts with `!` does *not* (see `counts_as_add`),
   * because `!A` means "add the complement of A" and starting from everything would
   * make it select everything. This applies at every level, so `{^arm3*}` is also
   * "all but arm3". */
  r_program.default_all = !saw_add && !r_program.operations.is_empty();
  return true;
}

static std::optional<Program> parse_selection(const StringRef text, std::string &r_error)
{
  Vector<Token> tokens;
  if (!tokenize(text, tokens, r_error)) {
    return std::nullopt;
  }
  Vector<Token> merged;
  merge_brace_operators(tokens, merged);

  Program program;
  int64_t pos = 0;
  if (!parse_tokens(merged, pos, true, program, r_error)) {
    return std::nullopt;
  }
  return program;
}

/* ------------------------------------------------------------------------- */
/* evaluation                                                                */
/* ------------------------------------------------------------------------- */

/** The attribute namespace of a group: the one the picker and existing files use. */
static constexpr const char *GROUP_ATTR_PREFIX = "group:";

static std::string group_attribute_name(const StringRef group_name)
{
  std::string name = GROUP_ATTR_PREFIX;
  for (const char c : group_name) {
    name.push_back(c);
  }
  return name;
}

/** Looks a group pattern up in the geometry: how many `group:` attributes match,
 * and how many of those are boolean. */
struct GroupMatch {
  int matched = 0;
  int boolean = 0;
};

static GroupMatch find_matching_groups(const AttributeAccessor &attributes,
                                       const StringRef pattern,
                                       Vector<std::string, 8> &r_names)
{
  GroupMatch result;
  const bool wildcard = group_name_has_wildcard(pattern);
  attributes.foreach_attribute([&](const AttributeIter &iter) {
    const StringRef name = iter.name;
    if (!name.startswith(GROUP_ATTR_PREFIX)) {
      return;
    }
    const StringRef bare = name.drop_prefix(StringRef(GROUP_ATTR_PREFIX).size());
    if (wildcard ? !glob_match(pattern, bare) : bare != pattern) {
      return;
    }
    result.matched++;
    if (iter.data_type == bke::AttrType::Bool) {
      result.boolean++;
      r_names.append(as_std_string(name));
    }
  });
  return result;
}

/** Called for every native-domain index a pattern matches. */
using VisitFn = FunctionRef<void(int64_t index)>;

/** The members of every `group:<name>` attribute matching `pattern`, unioned. */
static void visit_group_members(const AttributeAccessor &attributes,
                                const AttrDomain native_domain,
                                const StringRef pattern,
                                const VisitFn visit)
{
  Vector<std::string, 8> names;
  find_matching_groups(attributes, pattern, names);
  for (const std::string &name : names) {
    const GAttributeReader reader = attributes.lookup(name);
    if (!reader || reader.varray.type() != CPPType::get<bool>()) {
      continue;
    }
    VArray<bool> membership = reader.varray.typed<bool>();
    if (reader.domain != native_domain) {
      membership = attributes.adapt_domain<bool>(membership, reader.domain, native_domain);
    }
    /* Every entry is filled, so a single-valued group covers the whole domain and
     * a multi-valued one is read index by index. */
    for (const int64_t index : IndexRange(membership.size())) {
      if (membership[index]) {
        visit(index);
      }
    }
  }
}

/** Materialized numeric attribute values, as doubles, on the attribute's domain. */
struct NumericValues {
  Array<double> values;
  int64_t size = 0;
  AttrDomain domain = AttrDomain::Point;
};

static bool materialize_numeric(const GAttributeReader &reader,
                                const int component,
                                NumericValues &r_values)
{
  const int64_t size = reader.varray.size();
  r_values.values = Array<double>(size);
  r_values.size = size;
  r_values.domain = reader.domain;
  MutableSpan<double> dst = r_values.values.as_mutable_span();
  const int channel = component < 0 ? 0 : component;

  const auto fill = [&](const auto &varray, const auto &convert) {
    for (const int64_t i : IndexRange(size)) {
      dst[i] = convert(varray[i], channel);
    }
  };

  switch (bke::cpp_type_to_attribute_type(reader.varray.type())) {
    case bke::AttrType::Float:
      fill(reader.varray.typed<float>(),
           [](const float value, const int) { return double(value); });
      return true;
    case bke::AttrType::Int32:
      fill(reader.varray.typed<int>(),
           [](const int value, const int) { return double(value); });
      return true;
    case bke::AttrType::Int8:
      fill(reader.varray.typed<int8_t>(),
           [](const int8_t value, const int) { return double(value); });
      return true;
    case bke::AttrType::Bool:
      fill(reader.varray.typed<bool>(),
           [](const bool value, const int) { return value ? 1.0 : 0.0; });
      return true;
    case bke::AttrType::Float2:
      fill(reader.varray.typed<float2>(), [](const float2 &value, const int index) {
        return double(value[math::clamp(index, 0, 1)]);
      });
      return true;
    case bke::AttrType::Float3:
      fill(reader.varray.typed<float3>(), [](const float3 &value, const int index) {
        return double(value[math::clamp(index, 0, 2)]);
      });
      return true;
    case bke::AttrType::Float4:
      fill(reader.varray.typed<float4>(), [](const float4 &value, const int index) {
        return double(value[math::clamp(index, 0, 3)]);
      });
      return true;
    default:
      /* Strings, matrices, quaternions: nothing sensible to compare with a
       * number, so the pattern matches nothing. */
      return false;
  }
}

static bool compare_double(const double left, const CompareOp op, const double right)
{
  switch (op) {
    case CompareOp::Eq:
      return left == right;
    case CompareOp::Ne:
      return left != right;
    case CompareOp::Lt:
      return left < right;
    case CompareOp::Le:
      return left <= right;
    case CompareOp::Gt:
      return left > right;
    case CompareOp::Ge:
      return left >= right;
    case CompareOp::BitAnd:
      return (int64_t(left) & int64_t(right)) != 0;
  }
  return false;
}

/** True when the value parses as an integer range/space separated list, as in
 * `0-4 78` - the form Houdini calls a value list. */
static bool value_is_list(const StringRef value)
{
  bool any = false;
  int64_t pos = 0;
  while (pos < value.size()) {
    while (pos < value.size() && ELEM(value[pos], ' ', '\t')) {
      pos++;
    }
    if (pos >= value.size()) {
      break;
    }
    int64_t first = 0;
    int64_t after_first = pos;
    if (!parse_int_at(value, pos, first, after_first)) {
      return false;
    }
    any = true;
    pos = after_first;
    if (pos < value.size() && value[pos] == '-') {
      int64_t last = 0;
      int64_t after_last = pos + 1;
      if (!parse_int_at(value, pos + 1, last, after_last)) {
        return false;
      }
      pos = after_last;
    }
  }
  return any;
}

/** A `=` / `!=` value may be a range or a space separated list of integers, as in
 * Houdini's `@id="0-4 78"`. Returns false when the value is not such a list. */
static bool value_list_matches(const StringRef value, const double actual)
{
  bool any = false;
  bool matched = false;
  int64_t pos = 0;
  while (pos < value.size()) {
    while (pos < value.size() && ELEM(value[pos], ' ', '\t')) {
      pos++;
    }
    if (pos >= value.size()) {
      break;
    }
    int64_t first = 0;
    int64_t after_first = pos;
    if (!parse_int_at(value, pos, first, after_first)) {
      return false;
    }
    any = true;
    pos = after_first;
    if (pos < value.size() && value[pos] == '-') {
      int64_t last = 0;
      int64_t after_last = pos + 1;
      if (parse_int_at(value, pos + 1, last, after_last)) {
        matched = matched || (actual >= double(first) && actual <= double(last));
        pos = after_last;
        continue;
      }
    }
    matched = matched || actual == double(first);
  }
  return any && matched;
}

/** Look up `name`, preferring the field context (which sees builtin attributes such
 * as `position`) and falling back to the component's accessor. */
static GAttributeReader lookup_attribute(const AttributeAccessor &attributes,
                                         const bke::GeometryFieldContext &context,
                                         const StringRef name)
{
  if (const std::optional<bke::AttributeAccessor> context_attributes = context.attributes()) {
    if (GAttributeReader reader = context_attributes->lookup(name)) {
      return reader;
    }
  }
  /* `position` is *derived* on instances (there is no stored attribute: the instance
   * domain only stores `instance_transform` and `.reference_index`). The field system
   * special-cases it too, so without this `@position.x>0` would report "attribute not
   * found" on an instance domain. */
  if (name == "position" && context.domain() == AttrDomain::Instance) {
    if (const bke::Instances *instances = context.instances()) {
      return GAttributeReader{
          bke::instance_position_varray(*instances), AttrDomain::Instance, nullptr};
    }
  }
  return attributes.lookup(name);
}

/**
 * Which geometry component the selection applies to. Derived from the element kind the
 * user picked (`Domain` property, see `index_domain_filter()`), never from an input: the
 * kind already says whether the indices address the mesh, the curves, the point cloud or
 * the instances (docs/manual/nodes/pdt_selection.md).
 */
enum class ComponentFilter : int8_t {
  Mesh = 0,
  Curve = 1,
  PointCloud = 2,
  Instances = 3,
};

/** True when a component of type \a type should be selected on. */
static bool component_filter_matches(const ComponentFilter filter, const GeometryComponent::Type type)
{
  switch (filter) {
    case ComponentFilter::Mesh:
      return type == GeometryComponent::Type::Mesh;
    case ComponentFilter::Curve:
      return type == GeometryComponent::Type::Curve;
    case ComponentFilter::PointCloud:
      return type == GeometryComponent::Type::PointCloud;
    case ComponentFilter::Instances:
      return type == GeometryComponent::Type::Instance;
  }
  return false;
}

/** The component types \a filter restricts the node to, for the warning pass. */
static Vector<GeometryComponent::Type> component_filter_types(const ComponentFilter filter)
{
  switch (filter) {
    case ComponentFilter::Mesh:
      return {GeometryComponent::Type::Mesh};
    case ComponentFilter::Curve:
      return {GeometryComponent::Type::Curve};
    case ComponentFilter::PointCloud:
      return {GeometryComponent::Type::PointCloud};
    case ComponentFilter::Instances:
      return {GeometryComponent::Type::Instance};
  }
  return {GeometryComponent::Type::Mesh};
}

class PDTSelectionFieldInput final : public bke::GeometryFieldInput {
 private:
  AttrDomain native_domain_ = AttrDomain::Point;
  std::shared_ptr<const Program> selection_;
  ComponentFilter filter_ = ComponentFilter::Mesh;

  /** `GroupMatch`, `group_attribute_name`, `find_matching_groups` and the
   * pattern matcher live outside this class so the warning pass can reuse them. */

  /** Called for every native-domain index the pattern matches. */
  using VisitFn = FunctionRef<void(int64_t index)>;

  static void visit_pattern(const bke::GeometryFieldContext &context,
                            const AttributeAccessor &attributes,
                            const AttrDomain native_domain,
                            const PatternValue &pattern,
                            const int64_t native_size,
                            const VisitFn visit)
  {
    if (std::holds_alternative<AllPattern>(pattern)) {
      for (const int64_t index : IndexRange(native_size)) {
        visit(index);
      }
      return;
    }
    if (const Slot *slot = std::get_if<Slot>(&pattern)) {
      for (const int64_t index : IndexRange(native_size)) {
        if (slot->contains(index)) {
          visit(index);
        }
      }
      return;
    }
    if (const GroupPattern *group = std::get_if<GroupPattern>(&pattern)) {
      visit_group_members(attributes, native_domain, group->name, visit);
      return;
    }
    const ComparePattern &compare = std::get<ComparePattern>(pattern);

    /* Element number: `@ptnum` / `@elemnum`, like Houdini. A real attribute of
     * that name wins, which is also what Houdini does. */
    if (ELEM(StringRef(compare.attribute), "ptnum", "elemnum") &&
        !lookup_attribute(attributes, context, compare.attribute))
    {
      double number = 0.0;
      if (!parse_double(compare.value, number)) {
        return;
      }
      for (const int64_t index : IndexRange(native_size)) {
        if (compare_double(double(index), compare.op, number)) {
          visit(index);
        }
      }
      return;
    }

    const GAttributeReader reader = lookup_attribute(attributes, context, compare.attribute);
    if (!reader) {
      return;
    }
    NumericValues values;
    if (reader.domain == native_domain) {
      if (!materialize_numeric(reader, compare.component, values)) {
        return;
      }
    }
    else {
      /* Another domain: interpolate it onto the native one, exactly like any
       * other boolean field does. */
      const GAttributeReader converted = attributes.lookup(compare.attribute, native_domain);
      if (!converted || !materialize_numeric(converted, compare.component, values)) {
        return;
      }
    }
    if (values.size < native_size) {
      /* A single-valued attribute that could not be expanded: nothing to visit. */
      return;
    }

    /* A value that is not a single number may still be a range/list value, as in
     * Houdini's `@id="0-4 78"` - that is valid for `=` and for `!=` (negated). */
    double number = 0.0;
    const bool numeric_value = parse_double(compare.value, number);
    const bool list_value = ELEM(compare.op, CompareOp::Eq, CompareOp::Ne) &&
                            value_is_list(compare.value);
    for (const int64_t index : IndexRange(native_size)) {
      const double actual = values.values[index];
      bool match;
      if (list_value) {
        match = ELEM(compare.op, CompareOp::Eq) ? value_list_matches(compare.value, actual) :
                                                  !value_list_matches(compare.value, actual);
      }
      else if (numeric_value) {
        match = compare_double(actual, compare.op, number);
      }
      else {
        /* A non-numeric value on a numeric attribute never matches. */
        match = false;
      }
      if (match) {
        visit(index);
      }
    }
  }

  /** Evaluate `[start, end)` of the operation list over one accumulated set. */
  Array<bool> evaluate_range(const bke::GeometryFieldContext &context,
                             const AttributeAccessor &attributes,
                             const Span<Operation> operations,
                             const IndexRange range,
                             const bool default_all,
                             const int64_t size) const
  {
    Array<bool> accumulated(size, default_all);
    /* A sub-expression's operations live further down the same flat list, and the
     * operation that consumes them comes first. After evaluating one, jump past its
     * range so the operand is not applied a second time by this loop. */
    int64_t skip_until = range.start();
    for (const int64_t i : range) {
      if (i < skip_until) {
        continue;
      }
      const Operation &operation = operations[i];
      const bool invert = operation.negate;
      const auto combine = [&](const int64_t index, const bool member) {
        switch (operation.kind) {
          case OpKind::Add:
            accumulated[index] = accumulated[index] || member;
            break;
          case OpKind::Subtract:
            accumulated[index] = accumulated[index] && !member;
            break;
          case OpKind::Intersect:
            accumulated[index] = accumulated[index] && member;
            break;
        }
      };
      const auto add_at = [&](const int64_t index) { combine(index, true); };

      if (const OperationList *sub = std::get_if<OperationList>(&operation.pattern)) {
        /* The sub-expression is one operand: evaluate it from its own start value,
         * then add / remove / intersect its members as a unit. */
        const Array<bool> matched = this->evaluate_range(
            context, attributes, operations, sub->range(), sub->default_all, size);
        for (const int64_t index : IndexRange(size)) {
          combine(index, invert ? !matched[index] : matched[index]);
        }
        skip_until = sub->end;
        continue;
      }

      if (!invert) {
        if (operation.is_plain_add() && std::holds_alternative<AllPattern>(operation.pattern)) {
          for (const int64_t index : IndexRange(size)) {
            accumulated[index] = true;
          }
          continue;
        }
        if (operation.kind == OpKind::Intersect) {
          /* `visit_pattern()` only reports the indices it *matches*, so `&` cannot
           * be done from the members alone: the elements the pattern does not match
           * have to be cleared explicitly. Doing it with `add_at` left the previous
           * accumulator value in place, which is why `A &B` used to return `A`. */
          Array<bool> matched(size, false);
          const auto mark = [&](const int64_t index) { matched[index] = true; };
          visit_pattern(context, attributes, native_domain_, operation.pattern, size, mark);
          for (const int64_t index : IndexRange(size)) {
            accumulated[index] = accumulated[index] && matched[index];
          }
          continue;
        }
        visit_pattern(context, attributes, native_domain_, operation.pattern, size, add_at);
        continue;
      }

      /* `!pattern` adds every element the pattern does *not* match: collect the
       * members once, then add the complement. Flipping the whole accumulator
       * instead would be wrong for the very ops this exists for (`&`, `^`). */
      Array<bool> matched(size, false);
      const auto mark = [&](const int64_t index) { matched[index] = true; };
      visit_pattern(context, attributes, native_domain_, operation.pattern, size, mark);
      for (const int64_t index : IndexRange(size)) {
        combine(index, !matched[index]);
      }
    }
    return accumulated;
  }

 public:
  PDTSelectionFieldInput(const AttrDomain native_domain,
                         std::shared_ptr<const Program> selection,
                         const ComponentFilter filter = ComponentFilter::Mesh)
      : bke::GeometryFieldInput(CPPType::get<bool>(), "PDT Selection"),
        native_domain_(native_domain),
        selection_(std::move(selection)),
        filter_(filter)
  {
  }

  GVArray get_varray_for_context(const bke::GeometryFieldContext &context,
                                 const IndexMask & /*mask*/) const final
  {
    const AttributeAccessor attributes = *context.attributes();
    const AttrDomain target_domain = context.domain();
    if (attributes.domain_size(target_domain) <= 0) {
      return {};
    }

    /* A geometry set can carry several components at once; the filter decides
     * which ones this node addresses. Every other component gets "false", so the
     * same expression can be aimed at the mesh or at the curves explicitly. */
    if (!component_filter_matches(filter_, context.type())) {
      return VArray<bool>::from_single(false, attributes.domain_size(target_domain));
    }

    /* The expression addresses `native_domain_`, which may differ from the domain
     * the field is evaluated on; compute there and let the attribute system
     * adapt. */
    const int64_t native_size = attributes.domain_size(native_domain_);
    if (native_size <= 0) {
      return VArray<bool>::from_single(false, attributes.domain_size(target_domain));
    }

    Array<bool> values = this->evaluate_range(context,
                                              attributes,
                                              selection_->operations,
                                              selection_->operations.index_range(),
                                              selection_->default_all,
                                              native_size);
    VArray<bool> native_selection = VArray<bool>::from_container(std::move(values));

    if (native_domain_ == target_domain) {
      return native_selection;
    }
    return attributes.adapt_domain<bool>(
        std::move(native_selection), native_domain_, target_domain);
  }

  void hash_unique(UniqueHashBytes &hash, fn::FieldHashDeep & /*deep_hash_cache*/) const final
  {
    static constexpr int8_t id = 8;
    hash.add(id);
    hash.add(int8_t(native_domain_));
    hash.add(int8_t(filter_));
    this->hash_operations(hash, selection_->operations, selection_->default_all);
  }

  static void hash_operations(UniqueHashBytes &hash,
                              const Span<Operation> operations,
                              const bool default_all)
  {
    hash.add(default_all);
    hash.add(int64_t(operations.size()));
    for (const Operation &operation : operations) {
      hash.add(int8_t(operation.kind));
      hash.add(operation.negate);
      if (const Slot *slot = std::get_if<Slot>(&operation.pattern)) {
        hash.add(int8_t(0));
        hash.add(slot->first);
        hash.add(slot->last);
        hash.add(slot->step);
      }
      else if (const GroupPattern *group = std::get_if<GroupPattern>(&operation.pattern)) {
        hash.add(int8_t(1));
        /* A length prefix keeps ["ab"] distinct from ["a", "b"]. */
        hash.add(int64_t(group->name.size()));
        for (const char c : group->name) {
          hash.data.append(static_cast<std::byte>(c));
        }
      }
      else if (const ComparePattern *compare = std::get_if<ComparePattern>(&operation.pattern)) {
        hash.add(int8_t(2));
        hash.add(int64_t(compare->attribute.size()));
        for (const char c : compare->attribute) {
          hash.data.append(static_cast<std::byte>(c));
        }
        hash.add(int8_t(compare->op));
        hash.add(int32_t(compare->component));
        hash.add(int64_t(compare->value.size()));
        for (const char c : compare->value) {
          hash.data.append(static_cast<std::byte>(c));
        }
      }
      else if (const OperationList *sub = std::get_if<OperationList>(&operation.pattern)) {
        hash.add(int8_t(3));
        /* The sub-expression's length and start are implied by the flat list, so
         * only its start value is extra identity. */
        hash.add(sub->default_all);
      }
      else {
        hash.add(int8_t(4));
      }
    }
  }

  std::optional<AttrDomain> preferred_domain(const GeometryComponent & /*component*/) const final
  {
    return native_domain_;
  }
};

/* ------------------------------------------------------------------------- */
/* node                                                                      */
/* ------------------------------------------------------------------------- */

/* Long selections do not fit into a string socket (DNA stores char[1024]), so
 * they are kept in an ID property on the node itself and the socket only holds
 * this token. Python writes the property; no DNA change and no size limit. */
static constexpr const char *LONG_SELECTION_PROP = "pdt_selection_long";
static constexpr const char *LONG_SELECTION_TOKEN = "@prop";

/**
 * The element kind the indices address ("Domain" in the UI).
 *
 * Blender's #AttrDomain cannot express this: mesh vertices, curve control points and
 * point cloud points are *all* #AttrDomain::Point, while the user has to be able to say
 * which of the three she means - the picker bakes exactly one component. Each kind
 * therefore carries the attribute domain it evaluates on **and** the geometry component
 * it addresses (see `index_domain_attr_domain()` / `index_domain_filter()`), which is
 * also why the node needs no separate "Component" input any more.
 *
 * The order is the menu order (L0 `spec/pdt_selection.md` PDS-9) and is what the stored
 * integer means: it is a node property, so the value in a `.blend` is positional.
 */
enum class IndexDomain : int8_t {
  Vertex = 0,
  Edge = 1,
  Face = 2,
  ControlPoint = 3,
  Spline = 4,
  Point = 5,
  Instance = 6,
};

static const EnumPropertyItem index_domain_items[] = {
    {int(IndexDomain::Vertex),
     "VERTEX",
     ICON_VERTEXSEL,
     N_("Vertex"),
     N_("Indices address mesh vertices")},
    {int(IndexDomain::Edge), "EDGE", ICON_EDGESEL, N_("Edge"), N_("Indices address mesh edges")},
    {int(IndexDomain::Face), "FACE", ICON_FACESEL, N_("Face"), N_("Indices address mesh faces")},
    {int(IndexDomain::ControlPoint),
     "CONTROL_POINT",
     ICON_CURVE_DATA,
     N_("Control Point"),
     N_("Indices address curve control points (one index per control point, flat across "
        "splines)")},
    {int(IndexDomain::Spline),
     "SPLINE",
     ICON_CURVE_DATA,
     N_("Spline"),
     N_("Indices address curve splines: one index per spline (the curve domain)")},
    {int(IndexDomain::Point),
     "POINT",
     ICON_POINTCLOUD_DATA,
     N_("Point"),
     N_("Indices address point cloud points")},
    {int(IndexDomain::Instance),
     "INSTANCE",
     ICON_EMPTY_AXIS,
     N_("Instance"),
     N_("Indices address top level instances: one index per instance (the instance domain)")},
    {0, nullptr, 0, nullptr, nullptr},
};

static AttrDomain index_domain_to_attr_domain(const IndexDomain domain)
{
  switch (domain) {
    case IndexDomain::Vertex:
    case IndexDomain::ControlPoint:
    case IndexDomain::Point:
      return AttrDomain::Point;
    case IndexDomain::Edge:
      return AttrDomain::Edge;
    case IndexDomain::Face:
      return AttrDomain::Face;
    case IndexDomain::Spline:
      return AttrDomain::Curve;
    case IndexDomain::Instance:
      return AttrDomain::Instance;
  }
  return AttrDomain::Point;
}

/** The geometry component the element kind addresses (the old `Component` input). */
static ComponentFilter index_domain_filter(const IndexDomain domain)
{
  switch (domain) {
    case IndexDomain::Vertex:
    case IndexDomain::Edge:
    case IndexDomain::Face:
      return ComponentFilter::Mesh;
    case IndexDomain::ControlPoint:
    case IndexDomain::Spline:
      return ComponentFilter::Curve;
    case IndexDomain::Point:
      return ComponentFilter::PointCloud;
    case IndexDomain::Instance:
      return ComponentFilter::Instances;
  }
  return ComponentFilter::Mesh;
}


/**
 * Resolve `@prop` / `@prop:<name>` to the string stored on the node.
 * Returns false (and sets \a r_error) when the token cannot be resolved.
 */
static bool resolve_long_selection(const bNode &node,
                                   const StringRef text,
                                   std::string &r_text,
                                   std::string &r_error)
{
  const StringRef body = text.substr(StringRef(LONG_SELECTION_TOKEN).size());
  std::string prop_name = LONG_SELECTION_PROP;
  if (body.startswith(":")) {
    prop_name = body.substr(1);
    if (prop_name.empty()) {
      r_error = "expected a property name after '@prop:'";
      return false;
    }
  }
  else if (!body.is_empty()) {
    r_error = "unexpected characters after '@prop'";
    return false;
  }

  if (node.prop == nullptr) {
    r_error = "the node has no stored long selection";
    return false;
  }
  IDProperty *property = IDP_GetPropertyTypeFromGroup(node.prop, prop_name.c_str(), IDP_STRING);
  if (property == nullptr) {
    r_error = std::string("stored long selection '") + prop_name + "' not found on the node";
    return false;
  }
  r_text = IDP_string_get(property);
  return true;
}

/** Collect group patterns, and attribute patterns *except* those the field
 * resolves from the geometry it is evaluated on, for the warning pass. */
static void collect_references(const Span<Operation> operations,
                               Vector<StringRef> &r_groups,
                               Vector<StringRef> &r_attributes)
{
  for (const Operation &operation : operations) {
    if (const GroupPattern *group = std::get_if<GroupPattern>(&operation.pattern)) {
      r_groups.append(group->name);
      continue;
    }
    if (const ComparePattern *compare = std::get_if<ComparePattern>(&operation.pattern)) {
      r_attributes.append(compare->attribute);
      continue;
    }
    /* Sub-expressions are inline in the same list, so there is nothing to
     * recurse into: their operations are iterated by the caller's loop. */
  }
}

/**
 * Warn about groups and attributes the incoming geometry does not provide.
 *
 * Only possible when the geometry input is connected - with the documented "only
 * wire the Selection output" style the node never sees the geometry and the field
 * resolves the references later, on geometry this code has no access to. Missing
 * references are warnings, never errors: `0-10 typo` has to keep selecting 0-10.
 *
 * Only the component types the node is aimed at are inspected (the same list the
 * field filter uses), so a node restricted to curves neither warns about mesh
 * attributes nor stays silent about curve ones.
 */
static void warn_about_references(GeoNodeExecParams &params,
                                  const GeometrySet &geometry,
                                  const Program &set,
                                  const ComponentFilter filter)
{
  Vector<StringRef> groups;
  Vector<StringRef> attribute_names;
  collect_references(set.operations, groups, attribute_names);
  if (groups.is_empty() && attribute_names.is_empty()) {
    return;
  }

  Vector<const GeometryComponent *> components;
  bool has_instances = false;
  for (const GeometryComponent::Type type : component_filter_types(filter)) {
    if (const GeometryComponent *component = geometry.get_component(type)) {
      components.append(component);
      has_instances = has_instances || (type == GeometryComponent::Type::Instance);
    }
  }
  /* Volumes: nothing to look the attribute up in, so stay quiet rather than warning
   * about every reference. */
  if (components.is_empty()) {
    return;
  }

  const auto attribute_meta = [&](const StringRef name) -> std::optional<AttributeMetaData> {
    for (const GeometryComponent *component : components) {
      const std::optional<AttributeAccessor> attributes = component->attributes();
      if (!attributes) {
        continue;
      }
      const std::optional<AttributeMetaData> meta_data = attributes->lookup_meta_data(name);
      if (meta_data) {
        return meta_data;
      }
    }
    return std::nullopt;
  };

  for (const StringRef pattern : groups) {
    int matched = 0;
    bool is_boolean = false;
    for (const GeometryComponent *component : components) {
      const std::optional<AttributeAccessor> attributes = component->attributes();
      if (!attributes) {
        continue;
      }
      Vector<std::string, 8> names;
      const GroupMatch match = find_matching_groups(*attributes, pattern, names);
      matched += match.matched;
      is_boolean = is_boolean || (match.boolean > 0);
    }
    if (matched == 0) {
      params.error_message_add(
          NodeWarningType::Warning,
          "group pattern '" + as_std_string(pattern) + "' matches no '" +
              std::string(GROUP_ATTR_PREFIX) + "' attribute");
    }
    else if (!is_boolean) {
      params.error_message_add(
          NodeWarningType::Warning,
          "group pattern '" + as_std_string(pattern) +
              "' only matches non-boolean attributes, so it selects nothing");
    }
  }

  for (const StringRef name : attribute_names) {
    if (ELEM(name, "ptnum", "elemnum")) {
      /* Element numbers exist on every geometry. */
      continue;
    }
    if (name == "position" && has_instances) {
      /* Derived on instances, and `lookup_attribute()` provides it there. */
      continue;
    }
    if (!attribute_meta(name)) {
      params.error_message_add(NodeWarningType::Warning,
                               "attribute '" + as_std_string(name) + "' not found, so '@" +
                                   as_std_string(name) + "' matches nothing");
    }
  }
}

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Geometry>("Geometry"_ustr)
      .description("Geometry the selection refers to; also passed through unchanged");
  b.add_output<decl::Geometry>("Geometry"_ustr)
      .description("Unchanged input geometry, so the node can be chained");
  b.add_input<decl::String>("Selection"_ustr)
      .default_value("*")
      .description("Houdini group pattern list, evaluated left to right: '0-10', '5', "
                   "'0-100:2', '*', a group name such as 'A' (the boolean attribute "
                   "'group:A'), a group wildcard such as 'arm*' or '{arm* ^arm3*}', "
                   "'@position.y>0' for an attribute pattern, '^' subtract, '&' intersect, "
                   "'!' complement, '@prop' for a long selection stored on the node");
  /* `Domain` is deliberately **not** a socket: it is a node property drawn by
   * `node_layout()` (the way `Delete Geometry` carries its own `Domain`). The picker
   * reads it through the same RNA enum, and a property cannot be wired at all. */
  b.add_output<decl::Bool>("Selection"_ustr)
      .structure_type(StructureType::Field)
      .description("True for the selected elements, resolved on the chosen element kind");
}

static void node_layout(ui::Layout &layout, bContext * /*C*/, PointerRNA *ptr)
{
  layout.prop(ptr, "domain", UI_ITEM_NONE, std::nullopt, ICON_NONE);
}

static void node_init(bNodeTree * /*tree*/, bNode *node)
{
  NodeGeometryPDTSelection *data = MEM_new<NodeGeometryPDTSelection>(__func__);
  data->domain = int8_t(IndexDomain::Vertex);
  node->storage = data;
}

static void node_geo_exec(GeoNodeExecParams params)
{
  const std::string raw_text = params.extract_input<std::string>("Selection"_ustr);
  const IndexDomain index_domain = IndexDomain(node_storage(params.node()).domain);
  const AttrDomain native_domain = index_domain_to_attr_domain(index_domain);
  /* The element kind already says which component is addressed, so the component filter
   * is derived instead of asked for (L0 PDS-9: one menu, no "Component" input). */
  const ComponentFilter component_filter = index_domain_filter(index_domain);
  const GeometrySet geometry = params.extract_input<GeometrySet>("Geometry"_ustr);

  /* Whitespace around the value would break the '@prop' token, so trim both ends. */
  const std::string selection_text = [&]() {
    const size_t begin = raw_text.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) {
      return std::string();
    }
    const size_t end = raw_text.find_last_not_of(" \t\r\n");
    return raw_text.substr(begin, end - begin + 1);
  }();

  std::string resolved_text = selection_text;
  std::string error;
  if (StringRef(selection_text).startswith(LONG_SELECTION_TOKEN)) {
    if (!resolve_long_selection(params.node(), StringRef(selection_text), resolved_text, error)) {
      params.error_message_add(NodeWarningType::Error, error);
      params.set_output("Geometry"_ustr, geometry);
      params.set_output("Selection"_ustr,
                        fn::Field<bool>::from_input<PDTSelectionFieldInput>(
                            native_domain, std::make_shared<const Program>(), component_filter));
      return;
    }
  }

  const std::optional<Program> parsed = parse_selection(resolved_text, error);
  if (!parsed) {
    /* Fail safe: keep the geometry unchanged and select nothing, instead of
     * letting default outputs silently delete everything downstream. */
    params.error_message_add(NodeWarningType::Error,
                             std::string("Invalid selection '") + resolved_text + "': " + error);
    params.set_output("Geometry"_ustr, geometry);
    params.set_output("Selection"_ustr,
                      fn::Field<bool>::from_input<PDTSelectionFieldInput>(
                          native_domain, std::make_shared<const Program>(), component_filter));
    return;
  }

  params.set_output("Geometry"_ustr, geometry);
  params.set_output("Selection"_ustr,
                    fn::Field<bool>::from_input<PDTSelectionFieldInput>(
                        native_domain, std::make_shared<const Program>(*parsed), component_filter));
  warn_about_references(params, geometry, *parsed, component_filter);
}

/* ------------------------------------------------------------------------- */
/* node rna                                                                  */
/* ------------------------------------------------------------------------- */

/** `Domain` as a node property, like `Delete Geometry`'s own `domain`. Python (and so
 * the picker add-on) sees the whole item list here; a menu *socket* would only expose
 * the current label. */
static void node_rna(StructRNA *srna)
{
  RNA_def_node_enum(srna,
                    "domain",
                    "Domain",
                    "Which element kind the indices address",
                    index_domain_items,
                    NOD_storage_enum_accessors(domain),
                    int(IndexDomain::Vertex));
}

static void node_register()
{
  static bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodePDTSelection"_ustr);
  ntype.ui_name = "PDT Selection";
  ntype.ui_description = "Select elements by index ranges, groups and attribute patterns";
  ntype.nclass = NODE_CLASS_GEOMETRY;
  bke::node_type_storage(ntype,
                         "NodeGeometryPDTSelection",
                         node_free_standard_storage,
                         node_copy_standard_storage);

  ntype.initfunc = node_init;
  ntype.declare = node_declare;
  ntype.draw_buttons = node_layout;
  ntype.geometry_node_execute = node_geo_exec;

  bke::node_register_type(ntype);

  node_rna(ntype.rna_ext.srna);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_pdt_selection_cc

