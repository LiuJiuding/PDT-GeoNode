/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup nodes
 *
 * `WR-31`/`WR-32`: the lexical side of the `Code` box's syntax highlight.
 *
 * This header is deliberately free of every Blender dependency (it sits next to
 * `pdt_wrangle_vm.hh` and only includes that), so the rules can be asserted in the standalone
 * engine selftest - pixels cannot be asserted, token classes can.
 *
 * The categories are the nine a live Houdini VEX field actually shows (`ParmSyntax*` in
 * `config/ColorSchemes/*.colors.json`, read back with `hou.ui.colorFromName` and confirmed on
 * screen): plain identifiers, keywords, type names, function names, references (`@P` - one
 * class, the `f`/`i`/`v` prefix included), numbers, strings (the quotes and their contents are
 * *one* class, as Houdini draws them), comments and errors. The colours are Blender's own
 * theme syntax slots, so nothing here names a colour at all.
 *
 * Nothing in here decides whether the code is legal: that is the compiler's job (`WR-2`), and
 * the only thing this takes from a compile is the position of an error (`WR-32`).
 */

#pragma once

#include "pdt_wrangle_vm.hh"

#include <algorithm>
#include <string>
#include <vector>

namespace pdt_wrangle {

/** One character's class. `WR-31`'s nine categories. */
enum class TokenClass {
  /** Plain identifiers, punctuation and whitespace. */
  Plain,
  Keyword,
  Type,
  Function,
  /** `@P`, `f@height`, `v@Cd`: one class, prefix included. */
  Reference,
  Number,
  /** The quotes and their contents together. */
  String,
  Comment,
  /** Only ever produced by `mark_error()`, never by the lexer. */
  Error,
};

/** Exactly one entry per character of the source, so a caller can walk the drawn text and this
 * array together. */
using FormatArray = std::vector<TokenClass>;

/** The type names the language declares. The VM derives types from these (`float`/`int`/`vector`
 * in its cast table, and `quat` / `matrix` since `WR-47` / `WR-53`); `bool` is the one the L0's
 * conversion rule names as well.
 *
 * The rotation type is spelled **`quat`**, not `quaternion`: the *function* that builds one from
 * four components is called `quaternion(...)` (`WR-47`), and a type name that is also a function
 * name cannot be told apart by a lexer - both words would take the same colour. The type wins the
 * name `quat`, the function keeps `quaternion`. */
inline bool is_type_name(const std::string &name)
{
  return name == "float" || name == "int" || name == "vector" || name == "bool" ||
         name == "quat" || name == "matrix";
}

/** The three statement keywords the parser knows (`if` / `else` / `for` - there is no `while`,
 * no `return` and no user functions in this language). */
inline bool is_keyword(const std::string &name)
{
  return name == "if" || name == "else" || name == "for";
}

/** The explicit-index, topology, channel and transform family: the VM keeps these in its own tables
 * (the reads, the setters, `addpoint`/`addprim`, the removals, `chf`/`chi`/`chv`/`chq`/`chm`, and
 * since `WR-43`…`WR-58` the rotation and matrix functions), not in the math library `kFunctions()`
 * returns - but the L0 calls them functions too, so they have to highlight as one. The list is the
 * L0's vocabulary, and the selftest asserts that every name in it really compiles as a call. */
inline bool is_indexed_function_name(const std::string &name)
{
  static const char *names[] = {"fpoint",   "ipoint",       "vpoint", "qpoint",  "mpoint",
                                "fprim",    "iprim",        "vprim",  "qprim",   "mprim",
                                "npoints",  "nprimitives",  "setpointattrib", "setprimattrib",
                                "addpoint", "addprim",      "removepoint", "removeprim",
                                "chf",      "chi",          "chv",    "chq",     "chm",
                                /* `WR-43`…`WR-58` */
                                "rotate_rotation", "rotate_vector", "axis_angle_rotation",
                                "euler_rotation", "quaternion", "to_euler", "to_axis_angle",
                                "axes_rotation", "invert_rotation", "align_rotation_to_vector",
                                "combine_transform", "matrix_multiply", "invert_matrix",
                                "transpose_matrix", "matrix_determinant", "separate_transform"};
  return std::any_of(std::begin(names), std::end(names), [&](const char *candidate) {
    return name == candidate;
  });
}

/** The compiler's own math library, so a name that highlights as a function is a name that
 * compiles as one - there is no second list to drift. */
inline bool is_function_name(const std::string &name)
{
  const std::vector<FunctionInfo> &table = kFunctions();
  return std::any_of(table.begin(), table.end(), [&](const FunctionInfo &info) {
    return name == info.name;
  }) || is_indexed_function_name(name);
}

inline bool is_ident_start(const char c)
{
  return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_';
}

inline bool is_ident_char(const char c)
{
  return is_ident_start(c) || (c >= '0' && c <= '9');
}

inline bool is_digit(const char c)
{
  return c >= '0' && c <= '9';
}

/** A reference prefix is a single `f`/`i`/`v`/`b` glued to the `@`: `f@height` is one reference,
 * exactly as Houdini draws it. `q@` / `m@` (`WR-47` / `WR-53`) join them. */
inline bool is_reference_prefix(const char c)
{
  return c == 'f' || c == 'i' || c == 'v' || c == 'b' || c == 'q' || c == 'm';
}

/** `WR-31`, the whole lexer: classify every character of `code`. */
inline FormatArray format_code(const std::string &code)
{
  FormatArray format(code.size(), TokenClass::Plain);
  const size_t size = code.size();
  size_t i = 0;
  while (i < size) {
    const char c = code[i];

    /* `//` to the end of the line. */
    if (c == '/' && i + 1 < size && code[i + 1] == '/') {
      while (i < size && code[i] != '\n') {
        format[i] = TokenClass::Comment;
        i++;
      }
      continue;
    }
    /* `/* ... *\/`, and an unterminated one runs to the end of the code (the compiler reports it;
     * the highlight simply keeps colouring). */
    if (c == '/' && i + 1 < size && code[i + 1] == '*') {
      format[i] = TokenClass::Comment;
      format[i + 1] = TokenClass::Comment;
      i += 2;
      while (i < size) {
        format[i] = TokenClass::Comment;
        if (code[i] == '*' && i + 1 < size && code[i + 1] == '/') {
          format[i + 1] = TokenClass::Comment;
          i += 2;
          break;
        }
        i++;
      }
      continue;
    }
    /* A string literal: the quotes belong to the class, escapes do not end it. */
    if (c == '"') {
      format[i] = TokenClass::String;
      i++;
      while (i < size) {
        const bool escaped = code[i] == '\\' && i + 1 < size;
        format[i] = TokenClass::String;
        if (escaped) {
          format[i + 1] = TokenClass::String;
          i += 2;
          continue;
        }
        if (code[i] == '"') {
          i++;
          break;
        }
        if (code[i] == '\n') {
          /* An unterminated string does not swallow the next line. */
          break;
        }
        i++;
      }
      continue;
    }
    /* A number, including `1.5` and `1e-3`. */
    if (is_digit(c) || (c == '.' && i + 1 < size && is_digit(code[i + 1]))) {
      while (i < size && (is_digit(code[i]) || code[i] == '.')) {
        format[i] = TokenClass::Number;
        i++;
      }
      if (i < size && (code[i] == 'e' || code[i] == 'E')) {
        size_t j = i + 1;
        if (j < size && (code[j] == '+' || code[j] == '-')) {
          j++;
        }
        if (j < size && is_digit(code[j])) {
          while (i < size && (is_digit(code[i]) || code[i] == 'e' || code[i] == 'E' ||
                              code[i] == '+' || code[i] == '-'))
          {
            format[i] = TokenClass::Number;
            i++;
          }
        }
      }
      continue;
    }
    /* A reference: `@name`, and the prefix letter in front of it belongs to the same class. */
    if (c == '@') {
      size_t start = i;
      if (i > 0 && is_reference_prefix(code[i - 1]) && format[i - 1] == TokenClass::Plain &&
          (i == 1 || !is_ident_char(code[i - 2])))
      {
        start = i - 1;
      }
      size_t end = i + 1;
      while (end < size && (is_ident_char(code[end]) || code[end] == '.')) {
        end++;
      }
      for (size_t k = start; k < end; k++) {
        format[k] = TokenClass::Reference;
      }
      i = end;
      continue;
    }
    /* An identifier: keyword, type, built-in function or plain. */
    if (is_ident_start(c)) {
      size_t end = i;
      while (end < size && is_ident_char(code[end])) {
        end++;
      }
      const std::string name = code.substr(i, end - i);
      const TokenClass klass = is_keyword(name)    ? TokenClass::Keyword :
                               is_type_name(name)  ? TokenClass::Type :
                               is_function_name(name) ? TokenClass::Function :
                                                        TokenClass::Plain;
      for (size_t k = i; k < end; k++) {
        format[k] = klass;
      }
      i = end;
      continue;
    }
    i++;
  }
  return format;
}

/** `WR-32`: the one thing the highlight takes from a compile - where the error is. Marks the
 * token that starts at `line`/`column` (both 1-based, as the compiler reports them), or a single
 * character when no token starts there. */
inline void mark_error(FormatArray &format,
                       const std::string &code,
                       const int line,
                       const int column)
{
  size_t offset = 0;
  int current_line = 1;
  while (offset < code.size() && current_line < line) {
    if (code[offset] == '\n') {
      current_line++;
    }
    offset++;
  }
  if (offset < code.size() && line > 1) {
    /* the loop stopped on the newline of the previous line */
  }
  offset += size_t(std::max(column - 1, 0));
  if (offset >= code.size()) {
    return;
  }
  size_t end = offset + 1;
  if (is_ident_char(code[offset])) {
    while (end < code.size() && (is_ident_char(code[end]) || code[end] == '@')) {
      end++;
    }
  }
  for (size_t k = offset; k < end && k < format.size(); k++) {
    format[k] = TokenClass::Error;
  }
}

}  // namespace pdt_wrangle
