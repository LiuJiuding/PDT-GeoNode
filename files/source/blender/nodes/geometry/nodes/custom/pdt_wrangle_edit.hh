/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup nodes
 *
 * `WR-33`/`WR-34`: the two shortcuts of the `Code` box, as pure text transformations.
 *
 * Like the lexer next door this header has no Blender dependency, so the rules can be asserted in
 * the standalone engine selftest - a key press cannot be asserted, the text it produces can. The
 * caller (the text button's key handler) is responsible for turning the returned state into an
 * actual edit with the usual undo and selection bookkeeping.
 *
 * Both shortcuts are lexical and neither judges the code: `WR-33` uses the lexer only to be sure
 * that a `//` really is a comment and not the inside of a string, and `WR-34` counts spaces.
 */

#pragma once

#include "pdt_wrangle_highlight.hh"

#include <algorithm>
#include <string>
#include <utility>
#include <vector>

namespace pdt_wrangle {

/** The four spaces `WR-34` inserts. */
inline constexpr int kIndentSpaces = 4;

/** A piece of code plus the cursor and selection an editor keeps. `sel_start == sel_end` means "no
 * selection, just a cursor"; offsets are clamped on use and may be given in either order. */
struct CodeEditState {
  std::string code;
  int cursor = 0;
  int sel_start = 0;
  int sel_end = 0;
};

inline int code_clamp(const int value, const std::string &code)
{
  return std::clamp(value, 0, int(code.size()));
}

/** The start offset of the line containing `offset`. */
inline int line_start_of(const std::string &code, const int offset)
{
  const int at = code_clamp(offset, code);
  const size_t found = code.rfind('\n', size_t(at > 0 ? at - 1 : 0));
  if (at == 0) {
    return 0;
  }
  if (found == std::string::npos) {
    return 0;
  }
  /* `rfind` searched only up to `at - 1`, so a newline at that position belongs to this line. */
  return int(found) + 1;
}

/** The offset just past the newline that ends the line containing `offset` (or the end of the
 * code). */
inline int line_end_of(const std::string &code, const int offset)
{
  const int at = code_clamp(offset, code);
  const size_t found = code.find('\n', size_t(at));
  return (found == std::string::npos) ? int(code.size()) : int(found) + 1;
}

/** The lines `WR-33`/`WR-34` act on: every line the selection touches, or - when there is no
 * selection - the **cursor's** line, which is what the L0 says.
 *
 * "No selection" is `sel_start == sel_end`, and the framework legitimately keeps the caret
 * somewhere else in that state: `textedit_insert_buf()` (what plain typing and `Tab` go through)
 * moves `pos` without touching `selsta`/`selend`. Using `sel_start` as the caret's line would then
 * act on a stale line, so the cursor decides. A selection that ends exactly at a line start does
 * not include that line (it is not touched at all), which is what "covered" means here. */
inline std::vector<std::pair<int, int>> target_lines(const std::string &code,
                                                     const int sel_start,
                                                     const int sel_end,
                                                     const int cursor)
{
  const bool has_selection = (sel_start != sel_end);
  const int from = has_selection ? std::min(sel_start, sel_end) : cursor;
  const int to = has_selection ? std::max(sel_start, sel_end) : cursor;
  const int a = code_clamp(from, code);
  const int b = code_clamp(to, code);
  std::vector<std::pair<int, int>> lines;
  if (a == b) {
    lines.emplace_back(line_start_of(code, a), line_end_of(code, a));
    return lines;
  }
  int line_start = line_start_of(code, a);
  /* The last character the selection covers decides the last line; a selection ending on a newline
   * therefore stops on the line before it. */
  const int last = std::max(b - 1, a);
  while (line_start <= last) {
    const int line_end = line_end_of(code, line_start);
    lines.emplace_back(line_start, line_end);
    if (line_end >= int(code.size())) {
      break;
    }
    line_start = line_end;
  }
  return lines;
}

/** The offset of the first non-space character of a line, i.e. where `//` belongs. */
inline int indent_end_of(const std::string &code, const int line_start)
{
  int at = line_start;
  while (at < int(code.size()) && (code[size_t(at)] == ' ' || code[size_t(at)] == '\t')) {
    at++;
  }
  return at;
}

/** The number of leading *space* characters of a line. A tab is deliberately not one: `WR-34` says
 * Shift+Tab leaves a line that starts with a tab alone. */
inline int leading_spaces_of(const std::string &code, const int line_start)
{
  int count = 0;
  while (line_start + count < int(code.size()) && code[size_t(line_start + count)] == ' ') {
    count++;
  }
  return count;
}

/** `WR-41`: the logical line a byte offset sits in, 1-based (`\n` starts a new one). This is what
 * the gutter highlights, and it counts *logical* lines - a wrapped row belongs to the line it was
 * wrapped from. */
inline int line_number_at(const std::string &code, const int offset)
{
  const int at = code_clamp(offset, code);
  int line = 1;
  for (int i = 0; i < at; i++) {
    if (code[size_t(i)] == '\n') {
      line++;
    }
  }
  return line;
}

/** `WR-41`: how many digits the gutter has to fit - the digits of the logical line count. It also
 * sizes the number field, so the column widens once the code grows past line 9 (then 99), which is
 * what the Text Editor does (`integer_digits_i(nlines)`). */
inline int line_number_digits(const std::string &code)
{
  const int lines = std::max(1, line_number_at(code, int(code.size())));
  /* `std::to_string` keeps this header free of `<cmath>`. */
  return int(std::to_string(lines).size());
}

/** `WR-41`: the number each visible **row** gets, in row order: the logical line number for a row
 * that starts one, and `0` for a wrapped continuation (its gutter stays blank). `row_offsets` are
 * the byte offsets the text wrap started the rows at, ascending; out-of-range values are clamped.
 *
 * The rule lives here, without Blender, so it can be asserted - only the drawing, the wrap width
 * and the mouse mapping need a screen. */
inline std::vector<int> line_number_rows(const std::string &code,
                                         const std::vector<int> &row_offsets)
{
  std::vector<int> numbers;
  numbers.reserve(row_offsets.size());
  int logical = 1;
  int previous = 0;
  for (size_t i = 0; i < row_offsets.size(); i++) {
    const int at = code_clamp(row_offsets[i], code);
    if (i > 0) {
      /* Every newline the rows so far consumed moves us to the next logical line. */
      for (int scan = previous; scan < at; scan++) {
        if (code[size_t(scan)] == '\n') {
          logical++;
        }
      }
    }
    /* A row starts a logical line when the byte before it is the newline that ended the previous
     * one - the first row always does, and so does the empty row a trailing newline leaves. */
    const bool starts_a_line = (i == 0) || (at > 0 && code[size_t(at - 1)] == '\n');
    numbers.push_back(starts_a_line ? logical : 0);
    previous = at;
  }
  return numbers;
}

/** The editor state a transform leaves behind.
 *
 * Both transforms change the length of the text *before* the selection, so the selection has to be
 * carried over explicitly: `first_start` / `last_end` are the offsets of the first and last target
 * line **in the new code** (recorded while writing it). Re-running the line search on the new code
 * with the *old* offsets - what this header did first - is only correct for edits that never shift
 * the text in front of the selection, and an outdent shifts it by up to four characters per line:
 * the range then reached one further line down on every press, which is the bug the user reported
 * from the GUI ("each Shift+Tab selects one more line"). Keeping the same lines covered also makes
 * a repeated press idempotent, which is what a text editor is expected to do.
 *
 * With no selection the caret is simply mapped (`sel_start == sel_end` stays true) and clamped into
 * the new code. */
inline void finish_edit(CodeEditState &result,
                        const bool had_selection,
                        const int mapped_cursor,
                        const int first_start,
                        const int last_end)
{
  const int cursor = code_clamp(mapped_cursor, result.code);
  if (had_selection) {
    result.sel_start = code_clamp(first_start, result.code);
    result.sel_end = code_clamp(last_end, result.code);
    result.cursor = std::clamp(cursor, result.sel_start, result.sel_end);
    return;
  }
  result.sel_start = cursor;
  result.sel_end = cursor;
  result.cursor = cursor;
}

/** `WR-33`: what `Ctrl+/` does. Adds `// ` after the indentation of every target line, or removes
 * the `//` and one following space from every one of them - all or nothing, never half and half. */
inline CodeEditState toggle_comment(const CodeEditState &state)
{
  CodeEditState result = state;
  const bool had_selection = (state.sel_start != state.sel_end);
  const std::vector<std::pair<int, int>> lines = target_lines(
      state.code, state.sel_start, state.sel_end, state.cursor);
  if (lines.empty()) {
    return result;
  }
  /* A line counts as commented when its first non-space characters are `//` *and* the lexer agrees
   * that those two characters are a comment - so `//` inside a string never counts. */
  const FormatArray format = format_code(state.code);
  bool all_commented = true;
  for (const auto &line : lines) {
    const int indent = indent_end_of(state.code, line.first);
    const bool has_prefix = indent + 1 < line.second && state.code[size_t(indent)] == '/' &&
                            state.code[size_t(indent) + 1] == '/';
    const bool is_comment = has_prefix && size_t(indent) < format.size() &&
                            format[size_t(indent)] == TokenClass::Comment;
    if (!is_comment) {
      all_commented = false;
      break;
    }
  }

  std::string out;
  out.reserve(state.code.size() + lines.size() * 4);
  int line_start = 0;
  int cursor = state.cursor;
  int first_start = 0;
  int last_end = 0;
  bool first = true;
  for (const auto &line : lines) {
    /* Copy whatever comes before this line untouched: the newline(s) that separate the target
     * lines and this line's own indentation. */
    const int indent = indent_end_of(state.code, line.first);
    const bool has_prefix = indent + 1 < line.second && state.code[size_t(indent)] == '/' &&
                            state.code[size_t(indent) + 1] == '/';
    const bool strip = all_commented && has_prefix;
    const int strip_len = strip ? ((indent + 2 < line.second && state.code[size_t(indent) + 2] == ' ')
                                       ? 3
                                       : 2)
                                : 0;
    out.append(state.code, size_t(line_start), size_t(indent - line_start));
    /* The line's own start (column 0, indentation included) is what the selection keeps. */
    const int new_line_start = int(out.size()) - (indent - line.first);
    if (strip) {
      /* Drop `//` and one following space when there is one. */
      out.append(state.code, size_t(indent + strip_len), size_t(line.second - indent - strip_len));
    }
    else {
      out += "// ";
      out.append(state.code, size_t(indent), size_t(line.second - indent));
    }
    if (first) {
      first_start = new_line_start;
      first = false;
    }
    last_end = int(out.size());
    if (cursor >= line.second) {
      cursor += strip ? -strip_len : 3;
    }
    else if (cursor >= indent) {
      cursor = strip ? std::max(indent, cursor - strip_len) : cursor + 3;
    }
    line_start = line.second;
  }
  out.append(state.code, size_t(line_start), state.code.size() - size_t(line_start));
  result.code = std::move(out);
  finish_edit(result, had_selection, cursor, first_start, last_end);
  return result;
}

/** `WR-34`: what `Tab` and `Shift+Tab` do. Four spaces at the head of every target line, or up to
 * four leading spaces removed - never a tab character, and never crossing a non-space. */
inline CodeEditState indent_lines(const CodeEditState &state, const bool add)
{
  CodeEditState result = state;
  const bool had_selection = (state.sel_start != state.sel_end);
  const std::vector<std::pair<int, int>> lines = target_lines(
      state.code, state.sel_start, state.sel_end, state.cursor);
  if (lines.empty()) {
    return result;
  }
  if (!had_selection && add) {
    /* No selection: the four spaces go in at the cursor, which is the plain "press Tab" case. */
    std::string out = state.code;
    const int at = code_clamp(state.cursor, state.code);
    out.insert(size_t(at), std::string(kIndentSpaces, ' '));
    result.code = std::move(out);
    result.cursor = at + kIndentSpaces;
    result.sel_start = result.cursor;
    result.sel_end = result.cursor;
    return result;
  }

  std::string out;
  out.reserve(state.code.size() + lines.size() * size_t(kIndentSpaces));
  int line_start = 0;
  int cursor = state.cursor;
  int first_start = 0;
  int last_end = 0;
  bool first = true;
  for (const auto &line : lines) {
    /* Everything before this line's first character, untouched: that is the newline(s) separating
     * the target lines plus this line's own leading spaces. */
    out.append(state.code, size_t(line_start), size_t(line.first - line_start));
    /* The line's own start (column 0) is what the selection keeps. */
    const int new_line_start = int(out.size());
    int removed = 0;
    if (add) {
      /* Adding: every target line gets the spaces at its head, an empty line included (the L0 says
       * every covered line). */
      out.append(std::string(kIndentSpaces, ' '));
      out.append(state.code, size_t(line.first), size_t(line.second - line.first));
      if (cursor >= line.first) {
        cursor += kIndentSpaces;
      }
    }
    else {
      /* Removing: up to four leading *spaces*. A line whose first character is a tab (or anything
       * else) is left alone, which is what `WR-34` asks for. */
      removed = std::min(leading_spaces_of(state.code, line.first), kIndentSpaces);
      out.append(state.code, size_t(line.first + removed), size_t(line.second - line.first - removed));
      if (cursor >= line.first + removed) {
        cursor -= removed;
      }
      else if (cursor > line.first) {
        cursor = line.first;
      }
    }
    if (first) {
      first_start = new_line_start;
      first = false;
    }
    last_end = int(out.size());
    line_start = line.second;
  }
  out.append(state.code, size_t(line_start), state.code.size() - size_t(line_start));
  result.code = std::move(out);
  finish_edit(result, had_selection, cursor, first_start, last_end);
  return result;
}

}  // namespace pdt_wrangle
