/* SPDX-FileCopyrightText: 2026 PDT Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo_nodes
 *
 * PDT Wrangle execution engine (M1): a VEX-like language compiled to a register
 * bytecode and interpreted per element.
 *
 * Deliberately **self-contained** (no Blender headers) so it can be built and exercised
 * by a standalone harness in seconds (`tools/pdt/tests/wrangle_engine_selftest.cpp`) instead
 * of minutes; that is where the semantics get debugged. `node_geo_pdt_wrangle.cc` is only
 * the host: it binds the program's attributes to Blender attribute spans and runs the
 * program under `threading::parallel_for`.
 *
 * Numeric model: **every scalar register is a float32** (what Blender attributes store).
 * `int` is a *static* type: storing into an int target truncates, and int attributes are
 * converted at the binding boundary. Integers up to 2^24 are therefore exact, which
 * covers element indices and ids; this is the deliberate M1 trade-off that keeps the VM
 * to one scalar register file.
 *
 * M1 language surface (pinned by `tools/pdt/tests/test_pdt_wrangle.py`):
 *   * types `float` / `int` / `vector`; attributes `@name`, `f@`, `i@`, `v@`
 *   * read-only `@elemnum` and `@numelem` (the Houdini `@ptnum` / `@primnum` are gone, WR-9)
 *   * statements: declaration, assignment (`= += -= *= /= %=`), `if`/`else`, `for`, blocks
 *   * operators `+ - * / %`, unary `-`/`!`, `< <= > >= == !=`, `&& ||`
 *   * componentwise vector arithmetic, `.x/.y/.z` / `.r/.g/.b`, `[0] [1] [2]`, `{a,b,c}`
 *   * component assignment (`@P.y += 1;`, `v[0] = 2;`)
 *   * the math library in `function_table()` / `kFunctions()`
 *   * NOT in M1: user functions, strings, arrays, `while`, `break`/`continue`, `printf`,
 *     topology changes, neighbour queries
 */

#pragma once

#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <unordered_map>
#include <unordered_set>
#include <vector>

namespace pdt_wrangle {

/* ------------------------------------------------------------------ */
/** \name Values and diagnostics                                       */
/* ------------------------------------------------------------------ */

struct Vec3 {
  float x = 0.0f, y = 0.0f, z = 0.0f;
};

/* `WR-47`: `Quaternion` is Blender's `QUATERNION` attribute type (`math::Quaternion`, so the
 * components are w, x, y, z). `WR-53`: `Matrix` is its `FLOAT4X4` attribute type - four columns
 * of four floats, exactly like `float4x4`.
 *
 * The C++ enumerator stays `Quaternion`; the **source-level** type name is `quat`, because the
 * function that builds one from four components is called `quaternion(...)` and a type name that
 * is also a function name cannot be told apart by the highlighter's lexer. */
enum class Type : uint8_t { Float, Int, Vector, Quaternion, Matrix };

/** How many scalar registers a value of this type occupies. */
inline int type_components(const Type type)
{
  switch (type) {
    case Type::Float:
    case Type::Int:
      return 1;
    case Type::Vector:
      return 3;
    case Type::Quaternion:
      return 4;
    case Type::Matrix:
      return 16;
  }
  return 1;
}

inline const char *type_name(const Type type)
{
  switch (type) {
    case Type::Float:
      return "float";
    case Type::Int:
      return "int";
    case Type::Vector:
      return "vector";
    case Type::Quaternion:
      return "quat";
    case Type::Matrix:
      return "matrix";
  }
  return "?";
}

inline bool is_scalar(const Type type)
{
  return type == Type::Float || type == Type::Int;
}

/** The rotation and matrix types are not numbers: arithmetic on them is a compile error. */
inline bool is_transform(const Type type)
{
  return type == Type::Quaternion || type == Type::Matrix;
}

struct Error {
  int line = 0;
  int column = 0;
  std::string message;

  std::string format() const
  {
    return std::to_string(line) + ":" + std::to_string(column) + ": " + message;
  }
};

/* ------------------------------------------------------------------ */
/** \name Lexer                                                         */
/* ------------------------------------------------------------------ */

enum class TokKind : uint8_t { End, Number, Ident, Attr, Punct, String };

struct Token {
  TokKind kind = TokKind::End;
  /** Ident: the name. Punct: the operator text. Attr: the attribute name. */
  std::string text;
  /** Attr: the VEX type prefix ('f'/'i'/'v'), or 0 when the source had none. */
  char prefix = 0;
  double number = 0.0;
  int line = 1;
  int column = 1;
};

/* ------------------------------------------------------------------ */
/** \name Bytecode                                                      */
/* ------------------------------------------------------------------ */

enum class Fn1 : uint8_t {
  Sin, Cos, Tan, Asin, Acos, Atan, Sqrt, Exp, Log, Log10,
  Abs, Floor, Ceil, Round, Trunc, Fract, Sign, Radians, Degrees,
};
enum class Fn2 : uint8_t { Atan2, Pow, Min, Max, Step };
enum class Fn3 : uint8_t { Lerp, Clamp, Smoothstep };

enum class Op : uint8_t {
  Halt = 0,
  /* scalars (one float register file) */
  LoadF, CopyF, AddF, SubF, MulF, DivF, ModF, NegF, TruncF,
  /* vectors */
  CopyV, MakeV, AddV, SubV, MulV, DivV, NegV,
  /* vector <-> scalar, componentwise */
  AddVF, SubVF, MulVF, DivVF, SubFV, DivFV,
  /* calls */
  CallF1, CallF2, CallF3, MapV1, MapV2, MapV2S,
  /* vector reductions and products */
  LenV, LenSqV, DistV, DotV, CrossV, NormalizeV,
  /* comparisons and logic (0.0 / 1.0 in a scalar register) */
  LtF, LeF, GtF, GeF, EqF, NeF, AndI, OrI, NotI,
  /* control flow */
  Jmp, JmpIfZero,
  /* components */
  VGet, VSet,
  /* deterministic helpers */
  RandF, NoiseF, NoiseV,
  /* `WR-31`: the explicit-index reads (`fpoint` / `vprim` / ...). `imm` is the binding slot and
   * `a` the register holding the element index - the index is a run-time value, which is why
   * the host cannot pre-load this one into a register like the `@` forms. */
  LoadAttrIndex,
  /* `WR-33`/`WR-34`: the buffered cross-element writes (`setpointattrib` / `setprimattrib`).
   * `imm` = the binding slot, `a` = the index register, `b` = the value register (the binding's
   * type says which file that is). The VM never touches the attribute: the host merges the
   * buffers in element order afterwards, which is what makes a parallel run deterministic. */
  StoreAttrIndex,
  /* `WR-34` promise 1: `addpoint`. The host gives each element its `new_point_base` and this
   * op hands out consecutive numbers from it, so two calls in one element are adjacent - and the
   * host's numbering pass is what makes the sequence start right after the input's points. */
  AddPoint,
  /* `WR-36`/`WR-37`: `addprim(0, "poly"|"polyline", p0, …)`, `removepoint(0, i)`,
   * `removeprim(0, i)`. `imm` is the binding slot; AddPrim reads `b` point numbers starting at
   * register `a` and hands out a number like AddPoint. */
  AddPrim, RemovePoint, RemovePrim,
  /* `WR-43`…`WR-58`: the rotation and matrix functions. Four separate register files carry the
   * values (`s`, `v`, `q`, `m`) so that every one of these is a *single* instruction - the
   * compiler owns the component layout, the VM never unpacks anything. `dst`/`a`/`b` name the
   * register themselves (a matrix register stands for its 16 floats, like a vector register
   * stands for its 3). */
  CopyQ, QuatMultiply, QuatRotateV, QuatFromAxisAngle, QuatFromEuler, QuatToEuler, QuatToAxisAngle,
  QuatInvert, QuatAlignVector, QuatAxes, QuatFromComponents, QuatIdentity,
  CopyM, MatrixMultiply, CombineTransform, InvertMatrix, TransposeMatrix, MatrixDeterminant,
  SeparateTransform, MatrixIdentity, MatrixColumn, MatrixTransformV,
  /* `WR-47`/`WR-53`: one scalar of a rotation (0…3) or matrix (0…15), read (`TGet`) or written
   * (`TSet`); `imm` packs the file bit and the element. */
  TGet, TSet,
};

struct Instr {
  Op op = Op::Halt;
  /** Register indices. `d` is only read by the few instructions that need a fifth one - the
   * rotation/matrix constructors (`WR-47`, `WR-58`), which take four parts or write three out. */
  uint8_t dst = 0, a = 0, b = 0, c = 0, d = 0;
  int32_t imm = 0;
  float fimm = 0.0f;
};

/**
 * `WR-31`: which table an explicit-index read looks in. The `@` forms use `RunOver` (the host
 * derives the domain from `Run Over`); the function name fixes the other two, and that is what
 * makes these functions the way to reach geometry from `Detail`, where there is no run-over
 * domain at all.
 */
enum class ReadDomain : uint8_t { RunOver, Point, Prim };

/**
 * `WR-18`/`WR-19`: which component an explicit-index read addresses. The `domain` argument names it
 * by word, so a read reaches geometry this pass is not running over - `fpoint(0, "curve", "P", 3)`
 * from a `Vertex` run reads the curve component of the same input.
 */
enum class Component : uint8_t { Pass, Mesh, Curve, PointCloud, Instance };

/** `WR-18`/`WR-19`: the words `domain` accepts, per table class. */
inline bool component_word(const ReadDomain domain,
                           const std::string &word,
                           Component &out)
{
  /* `WR-18`: the point tables are `mesh`, `curve`, `pointcloud` and `instance`; `WR-19`: the prim
   * tables (a mesh's faces, a curve's splines) are only `mesh` and `curve` - a point cloud and an
   * instance set have none. */
  if (word == "mesh") {
    out = Component::Mesh;
    return true;
  }
  if (word == "curve") {
    out = Component::Curve;
    return true;
  }
  if (domain == ReadDomain::Point && word == "pointcloud") {
    out = Component::PointCloud;
    return true;
  }
  if (domain == ReadDomain::Point && word == "instance") {
    out = Component::Instance;
    return true;
  }
  return false;
}

inline const char *component_word_hint(const ReadDomain domain)
{
  return domain == ReadDomain::Prim ? "\"mesh\" or \"curve\"" :
                                      "\"mesh\", \"curve\", \"pointcloud\" or \"instance\"";
}

inline const char *component_name(const Component component)
{
  switch (component) {
    case Component::Mesh:
      return "mesh";
    case Component::Curve:
      return "curve";
    case Component::PointCloud:
      return "pointcloud";
    case Component::Instance:
      return "instance";
    case Component::Pass:
      break;
  }
  return "pass";
}

/** One attribute the program touches; the host binds it to a Blender attribute. */
struct AttrBinding {
  /** Name as written, without the type prefix and without an `opinput<n>_` prefix. */
  std::string name;
  Type type = Type::Float;
  /** Register index: into the scalar file for float/int, the vector file for vector. */
  int reg = 0;
  bool read = false;
  bool write = false;
  /** The host fills these instead of reading them (`elemnum`, `numelem`). */
  bool host_supplied = false;
  /** 0 = the geometry being written; 1..3 = `@opinput<n>_<name>` (read-only, `WR-29`/`WR-30`). */
  int input = 0;
  /** `WR-31`: the table the explicit-index functions read (`RunOver` = the `@` forms). */
  ReadDomain domain = ReadDomain::RunOver;
  /** `WR-18`/`WR-19`: the component the `domain` word named (`Pass` = the one being processed). */
  Component component = Component::Pass;
  /** `WR-31`: `npoints` / `nprimitives` - the host fills one number, no per-element read. */
  bool count = false;
  /** `WR-36`: `chf` / `chi` / `chv` - a *channel* of the node itself, which the host reads from the
   * node's own sockets once per evaluation. Read-only, and never an attribute. */
  bool channel = false;
  /** `WR-33`: `setpointattrib` / `setprimattrib` - a buffered write at a run-time index. The
   * host merges these in element order (`WR-34`), so they are not the per-element `@` writes. */
  bool indexed_write = false;
  /** `WR-36`/`WR-37`/`WR-25`: the topology functions and what the host needs to know about them. */
  enum class Topology : uint8_t { None, AddPrim, RemovePoint, RemovePrim, AddPoint };
  Topology topology = Topology::None;
  /** `WR-36`: `"polyline"` (a curve spline) rather than `"poly"` (a mesh face). */
  bool polyline = false;
  /** `WR-25`: `addpoint(0, "pointcloud", pos)` grows the point cloud instead of the mesh. */
  bool addpoint_cloud = false;
  /** `WR-27`: `addprim`'s `pointdomain` - where the point numbers come from (`Pass` when the
   * function has no such argument). */
  Component point_component = Component::Pass;
  /** `WR-36`: how many point numbers `addprim` was given - the host checks it against the type,
   * because `WR-36` makes a too-short primitive a *node* error, not a compile error. */
  int argc = 0;
};

/** `WR-36`: one `addprim` an element asked for: which binding, the number handed out, and the
 * point numbers itself. The values have to travel with the request - after the run the registers
 * are gone, and the host needs them to build the face. */
struct TopologyEntry {
  int slot = 0;
  int number = 0;
  std::vector<int> points;
};

/** `WR-34`: one buffered cross-element write, harvested from a `Frame` after its element ran. */
struct WriteEntry {
  /** Index into `Program::attrs`. */
  int slot = 0;
  /** The element the code asked for (a point or prim number). */
  int index = 0;
  float scalar = 0.0f;
  Vec3 value;
  /** `WR-47`/`WR-53`: the rotation and matrix payloads of the same write. Only the field the
   * binding's type names is filled; the rest stay zero. */
  float quat[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  float matrix[16] = {0.0f};
};

/**
 * `WR-31`: how the VM fetches one explicit-index slot at run time. The host keeps its reader in
 * `ctx` and supplies the accessor for the type the code asked for - that keeps this header free
 * of Blender types while still reading straight out of a `VArray` (no copy). `size <= 0` or a
 * null accessor means "this reads 0" (`WR-32`); a float2 source reports `z = 0`.
 */
struct ReadSource {
  const void *ctx = nullptr;
  float (*read_f)(const void *ctx, int index) = nullptr;
  int (*read_i)(const void *ctx, int index) = nullptr;
  void (*read_v)(const void *ctx, int index, float *out_xyz) = nullptr;
  /** `WR-47`: four floats, w first (`math::Quaternion`). */
  void (*read_q)(const void *ctx, int index, float *out_wxyz) = nullptr;
  /** `WR-53`: sixteen floats, four columns (`float4x4`). */
  void (*read_m)(const void *ctx, int index, float *out_16) = nullptr;
  int size = 0;
};

struct Program {
  std::vector<Instr> code;
  std::vector<AttrBinding> attrs;
  int n_scalar = 0;
  int n_vector = 0;
  /** `WR-43`…`WR-58`: one register each, holding four (quaternion) or sixteen (matrix) floats. */
  int n_quat = 0;
  int n_matrix = 0;
  /** `WR-34`: true when the program can create elements. The host then has to count first and
   * hand every element its numbering base, instead of running the code once. */
  bool creates_topology = false;
  std::vector<Error> errors;

  bool ok() const
  {
    return errors.empty();
  }
  std::string error_report() const;
  std::string disassemble() const;
};

/** Compiles `code`. On failure `errors` is non-empty and `code` is empty. */
Program compile(std::string_view code, bool detail = false);

/* ------------------------------------------------------------------ */
/** \name Execution                                                     */
/* ------------------------------------------------------------------ */

enum class RunStatus : uint8_t { Ok, BudgetExceeded };

struct Frame {
  std::vector<float> s;
  std::vector<Vec3> v;
  /** `WR-43`…`WR-58`: the rotation and matrix register files, four and sixteen floats per entry. */
  std::vector<float> q;
  std::vector<float> m;
  /** `WR-31`: one entry per `program.attrs` binding (indexed by slot), or null when the program
   * has no explicit-index reads. The host installs it before the element loop. */
  const ReadSource *sources = nullptr;
  int n_sources = 0;
  /** `WR-34`: the writes this element buffered. The host harvests them after the element ran
   * and clears the vector - the VM never writes an attribute itself. */
  std::vector<WriteEntry> writes;
  /** `WR-34` promise 1: how many elements this run created, and where its numbering starts. The
   * host counts in a first pass and then sets the base for each element of the real pass. */
  /** `WR-25`: the counters are per point table - index 0 is the mesh, 1 the point cloud - because
   * one program may add to both, and each table numbers its own new elements (`WR-34` promise 1). */
  int new_points[2] = {0, 0};
  int new_point_base[2] = {0, 0};
  /** `WR-25`: one `addpoint` call - the number it handed out, which point table it named, and its
   * position argument. Registers are gone after the run, so the host applies the position from
   * here (it needs the table grown first, which is why it cannot be written in the VM). */
  struct AddPointEntry {
    int number = 0;
    bool cloud = false;
    Vec3 position;
  };
  std::vector<AddPointEntry> added_points;
  /** `WR-36`: the same numbering contract for new primitives. */
  int new_prims = 0;
  int new_prim_base = 0;
  /** `WR-36`/`WR-37`: what the element asked to build or remove; the host applies these in
   * element order, after the parallel loop. */
  std::vector<TopologyEntry> topology;
  /** `WR-28`/`WR-29`: one removal request - the element's buffered index and the binding that made
   * it, which is what tells the host which component (`domain`) it names. */
  struct RemovalEntry {
    int slot = 0;
    int index = 0;
  };
  std::vector<RemovalEntry> removed_points;
  std::vector<RemovalEntry> removed_prims;

  /** Sizes the register files and installs the read sources; call once per thread. */
  void ensure(const Program &program,
              const ReadSource *read_sources = nullptr,
              const int read_source_count = 0)
  {
    if (int(s.size()) != program.n_scalar) {
      s.resize(program.n_scalar);
    }
    if (int(v.size()) != program.n_vector) {
      v.resize(program.n_vector);
    }
    if (int(q.size()) != program.n_quat * 4) {
      q.resize(size_t(program.n_quat) * 4);
    }
    if (int(m.size()) != program.n_matrix * 16) {
      m.resize(size_t(program.n_matrix) * 16);
    }
    sources = read_sources;
    n_sources = read_source_count;
  }
};

/** Instruction budget per element: a runaway `for` loop is stopped, never hung on. */
constexpr uint32_t default_step_budget = 200000;

inline RunStatus run(const Program &program,
                     Frame &frame,
                     uint32_t step_budget = default_step_budget);

/* ------------------------------------------------------------------ */
/** \name Math library                                                  */
/* ------------------------------------------------------------------ */

struct FunctionInfo {
  const char *name;
  int args;
  /** True when the function is applied componentwise to vector arguments. */
  bool componentwise;
};

/** The M1 library; the tests and `manual/` are checked against this list. */
const std::vector<FunctionInfo> &kFunctions();

/* ================================================================== */
/* implementation                                                      */
/* ================================================================== */

namespace detail {

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
inline bool is_space(const char c)
{
  return c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == '\v' || c == '\f';
}

inline std::vector<Token> lex(std::string_view src, std::vector<Error> &errors)
{
  std::vector<Token> tokens;
  size_t p = 0;
  int line = 1, column = 1;
  auto advance = [&](const size_t n) {
    for (size_t k = 0; k < n && p < src.size(); k++) {
      if (src[p] == '\n') {
        line++;
        column = 1;
      }
      else {
        column++;
      }
      p++;
    }
  };
  auto make = [&](const TokKind kind, std::string text, const int l, const int c) {
    Token tok;
    tok.kind = kind;
    tok.text = std::move(text);
    tok.line = l;
    tok.column = c;
    tokens.push_back(std::move(tok));
  };

  while (p < src.size()) {
    const char c = src[p];
    if (is_space(c)) {
      advance(1);
      continue;
    }
    if (c == '/' && p + 1 < src.size() && src[p + 1] == '/') {
      while (p < src.size() && src[p] != '\n') {
        advance(1);
      }
      continue;
    }
    if (c == '/' && p + 1 < src.size() && src[p + 1] == '*') {
      const int sl = line, sc = column;
      advance(2);
      bool closed = false;
      while (p < src.size()) {
        if (src[p] == '*' && p + 1 < src.size() && src[p + 1] == '/') {
          advance(2);
          closed = true;
          break;
        }
        advance(1);
      }
      if (!closed) {
        errors.push_back({sl, sc, "unterminated /* comment"});
      }
      continue;
    }
    if (is_digit(c) || (c == '.' && p + 1 < src.size() && is_digit(src[p + 1]))) {
      const int sl = line, sc = column;
      const size_t start = p;
      while (p < src.size() && (is_digit(src[p]) || src[p] == '.')) {
        advance(1);
      }
      const std::string text(src.substr(start, p - start));
      Token tok;
      tok.kind = TokKind::Number;
      tok.text = text;
      tok.number = std::strtod(text.c_str(), nullptr);
      tok.line = sl;
      tok.column = sc;
      tokens.push_back(std::move(tok));
      continue;
    }
    if (is_ident_start(c)) {
      const int sl = line, sc = column;
      const size_t start = p;
      while (p < src.size() && is_ident_char(src[p])) {
        advance(1);
      }
      const std::string text(src.substr(start, p - start));
      /* `f@`, `i@`, `v@`, and (`WR-43`…) `q@` / `m@` */
      if (text.size() == 1 && (text[0] == 'f' || text[0] == 'i' || text[0] == 'v' ||
                               text[0] == 'q' || text[0] == 'm') &&
          p < src.size() && src[p] == '@')
      {
        advance(1);
        const size_t name_start = p;
        while (p < src.size() && is_ident_char(src[p])) {
          advance(1);
        }
        if (p == name_start) {
          errors.push_back({sl, sc, "expected an attribute name after the type prefix"});
          continue;
        }
        Token tok;
        tok.kind = TokKind::Attr;
        tok.prefix = text[0];
        tok.text = std::string(src.substr(name_start, p - name_start));
        tok.line = sl;
        tok.column = sc;
        tokens.push_back(std::move(tok));
        continue;
      }
      make(TokKind::Ident, text, sl, sc);
      continue;
    }
    if (c == '@') {
      const int sl = line, sc = column;
      advance(1);
      const size_t start = p;
      while (p < src.size() && is_ident_char(src[p])) {
        advance(1);
      }
      if (p == start) {
        errors.push_back({sl, sc, "expected an attribute name after '@'"});
        continue;
      }
      Token tok;
      tok.kind = TokKind::Attr;
      tok.text = std::string(src.substr(start, p - start));
      tok.line = sl;
      tok.column = sc;
      tokens.push_back(std::move(tok));
      continue;
    }
    if (c == '"') {
      /* WR-31: the attribute name in `fpoint(0, "P", i)` is a literal, so it is a token - and
       * because the compiler resolves it straight into the binding, the VM never needs it. */
      const int sl = line, sc = column;
      advance(1);
      std::string text;
      bool closed = false;
      while (p < src.size()) {
        const char ch = src[p];
        if (ch == '"') {
          advance(1);
          closed = true;
          break;
        }
        if (ch == '\n') {
          break; /* report the error on the line where the string opened */
        }
        if (ch == '\\' && p + 1 < src.size()) {
          advance(1);
          const char escaped = src[p];
          text.push_back(escaped == 'n' ? '\n' : escaped);
          advance(1);
          continue;
        }
        text.push_back(ch);
        advance(1);
      }
      if (!closed) {
        errors.push_back({sl, sc, "unterminated string literal"});
        continue;
      }
      make(TokKind::String, text, sl, sc);
      continue;
    }
    {
      const int sl = line, sc = column;
      static const char *two[] = {
          "==", "!=", "<=", ">=", "&&", "||", "+=", "-=", "*=", "/=", "%=", "++", "--"};
      bool matched = false;
      for (const char *op : two) {
        if (src.compare(p, 2, op) == 0) {
          make(TokKind::Punct, op, sl, sc);
          advance(2);
          matched = true;
          break;
        }
      }
      if (matched) {
        continue;
      }
      static const std::string one = "+-*/%=<>!{}()[];,.&|";
      if (one.find(c) != std::string::npos) {
        make(TokKind::Punct, std::string(1, c), sl, sc);
        advance(1);
        continue;
      }
      errors.push_back({sl, sc, std::string("unexpected character '") + c + "'"});
      advance(1);
    }
  }
  Token end;
  end.kind = TokKind::End;
  end.line = line;
  end.column = column;
  tokens.push_back(std::move(end));
  return tokens;
}

/* ---------------------------------------------------------------- table */

inline Fn1 fn1_from_name(const std::string &name)
{
  if (name == "sin") return Fn1::Sin;
  if (name == "cos") return Fn1::Cos;
  if (name == "tan") return Fn1::Tan;
  if (name == "asin") return Fn1::Asin;
  if (name == "acos") return Fn1::Acos;
  if (name == "atan") return Fn1::Atan;
  if (name == "sqrt") return Fn1::Sqrt;
  if (name == "exp") return Fn1::Exp;
  if (name == "log") return Fn1::Log;
  if (name == "log10") return Fn1::Log10;
  if (name == "abs") return Fn1::Abs;
  if (name == "floor") return Fn1::Floor;
  if (name == "ceil") return Fn1::Ceil;
  if (name == "round") return Fn1::Round;
  if (name == "trunc") return Fn1::Trunc;
  if (name == "fract") return Fn1::Fract;
  if (name == "sign") return Fn1::Sign;
  if (name == "radians") return Fn1::Radians;
  return Fn1::Degrees;
}
inline Fn2 fn2_from_name(const std::string &name)
{
  if (name == "atan2") return Fn2::Atan2;
  if (name == "pow") return Fn2::Pow;
  if (name == "min") return Fn2::Min;
  if (name == "max") return Fn2::Max;
  return Fn2::Step;
}
inline Fn3 fn3_from_name(const std::string &name)
{
  if (name == "lerp") return Fn3::Lerp;
  if (name == "clamp") return Fn3::Clamp;
  return Fn3::Smoothstep;
}

/** The M1 library. `kFunctions()` is the public view of exactly this table. */
inline const std::vector<FunctionInfo> &function_table()
{
  static const std::vector<FunctionInfo> table = {
      {"sin", 1, true},        {"cos", 1, true},       {"tan", 1, true},
      {"asin", 1, true},       {"acos", 1, true},      {"atan", 1, true},
      {"sqrt", 1, true},       {"exp", 1, true},       {"log", 1, true},
      {"log10", 1, true},      {"abs", 1, true},       {"floor", 1, true},
      {"ceil", 1, true},       {"round", 1, true},     {"trunc", 1, true},
      {"fract", 1, true},      {"sign", 1, true},      {"radians", 1, true},
      {"degrees", 1, true},    {"atan2", 2, false},    {"pow", 2, false},
      {"min", 2, true},        {"max", 2, true},       {"step", 2, true},
      {"lerp", 3, true},       {"clamp", 3, true},     {"smoothstep", 3, true},
      {"fit", 5, false},       {"length", 1, false},   {"lensq", 1, false},
      {"distance", 2, false},  {"dot", 2, false},      {"cross", 2, false},
      {"normalize", 1, false}, {"rand", 1, false},     {"noise", 1, false},
      {"float", 1, false},     {"int", 1, false},      {"vector", 3, false},
  };
  return table;
}

/* ------------------------------------------------------------------ */
/** \name Rotation and matrix library (`WR-43`…`WR-58`)               */
/* ------------------------------------------------------------------ */

/**
 * These transform functions are named after the Blender *function node* each one stands for, and
 * their argument order is that node's socket order (a node's buttons lead, exactly as they are
 * drawn above its sockets). The maths is `BLI_math`'s, so the numbers match the nodes'.
 */

/** `Quaternion * Quaternion`: `a * b` applies `b` first (Blender's convention). */
inline void quat_mul(const float *a, const float *b, float *out)
{
  const float w = a[0] * b[0] - a[1] * b[1] - a[2] * b[2] - a[3] * b[3];
  const float x = a[0] * b[1] + a[1] * b[0] + a[2] * b[3] - a[3] * b[2];
  const float y = a[0] * b[2] - a[1] * b[3] + a[2] * b[0] + a[3] * b[1];
  const float z = a[0] * b[3] + a[1] * b[2] - a[2] * b[1] + a[3] * b[0];
  out[0] = w;
  out[1] = x;
  out[2] = y;
  out[3] = z;
}

/** `math::transform_point(q, v)`: rotate a vector (no translation, no scale). */
inline Vec3 quat_rotate_vec(const float *q, const Vec3 &v)
{
  const float sw = -q[1] * v.x - q[2] * v.y - q[3] * v.z;
  const float sx = q[0] * v.x + q[2] * v.z - q[3] * v.y;
  const float sy = q[0] * v.y + q[3] * v.x - q[1] * v.z;
  const float sz = q[0] * v.z + q[1] * v.y - q[2] * v.x;
  return Vec3{sw * -q[1] + sx * q[0] - sy * q[3] + sz * q[2],
              sw * -q[2] + sy * q[0] - sz * q[1] + sx * q[3],
              sw * -q[3] + sz * q[0] - sx * q[2] + sy * q[1]};
}

inline void quat_normalize(float *q)
{
  const float len = std::sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3]);
  if (len == 0.0f) {
    q[0] = 1.0f;
    q[1] = q[2] = q[3] = 0.0f;
    return;
  }
  const float inv = 1.0f / len;
  for (int i = 0; i < 4; i++) {
    q[i] *= inv;
  }
}

/** `math::to_quaternion(AxisAngle)`: an axis of length 0 gives the identity (`WR-45`). */
inline void quat_from_axis_angle(const Vec3 &axis, const float angle, float *out)
{
  const float len = std::sqrt(axis.x * axis.x + axis.y * axis.y + axis.z * axis.z);
  if (len == 0.0f) {
    out[0] = 1.0f;
    out[1] = out[2] = out[3] = 0.0f;
    return;
  }
  const float half = angle * 0.5f;
  const float s = std::sin(half) / len;
  out[0] = std::cos(half);
  out[1] = axis.x * s;
  out[2] = axis.y * s;
  out[3] = axis.z * s;
}

/** `math::to_quaternion(EulerXYZ)` and `math::to_euler(Quaternion)`. */
inline void quat_from_euler(const Vec3 &e, float *out)
{
  const float cx = std::cos(e.x * 0.5f), sx = std::sin(e.x * 0.5f);
  const float cy = std::cos(e.y * 0.5f), sy = std::sin(e.y * 0.5f);
  const float cz = std::cos(e.z * 0.5f), sz = std::sin(e.z * 0.5f);
  out[0] = cx * cy * cz + sx * sy * sz;
  out[1] = sx * cy * cz - cx * sy * sz;
  out[2] = cx * sy * cz + sx * cy * sz;
  out[3] = cx * cy * sz - sx * sy * cz;
}

/** `math::to_euler(q)`: the XYZ (EulerXYZ) answer, matching Blender's Rotation to Euler node. */
inline Vec3 euler_from_quat(const float *q)
{
  const float w = q[0], x = q[1], y = q[2], z = q[3];
  const float sinr_cosp = 2.0f * (w * x + y * z);
  const float cosr_cosp = 1.0f - 2.0f * (x * x + y * y);
  const float sinp = 2.0f * (w * y - z * x);
  const float siny_cosp = 2.0f * (w * z + x * y);
  const float cosy_cosp = 1.0f - 2.0f * (y * y + z * z);
  return Vec3{std::atan2(sinr_cosp, cosr_cosp),
              std::fabs(sinp) >= 1.0f ? std::copysign(1.5707963267948966f, sinp) :
                                        std::asin(sinp),
              std::atan2(siny_cosp, cosy_cosp)};
}

/** `math::to_axis_angle(q)`; an identity rotation gives angle 0 and the X axis (`WR-49`). */
inline void axis_angle_from_quat(const float *q, Vec3 &axis, float &angle)
{
  float copy[4] = {q[0], q[1], q[2], q[3]};
  if (copy[0] < 0.0f) {
    for (float &c : copy) {
      c = -c;
    }
  }
  quat_normalize(copy);
  const float s = std::sqrt(copy[1] * copy[1] + copy[2] * copy[2] + copy[3] * copy[3]);
  angle = 2.0f * std::atan2(s, copy[0]);
  if (s <= 0.0f) {
    axis = Vec3{1.0f, 0.0f, 0.0f};
    angle = 0.0f;
    return;
  }
  const float inv = 1.0f / s;
  axis = Vec3{copy[1] * inv, copy[2] * inv, copy[3] * inv};
}

/** `math::invert(q)`: a zero quaternion stays zero (`WR-51`). */
inline void quat_invert(const float *q, float *out)
{
  const float len2 = q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3];
  if (len2 == 0.0f) {
    for (int i = 0; i < 4; i++) {
      out[i] = 0.0f;
    }
    return;
  }
  const float inv = 1.0f / len2;
  out[0] = q[0] * inv;
  out[1] = -q[1] * inv;
  out[2] = -q[2] * inv;
  out[3] = -q[3] * inv;
}

/** Column-major access, the layout of Blender's `float4x4`: `at(m, column, row)`. */
inline int mat_index(const int column, const int row)
{
  return column * 4 + row;
}

inline void mat_identity(float *m)
{
  for (int i = 0; i < 16; i++) {
    m[i] = 0.0f;
  }
  m[0] = m[5] = m[10] = m[15] = 1.0f;
}

/** `math::from_loc_rot_scale` (Combine Transform): `M = T * R * S`, so `M * v = t + R*S*v`. */
inline void mat_from_transform(const Vec3 &t, const float *q, const Vec3 &scale, float *out)
{
  const float scale_components[3] = {scale.x, scale.y, scale.z};
  /* `rot[row][col]` - a row-major 3x3, written out column-major below (`mat_index`). */
  float rot[3][3];
  const float w = q[0], x = q[1], y = q[2], z = q[3];
  rot[0][0] = 1.0f - 2.0f * (y * y + z * z);
  rot[0][1] = 2.0f * (x * y - w * z);
  rot[0][2] = 2.0f * (x * z + w * y);
  rot[1][0] = 2.0f * (x * y + w * z);
  rot[1][1] = 1.0f - 2.0f * (x * x + z * z);
  rot[1][2] = 2.0f * (y * z - w * x);
  rot[2][0] = 2.0f * (x * z - w * y);
  rot[2][1] = 2.0f * (y * z + w * x);
  rot[2][2] = 1.0f - 2.0f * (x * x + y * y);
  for (int row = 0; row < 4; row++) {
    for (int col = 0; col < 4; col++) {
      out[mat_index(col, row)] =
          row < 3 && col < 3 ? rot[row][col] * scale_components[col] : 0.0f;
    }
  }
  out[mat_index(3, 0)] = t.x;
  out[mat_index(3, 1)] = t.y;
  out[mat_index(3, 2)] = t.z;
  out[mat_index(3, 3)] = 1.0f;
}

/** `A * B` in column-major terms: every column of the result is `A * (column of B)`. */
inline void mat_mul(const float *a, const float *b, float *out)
{
  float result[16];
  for (int col = 0; col < 4; col++) {
    for (int row = 0; row < 4; row++) {
      float sum = 0.0f;
      for (int k = 0; k < 4; k++) {
        sum += a[mat_index(k, row)] * b[mat_index(col, k)];
      }
      result[mat_index(col, row)] = sum;
    }
  }
  for (int i = 0; i < 16; i++) {
    out[i] = result[i];
  }
}

inline float mat_determinant(const float *a)
{
  const float s0 = a[0] * (a[5] * (a[10] * a[15] - a[11] * a[14]) -
                           a[6] * (a[9] * a[15] - a[11] * a[13]) +
                           a[7] * (a[9] * a[14] - a[10] * a[13]));
  const float s1 = a[1] * (a[4] * (a[10] * a[15] - a[11] * a[14]) -
                           a[6] * (a[8] * a[15] - a[11] * a[12]) +
                           a[7] * (a[8] * a[14] - a[10] * a[12]));
  const float s2 = a[2] * (a[4] * (a[9] * a[15] - a[11] * a[13]) -
                           a[5] * (a[8] * a[15] - a[11] * a[12]) +
                           a[7] * (a[8] * a[13] - a[9] * a[12]));
  const float s3 = a[3] * (a[4] * (a[9] * a[14] - a[10] * a[13]) -
                           a[5] * (a[8] * a[14] - a[10] * a[12]) +
                           a[6] * (a[8] * a[13] - a[9] * a[12]));
  return s0 - s1 + s2 - s3;
}

/** `math::transpose`. */
/**
 * `math::transpose` (`WR-56`): the entry at (row, column) moves to (column, row), so the flat
 * column-major storage moves `row + column * 4` to `column + row * 4`.
 *
 * Writing this as "read `mat_index(row, col)`, write `mat_index(col, row)`" is a trap: with
 * column-major storage those two expressions are the *same* index (`row + col * 4`), so the loop
 * only ever permutes equal indices and the function becomes a no-op that looks correct. It was one
 * until this was measured on the node: a matrix translating by (1,2,3) came back with its
 * translation still in the last column (`t[0][3]` read 0 where it had to read 1).
 */
inline void mat_transpose(const float *m, float *out)
{
  float result[16];
  for (int row = 0; row < 4; row++) {
    for (int column = 0; column < 4; column++) {
      result[row + column * 4] = m[column + row * 4];
    }
  }
  for (int i = 0; i < 16; i++) {
    out[i] = result[i];
  }
}

/**
 * `math::invert` (`WR-55`) plus the `Invertible` flag Blender's node also exposes: a singular
 * matrix yields the identity and `success = false`, never NaN.
 */
inline void mat_invert(const float *m, float *out, bool &success)
{
  float work[4][8];
  for (int row = 0; row < 4; row++) {
    for (int col = 0; col < 4; col++) {
      work[row][col] = m[mat_index(col, row)];
      work[row][4 + col] = row == col ? 1.0f : 0.0f;
    }
  }
  success = true;
  for (int col = 0; col < 4; col++) {
    int pivot = col;
    for (int row = col + 1; row < 4; row++) {
      if (std::fabs(work[row][col]) > std::fabs(work[pivot][col])) {
        pivot = row;
      }
    }
    if (std::fabs(work[pivot][col]) < 1e-12f) {
      success = false;
      mat_identity(out);
      return;
    }
    if (pivot != col) {
      for (int k = 0; k < 8; k++) {
        std::swap(work[col][k], work[pivot][k]);
      }
    }
    const float inv = 1.0f / work[col][col];
    for (int k = 0; k < 8; k++) {
      work[col][k] *= inv;
    }
    for (int row = 0; row < 4; row++) {
      if (row == col) {
        continue;
      }
      const float factor = work[row][col];
      if (factor == 0.0f) {
        continue;
      }
      for (int k = 0; k < 8; k++) {
        work[row][k] -= factor * work[col][k];
      }
    }
  }
  for (int col = 0; col < 4; col++) {
    for (int row = 0; row < 4; row++) {
      out[mat_index(col, row)] = work[row][4 + col];
    }
  }
}

/** `math::to_loc_rot_scale_safe` (Separate Transform): translation, rotation, scale. */
inline void mat_separate_transform(const float *m, Vec3 &t, float *q, Vec3 &scale)
{
  t = Vec3{m[mat_index(3, 0)], m[mat_index(3, 1)], m[mat_index(3, 2)]};
  float scale_components[3] = {0.0f, 0.0f, 0.0f};
  float rot[3][3];
  for (int col = 0; col < 3; col++) {
    Vec3 axis{m[mat_index(col, 0)], m[mat_index(col, 1)], m[mat_index(col, 2)]};
    const float len = std::sqrt(axis.x * axis.x + axis.y * axis.y + axis.z * axis.z);
    scale_components[col] = len;
    if (len > 0.0f) {
      axis.x /= len;
      axis.y /= len;
      axis.z /= len;
    }
    rot[0][col] = axis.x;
    rot[1][col] = axis.y;
    rot[2][col] = axis.z;
  }
  /* A negative scale shows up as a flipped third axis: fold it into the scale and re-flip the
   * rotation so the quaternion stays a proper rotation. */
  const float det = rot[0][0] * (rot[1][1] * rot[2][2] - rot[1][2] * rot[2][1]) -
                    rot[0][1] * (rot[1][0] * rot[2][2] - rot[1][2] * rot[2][0]) +
                    rot[0][2] * (rot[1][0] * rot[2][1] - rot[1][1] * rot[2][0]);
  if (det < 0.0f) {
    scale_components[2] = -scale_components[2];
    for (int row = 0; row < 3; row++) {
      rot[row][2] = -rot[row][2];
    }
  }
  const float trace = rot[0][0] + rot[1][1] + rot[2][2];
  if (trace > 0.0f) {
    const float s = std::sqrt(trace + 1.0f) * 2.0f;
    q[0] = 0.25f * s;
    q[1] = (rot[2][1] - rot[1][2]) / s;
    q[2] = (rot[0][2] - rot[2][0]) / s;
    q[3] = (rot[1][0] - rot[0][1]) / s;
  }
  else if (rot[0][0] > rot[1][1] && rot[0][0] > rot[2][2]) {
    const float s = std::sqrt(1.0f + rot[0][0] - rot[1][1] - rot[2][2]) * 2.0f;
    q[0] = (rot[2][1] - rot[1][2]) / s;
    q[1] = 0.25f * s;
    q[2] = (rot[0][1] + rot[1][0]) / s;
    q[3] = (rot[0][2] + rot[2][0]) / s;
  }
  else if (rot[1][1] > rot[2][2]) {
    const float s = std::sqrt(1.0f + rot[1][1] - rot[0][0] - rot[2][2]) * 2.0f;
    q[0] = (rot[0][2] - rot[2][0]) / s;
    q[1] = (rot[0][1] + rot[1][0]) / s;
    q[2] = 0.25f * s;
    q[3] = (rot[1][2] + rot[2][1]) / s;
  }
  else {
    const float s = std::sqrt(1.0f + rot[2][2] - rot[0][0] - rot[1][1]) * 2.0f;
    q[0] = (rot[1][0] - rot[0][1]) / s;
    q[1] = (rot[0][2] + rot[2][0]) / s;
    q[2] = (rot[1][2] + rot[2][1]) / s;
    q[3] = 0.25f * s;
  }
  quat_normalize(q);
  scale = Vec3{scale_components[0], scale_components[1], scale_components[2]};
}

/**
 * `math::to_quaternion(float3x3)`: a rotation matrix (nine floats, column-major, so `r[col * 3 +
 * row]`) as a rotation.
 *
 * Every branch reads the same layout. Mixing the two conventions here is a real trap: the trace
 * branch reads the diagonal, which is identical either way, so only the off-diagonal terms tell
 * you - and getting one branch wrong transposes the rotation for every matrix that lands in it
 * (measured: `axes_rotation("z","x", {1,0,0}, {0,1,0})` came back as the mirror rotation below).
 */
inline void quat_from_mat3(const float *r, float *q)
{
  const float trace = r[0] + r[4] + r[8];
  if (trace > 0.0f) {
    const float s = std::sqrt(trace + 1.0f) * 2.0f;
    q[0] = 0.25f * s;
    q[1] = (r[7] - r[5]) / s;
    q[2] = (r[2] - r[6]) / s;
    q[3] = (r[3] - r[1]) / s;
  }
  else if (r[0] > r[4] && r[0] > r[8]) {
    const float s = std::sqrt(1.0f + r[0] - r[4] - r[8]) * 2.0f;
    q[0] = (r[7] - r[5]) / s;
    q[1] = 0.25f * s;
    q[2] = (r[3] + r[1]) / s;
    q[3] = (r[6] + r[2]) / s;
  }
  else if (r[4] > r[8]) {
    const float s = std::sqrt(1.0f + r[4] - r[0] - r[8]) * 2.0f;
    q[0] = (r[2] - r[6]) / s;
    q[1] = (r[3] + r[1]) / s;
    q[2] = 0.25f * s;
    q[3] = (r[7] + r[5]) / s;
  }
  else {
    const float s = std::sqrt(1.0f + r[8] - r[0] - r[4]) * 2.0f;
    q[0] = (r[5] - r[7]) / s;
    q[1] = (r[6] + r[2]) / s;
    q[2] = (r[7] + r[5]) / s;
    q[3] = 0.25f * s;
  }
  quat_normalize(q);
}

/**
 * `align_rotation_to_vector` (`WR-52`): the node's auto-pivot path. A zero vector gives the input
 * rotation back, and a degenerate cross product falls back to another axis exactly like
 * `node_fn_align_rotation_to_vector.cc` does.
 */
inline void quat_align_to_vector(const float *q,
                                 const int main_axis,
                                 const Vec3 &v,
                                 const float factor,
                                 float *out)
{
  const Vec3 local_main{main_axis == 0 ? 1.0f : 0.0f,
                        main_axis == 1 ? 1.0f : 0.0f,
                        main_axis == 2 ? 1.0f : 0.0f};
  if (v.x == 0.0f && v.y == 0.0f && v.z == 0.0f) {
    for (int i = 0; i < 4; i++) {
      out[i] = q[i];
    }
    return;
  }
  const Vec3 old_axis = quat_rotate_vec(q, local_main);
  const float vlen = std::sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
  const Vec3 new_axis{v.x / vlen, v.y / vlen, v.z / vlen};
  const auto cross = [](const Vec3 &a, const Vec3 &b) {
    return Vec3{a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
  };
  Vec3 rotation_axis = cross(old_axis, new_axis);
  if (rotation_axis.x == 0.0f && rotation_axis.y == 0.0f && rotation_axis.z == 0.0f) {
    rotation_axis = cross(old_axis, Vec3{1.0f, 0.0f, 0.0f});
    if (rotation_axis.x == 0.0f && rotation_axis.y == 0.0f && rotation_axis.z == 0.0f) {
      rotation_axis = cross(old_axis, Vec3{0.0f, 1.0f, 0.0f});
    }
  }
  float dot = old_axis.x * new_axis.x + old_axis.y * new_axis.y + old_axis.z * new_axis.z;
  dot = dot < -1.0f ? -1.0f : (dot > 1.0f ? 1.0f : dot);
  const float angle = factor * std::acos(dot);
  float delta[4];
  quat_from_axis_angle(rotation_axis, angle, delta);
  quat_mul(delta, q, out);
}

/** `Axes to Rotation` (`WR-50`): the primary axis is exact, the secondary is best effort. */
inline void quat_from_axes(const int primary_axis,
                           const int secondary_axis,
                           const Vec3 &primary_in,
                           const Vec3 &secondary_in,
                           float *out)
{
  const auto identity = [&]() {
    out[0] = 1.0f;
    out[1] = out[2] = out[3] = 0.0f;
  };
  if (primary_axis == secondary_axis) {
    identity();
    return;
  }
  const int tertiary_axis = 3 - primary_axis - secondary_axis;
  const bool invert_tertiary = (secondary_axis + 1) % 3 == primary_axis;
  const float tertiary_factor = invert_tertiary ? -1.0f : 1.0f;

  const float plen = std::sqrt(primary_in.x * primary_in.x + primary_in.y * primary_in.y +
                               primary_in.z * primary_in.z);
  const float slen = std::sqrt(secondary_in.x * secondary_in.x + secondary_in.y * secondary_in.y +
                               secondary_in.z * secondary_in.z);
  const bool primary_non_zero = plen > 0.0f;
  const bool secondary_non_zero = slen > 0.0f;
  const auto orthogonal_of = [](const Vec3 &v) {
    if (v.x != -v.y) {
      return Vec3{-v.y, v.x, 0.0f};
    }
    if (v.x != -v.z) {
      return Vec3{-v.z, 0.0f, v.x};
    }
    return Vec3{0.0f, -v.z, v.y};
  };
  const auto unit = [](const Vec3 &v) {
    const float len = std::sqrt(v.x * v.x + v.y * v.y + v.z * v.z);
    return len > 0.0f ? Vec3{v.x / len, v.y / len, v.z / len} : Vec3{0.0f, 0.0f, 0.0f};
  };
  const auto cross = [](const Vec3 &a, const Vec3 &b) {
    return Vec3{a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
  };

  Vec3 primary = unit(primary_in);
  Vec3 secondary{secondary_in.x, secondary_in.y, secondary_in.z};
  Vec3 tertiary;
  if (primary_non_zero && secondary_non_zero) {
    tertiary = cross(primary, secondary);
    if (tertiary.x == 0.0f && tertiary.y == 0.0f && tertiary.z == 0.0f) {
      tertiary = orthogonal_of(primary);
    }
    tertiary = unit(tertiary);
    secondary = cross(tertiary, primary);
  }
  else if (primary_non_zero) {
    secondary = unit(orthogonal_of(primary));
    tertiary = cross(primary, secondary);
  }
  else if (secondary_non_zero) {
    secondary = unit(secondary);
    primary = unit(orthogonal_of(secondary));
    tertiary = cross(primary, secondary);
  }
  else {
    identity();
    return;
  }

  float basis[9] = {0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f};
  const auto set_column = [&](const int axis, const Vec3 &value) {
    basis[axis * 3 + 0] = value.x;
    basis[axis * 3 + 1] = value.y;
    basis[axis * 3 + 2] = value.z;
  };
  set_column(primary_axis, primary);
  set_column(secondary_axis, secondary);
  set_column(tertiary_axis,
             Vec3{tertiary.x * tertiary_factor,
                  tertiary.y * tertiary_factor,
                  tertiary.z * tertiary_factor});
  quat_from_mat3(basis, out);
}

inline float apply_fn1(const Fn1 fn, const float x)
{
  switch (fn) {
    case Fn1::Sin: return std::sin(x);
    case Fn1::Cos: return std::cos(x);
    case Fn1::Tan: return std::tan(x);
    case Fn1::Asin: return std::asin(x);
    case Fn1::Acos: return std::acos(x);
    case Fn1::Atan: return std::atan(x);
    case Fn1::Sqrt: return std::sqrt(x);
    case Fn1::Exp: return std::exp(x);
    case Fn1::Log: return std::log(x);
    case Fn1::Log10: return std::log10(x);
    case Fn1::Abs: return std::fabs(x);
    case Fn1::Floor: return std::floor(x);
    case Fn1::Ceil: return std::ceil(x);
    case Fn1::Round: return std::round(x);
    case Fn1::Trunc: return std::trunc(x);
    case Fn1::Fract: return x - std::floor(x);
    case Fn1::Sign: return x > 0.0f ? 1.0f : (x < 0.0f ? -1.0f : 0.0f);
    case Fn1::Radians: return x * 0.017453292519943295f;
    case Fn1::Degrees: return x * 57.29577951308232f;
  }
  return x;
}

inline float apply_fn2(const Fn2 fn, const float a, const float b)
{
  switch (fn) {
    case Fn2::Atan2: return std::atan2(a, b);
    case Fn2::Pow: return std::pow(a, b);
    case Fn2::Min: return a < b ? a : b;
    case Fn2::Max: return a > b ? a : b;
    /* VEX step(edge, x): 0 when x < edge, else 1. */
    case Fn2::Step: return b < a ? 0.0f : 1.0f;
  }
  return a;
}

inline float apply_fn3(const Fn3 fn, const float a, const float b, const float c)
{
  switch (fn) {
    case Fn3::Lerp:
      return a + (b - a) * c;
    case Fn3::Clamp:
      return a < b ? b : (a > c ? c : a);
    case Fn3::Smoothstep: {
      /* smoothstep(edge0, edge1, x) */
      const float span = b - a;
      if (span == 0.0f) {
        return 0.0f;
      }
      float t = (c - a) / span;
      t = t < 0.0f ? 0.0f : (t > 1.0f ? 1.0f : t);
      return t * t * (3.0f - 2.0f * t);
    }
  }
  return a;
}

/** Deterministic 32-bit hash (PCG-ish); the same everywhere, no state. */
inline uint32_t hash_u32(uint32_t x)
{
  x ^= x >> 16;
  x *= 0x7feb352du;
  x ^= x >> 15;
  x *= 0x846ca68bu;
  x ^= x >> 16;
  return x;
}
inline float hash_to_unit(const uint32_t h)
{
  return float(h >> 8) * (1.0f / 16777216.0f);
}
inline float hash_float(const float x)
{
  uint32_t bits;
  static_assert(sizeof(bits) == sizeof(x));
  std::memcpy(&bits, &x, sizeof(bits));
  return hash_to_unit(hash_u32(bits));
}
/** Trilinear value noise on an integer lattice; continuous, deterministic, not Houdini's. */
inline float value_noise(const float x, const float y, const float z)
{
  const float fx = std::floor(x), fy = std::floor(y), fz = std::floor(z);
  const float tx = x - fx, ty = y - fy, tz = z - fz;
  const auto corner = [](const float ix, const float iy, const float iz) {
    const uint32_t hx = hash_u32(uint32_t(int32_t(ix)) * 73856093u);
    const uint32_t hy = hash_u32(uint32_t(int32_t(iy)) * 19349663u);
    const uint32_t hz = hash_u32(uint32_t(int32_t(iz)) * 83492791u);
    return hash_to_unit(hash_u32(hx ^ hy ^ hz));
  };
  const float ux = tx * tx * (3.0f - 2.0f * tx);
  const float uy = ty * ty * (3.0f - 2.0f * ty);
  const float uz = tz * tz * (3.0f - 2.0f * tz);
  const float c000 = corner(fx, fy, fz), c100 = corner(fx + 1, fy, fz);
  const float c010 = corner(fx, fy + 1, fz), c110 = corner(fx + 1, fy + 1, fz);
  const float c001 = corner(fx, fy, fz + 1), c101 = corner(fx + 1, fy, fz + 1);
  const float c011 = corner(fx, fy + 1, fz + 1), c111 = corner(fx + 1, fy + 1, fz + 1);
  const float x00 = c000 + (c100 - c000) * ux, x10 = c010 + (c110 - c010) * ux;
  const float x01 = c001 + (c101 - c001) * ux, x11 = c011 + (c111 - c011) * ux;
  const float y0 = x00 + (x10 - x00) * uy, y1 = x01 + (x11 - x01) * uy;
  return (y0 + (y1 - y0) * uz) * 2.0f - 1.0f;
}

/* ------------------------------------------------------------- compiler */

struct Value {
  Type type = Type::Float;
  int reg = -1;
};

struct Local {
  Type type = Type::Float;
  int reg = -1;
};

class Compiler {
 public:
  Compiler(const std::vector<Token> &tokens, Program &program, const bool detail = false)
      : tokens_(tokens), program_(program), detail_(detail)
  {
    /* `P` is the one name VEX and Blender spell differently. */
    aliases_["P"] = "position";
  }

  void compile_program();

  bool failed() const
  {
    return !program_.errors.empty();
  }

 private:
  const std::vector<Token> &tokens_;
  Program &program_;
  size_t pos_ = 0;
  bool panic_ = false;
  std::vector<std::unordered_map<std::string, Local>> scopes_;
  std::unordered_map<std::string, std::string> aliases_;
  std::unordered_map<std::string, int> attr_index_;
  /** True when this program is compiled for `Run Over = Detail` (`WR-8`, `WR-30`). */
  bool detail_ = false;
  static constexpr int max_registers = 4096;

  /* ------------------------------------------------------------ tokens */
  const Token &peek(const size_t ahead = 0) const
  {
    const size_t index = pos_ + ahead;
    return tokens_[index < tokens_.size() ? index : tokens_.size() - 1];
  }
  bool at_end() const
  {
    return peek().kind == TokKind::End;
  }
  const Token &advance()
  {
    const size_t index = at_end() ? pos_ : pos_++;
    return tokens_[index];
  }
  bool check_punct(std::string_view text) const
  {
    return peek().kind == TokKind::Punct && peek().text == text;
  }
  bool check_ident(std::string_view text) const
  {
    return peek().kind == TokKind::Ident && peek().text == text;
  }
  bool accept_punct(std::string_view text)
  {
    if (check_punct(text)) {
      advance();
      return true;
    }
    return false;
  }
  bool accept_ident(std::string_view text)
  {
    if (check_ident(text)) {
      advance();
      return true;
    }
    return false;
  }
  void error(const int line, const int column, std::string message)
  {
    program_.errors.push_back({line, column, std::move(message)});
  }
  void error_here(std::string message)
  {
    error(peek().line, peek().column, std::move(message));
  }
  void expect_punct(std::string_view text, std::string message)
  {
    if (!accept_punct(text)) {
      error_here(std::move(message));
      panic_ = true;
    }
  }

  /* --------------------------------------------------------- registers */
  int new_scalar()
  {
    if (program_.n_scalar >= max_registers) {
      if (program_.errors.empty()) {
        error_here("the code is too large (register limit reached)");
      }
      return 0;
    }
    return program_.n_scalar++;
  }
  int new_vector()
  {
    if (program_.n_vector >= max_registers) {
      if (program_.errors.empty()) {
        error_here("the code is too large (register limit reached)");
      }
      return 0;
    }
    return program_.n_vector++;
  }
  int new_quat()
  {
    if (program_.n_quat >= max_registers) {
      if (program_.errors.empty()) {
        error_here("the code is too large (register limit reached)");
      }
      return 0;
    }
    return program_.n_quat++;
  }
  int new_matrix()
  {
    if (program_.n_matrix >= max_registers) {
      if (program_.errors.empty()) {
        error_here("the code is too large (register limit reached)");
      }
      return 0;
    }
    return program_.n_matrix++;
  }
  int new_reg(const Type type)
  {
    switch (type) {
      case Type::Vector:
        return new_vector();
      case Type::Quaternion:
        return new_quat();
      case Type::Matrix:
        return new_matrix();
      case Type::Float:
      case Type::Int:
        break;
    }
    return new_scalar();
  }
  /** The whole-value copy of a register file (`WR-43`…`WR-58` added the last two). */
  static Op copy_op(const Type type)
  {
    switch (type) {
      case Type::Vector:
        return Op::CopyV;
      case Type::Quaternion:
        return Op::CopyQ;
      case Type::Matrix:
        return Op::CopyM;
      case Type::Float:
      case Type::Int:
        break;
    }
    return Op::CopyF;
  }
  /**
   * `TGet` / `TSet` pack "which register file, which element" into `imm`: bit 12 selects the
   * matrix file (a rotation and a matrix register may carry the same index), and the element sits
   * in the upper bits.
   */
  static int element_pack(const Type type, const int element)
  {
    return (type == Type::Matrix ? 0x1000 : 0) | (element << 16);
  }
  int emit(const Op op,
           const int dst = 0,
           const int a = 0,
           const int b = 0,
           const int c = 0,
           const int d = 0)
  {
    Instr instr;
    instr.op = op;
    instr.dst = uint8_t(dst);
    instr.a = uint8_t(a);
    instr.b = uint8_t(b);
    instr.c = uint8_t(c);
    instr.d = uint8_t(d);
    program_.code.push_back(instr);
    return int(program_.code.size()) - 1;
  }
  void set_imm(const int at, const int32_t value)
  {
    program_.code[at].imm = value;
  }
  void set_fimm(const int at, const float value)
  {
    program_.code[at].fimm = value;
  }
  int here() const
  {
    return int(program_.code.size());
  }
  void patch_jump(const int at, const int target)
  {
    program_.code[at].imm = target;
  }

  /* ------------------------------------------------------------ scopes */
  void push_scope()
  {
    scopes_.emplace_back();
  }
  void pop_scope()
  {
    scopes_.pop_back();
  }
  Local *find_local(const std::string &name)
  {
    for (auto it = scopes_.rbegin(); it != scopes_.rend(); ++it) {
      const auto found = it->find(name);
      if (found != it->end()) {
        return &found->second;
      }
    }
    return nullptr;
  }
  Local *find_local_here(const std::string &name)
  {
    if (scopes_.empty()) {
      return nullptr;
    }
    const auto found = scopes_.back().find(name);
    return found == scopes_.back().end() ? nullptr : &found->second;
  }

  /* -------------------------------------------------------- attributes */
  Type prefix_type(const char prefix) const
  {
    switch (prefix) {
      case 'v':
        return Type::Vector;
      case 'q':
        return Type::Quaternion;
      case 'm':
        return Type::Matrix;
      case 'i':
        return Type::Int;
      default:
        return Type::Float;
    }
  }
  static std::string attr_key(const int input, const std::string &name)
  {
    return input == 0 ? name : (std::to_string(input) + ":" + name);
  }
  /**
   * Recognises the removed `@opinput<n>_<name>` form: writes n into `input` and strips the prefix
   * off `name` so the caller can report *that* name back. Returns -1 for an ordinary attribute
   * name. The form itself is retired (它的读取属于 L0 的"明确不做")，剩下的跨流读法是显式索引函数。`# ` */
  int split_input(const Token &tok, std::string &name) const
  {
    name = tok.text;
    if (name.compare(0, 7, "opinput") != 0) {
      return -1;
    }
    size_t index = 7;
    int input = 0;
    while (index < name.size() && name[index] >= '0' && name[index] <= '9') {
      input = input * 10 + (name[index] - '0');
      ++index;
    }
    if (index == 7 || index >= name.size() || name[index] != '_') {
      return -1;
    }
    name = name.substr(index + 1);
    return input;
  }
  int attr_slot(const int input,
                const std::string &name,
                const Type type,
                const Token &tok)
  {
    const std::string key = attr_key(input, name);
    const std::string where = input == 0 ?
                                  ("'" + name + "'") :
                                  ("input " + std::to_string(input) + "'s '" + name + "'");
    const auto found = attr_index_.find(key);
    if (found != attr_index_.end()) {
      AttrBinding &binding = program_.attrs[found->second];
      if (binding.type != type) {
        error(tok.line,
              tok.column,
              "attribute " + where + " is already used as " + type_name(binding.type) +
                  ", here as " + type_name(type));
      }
      return found->second;
    }
    AttrBinding binding;
    binding.name = name;
    binding.type = type;
    binding.input = input;
    binding.reg = new_reg(type);
    program_.attrs.push_back(binding);
    const int slot = int(program_.attrs.size()) - 1;
    attr_index_[key] = slot;
    return slot;
  }
  Value resolve_attribute(const Token &tok, const bool writing)
  {
    /* WR-8: Detail has no per-element context, so `@` does not exist there at all - and
     * `@opinput<n>_<name>` in particular (WR-30). The explicit-index functions are the way
     * to reach geometry from a Detail run. */
    if (detail_) {
      error(tok.line,
            tok.column,
            "'@' cannot be used when Run Over is Detail (WR-8): there is no per-element "
            "context. Use the explicit-index functions instead, e.g. fpoint(0, \"P\", 0)");
    }
    std::string source;
    int input = split_input(tok, source);
    if (input != -1) {
      /* 明确不做: `@opinput<n>_<name>` 跨流读取。报错之后按 input 0 继续解析——程序已经失败，
       * 继续只是为了不产生级联错误。读别的几何输入的合法途径是显式索引函数（fpoint / ipoint /
       * vpoint / qpoint / mpoint 与 fprim / iprim / vprim / qprim / mprim 的 geo 取 1..3，WR-20）。 */
      error(tok.line,
            tok.column,
            "'@opinput" + std::to_string(input) + "_" + source +
                "' does not exist: cross-stream reads are not supported; read another input with "
                "fpoint / ipoint / vpoint / qpoint / mpoint or fprim / iprim / vprim / qprim / "
                "mprim and geo 1..3");
    }
    /* The only producer of `input > 0` was the retired form, so every surviving binding reads
     * input 0 (`attr_key` and the host's `host_supplied` check both rely on this). */
    input = 0;
    const bool host_supplied = input == 0 && (source == "elemnum" || source == "numelem");
    if (host_supplied) {
      if (writing) {
        error(tok.line, tok.column, "'" + source + "' is read-only");
      }
      if (tok.prefix != 0 && prefix_type(tok.prefix) != Type::Int) {
        error(tok.line, tok.column, "'" + source + "' is an int");
      }
      const int slot = attr_slot(input, source, Type::Int, tok);
      AttrBinding &binding = program_.attrs[slot];
      binding.read = true;
      binding.host_supplied = true;
      Value value;
      value.type = Type::Int;
      value.reg = binding.reg;
      return value;
    }

    const std::string &name = aliases_.count(source) ? aliases_[source] : source;
    /* WR-3: a bare name would have to be *guessed* at compile time, so every attribute
     * reference carries a type prefix. The only names whose type is known without the
     * geometry are the aliases (`P` -> position, a vector) and the host-provided element
     * numbers, which returned above. `@opinput<n>_…` without a prefix was reported above. */
    if (tok.prefix == 0 && input == 0 && !aliases_.count(source)) {
      /* WR-9: only @elemnum / @numelem carry an element number. The Houdini names are gone,
       * so point at the replacement instead of only complaining about the missing prefix. */
      if (source == "ptnum" || source == "primnum") {
        error(tok.line,
              tok.column,
              "'@" + source + "' does not exist (WR-9): use @elemnum for the element number and "
              "@numelem for the element count");
      }
      error(tok.line,
            tok.column,
            "'@" + source + "' needs a type prefix (f@" + source + " / i@" + source + " / v@" +
                source + "); only @P, @elemnum and @numelem may be written bare");
    }
    const auto found = attr_index_.find(attr_key(input, name));
    Type type;
    if (found != attr_index_.end()) {
      type = program_.attrs[found->second].type;
      if (tok.prefix != 0 && prefix_type(tok.prefix) != type) {
        error(tok.line,
              tok.column,
              "attribute '" + name + "' is already used as " + type_name(type) + ", not " +
                  type_name(prefix_type(tok.prefix)));
      }
    }
    else if (tok.prefix != 0) {
      type = prefix_type(tok.prefix);
    }
    else {
      /* A known alias keeps its own type; an unknown bare name already errored above. */
      type = source == "P" ? Type::Vector : Type::Float;
    }
    const int slot = attr_slot(input, name, type, tok);
    AttrBinding &binding = program_.attrs[slot];
    if (writing) {
      binding.write = true;
    }
    else {
      binding.read = true;
    }
    Value value;
    value.type = binding.type;
    value.reg = binding.reg;
    return value;
  }

  /* ----------------------------------------------------------- codegen */
  Value cast_to(Value value, const Type target)
  {
    if (value.type == target) {
      return value;
    }
    if (target == Type::Float && value.type == Type::Int) {
      value.type = Type::Float; /* ints already live in a scalar register */
      return value;
    }
    if (target == Type::Int && is_scalar(value.type)) {
      Value out;
      out.type = Type::Int;
      out.reg = new_scalar();
      emit(Op::TruncF, out.reg, value.reg);
      return out;
    }
    if (target == Type::Vector && value.type != Type::Vector) {
      /* Scalar to vector: broadcast, which is what VEX does for `vector x = 1;`. */
      Value out;
      out.type = Type::Vector;
      out.reg = new_vector();
      emit(Op::MakeV, out.reg, value.reg, value.reg, value.reg);
      return out;
    }
    if (is_transform(value.type) || is_transform(target)) {
      /* `WR-43`…`WR-58`: a rotation and a matrix are not numbers - there is no implicit conversion
       * between them and the numeric types, so this is always an error. */
      error_here(std::string("cannot convert ") + type_name(value.type) + " to " +
                 type_name(target));
      return value;
    }
    if (target != Type::Vector && value.type == Type::Vector) {
      error_here("cannot use a vector here (pick a component, e.g. .x)");
      return value;
    }
    return value;
  }

  /** Promotes both sides of an arithmetic operation to a common shape. */
  void unify(Value &left, Value &right)
  {
    if (is_transform(left.type) || is_transform(right.type)) {
      error_here(std::string("cannot do arithmetic on ") +
                 type_name(is_transform(left.type) ? left.type : right.type));
      return;
    }
    if (left.type == Type::Vector || right.type == Type::Vector) {
      left = cast_to(left, left.type == Type::Vector ? Type::Vector : Type::Float);
      right = cast_to(right, right.type == Type::Vector ? Type::Vector : Type::Float);
      return;
    }
    if (left.type == Type::Int && right.type == Type::Int) {
      return;
    }
    left = cast_to(left, Type::Float);
    right = cast_to(right, Type::Float);
  }

  Value unary(const std::string &op, Value operand, const Token &tok)
  {
    if (op == "-" && is_transform(operand.type)) {
      error(tok.line, tok.column, std::string("cannot negate a ") + type_name(operand.type));
      return operand;
    }
    if (op == "-") {
      Value out;
      out.type = operand.type;
      out.reg = new_reg(operand.type);
      emit(operand.type == Type::Vector ? Op::NegV : Op::NegF, out.reg, operand.reg);
      return out;
    }
    if (op == "!") {
      operand = cast_to(operand, Type::Int);
      Value out;
      out.type = Type::Int;
      out.reg = new_scalar();
      emit(Op::NotI, out.reg, operand.reg);
      return out;
    }
    error(tok.line, tok.column, "unsupported unary operator '" + op + "'");
    return operand;
  }

  Value binary(const std::string &op, Value left, Value right, const Token &tok)
  {
    const bool comparison =
        op == "<" || op == "<=" || op == ">" || op == ">=" || op == "==" || op == "!=";
    if (op == "&&" || op == "||") {
      left = cast_to(left, Type::Int);
      right = cast_to(right, Type::Int);
      Value out;
      out.type = Type::Int;
      out.reg = new_scalar();
      emit(op == "&&" ? Op::AndI : Op::OrI, out.reg, left.reg, right.reg);
      return out;
    }
    if (comparison) {
      if (left.type == Type::Vector || right.type == Type::Vector) {
        error(tok.line, tok.column, "compare vector components instead of whole vectors");
        return left;
      }
      if (is_transform(left.type) || is_transform(right.type)) {
        error(tok.line,
              tok.column,
              std::string("compare ") + type_name(is_transform(left.type) ? left.type : right.type) +
                  " components instead of whole values");
        return left;
      }
      const bool both_int = left.type == Type::Int && right.type == Type::Int;
      unify(left, right);
      Value out;
      out.type = Type::Int;
      out.reg = new_scalar();
      static const char *names[] = {"<", "<=", ">", ">=", "==", "!="};
      static const Op ops[] = {Op::LtF, Op::LeF, Op::GtF, Op::GeF, Op::EqF, Op::NeF};
      for (int k = 0; k < 6; k++) {
        if (op == names[k]) {
          emit(ops[k], out.reg, left.reg, right.reg);
          break;
        }
      }
      (void)both_int;
      return out;
    }

    /* `WR-53`: a matrix times a vector is the transform applied to that vector (`m * v`), and the
     * mirrored form is the same product written the other way round. This is the same `M * v` that
     * Blender's `float4x4 * float3` means, so it lives in the ordinary `*` operator. */
    if (op == "*" && (left.type == Type::Matrix || right.type == Type::Matrix) &&
        (left.type == Type::Vector || right.type == Type::Vector))
    {
      const bool matrix_on_left = left.type == Type::Matrix;
      Value out{Type::Vector, new_vector()};
      emit(Op::MatrixTransformV,
           out.reg,
           matrix_on_left ? left.reg : right.reg,
           matrix_on_left ? right.reg : left.reg);
      return out;
    }

    unify(left, right);
    const bool vec_left = left.type == Type::Vector;
    const bool vec_right = right.type == Type::Vector;
    const bool both_int = !vec_left && !vec_right && left.type == Type::Int &&
                          right.type == Type::Int;
    Value out;
    out.type = (vec_left || vec_right) ? Type::Vector : (both_int ? Type::Int : Type::Float);
    out.reg = new_reg(out.type);

    static const char *names[] = {"+", "-", "*", "/", "%"};
    if (out.type == Type::Vector) {
      if (vec_left && vec_right) {
        static const Op ops[] = {Op::AddV, Op::SubV, Op::MulV, Op::DivV};
        for (int k = 0; k < 4; k++) {
          if (op == names[k]) {
            emit(ops[k], out.reg, left.reg, right.reg);
            return out;
          }
        }
      }
      else if (vec_left) {
        static const Op ops[] = {Op::AddVF, Op::SubVF, Op::MulVF, Op::DivVF};
        for (int k = 0; k < 4; k++) {
          if (op == names[k]) {
            emit(ops[k], out.reg, left.reg, right.reg);
            return out;
          }
        }
      }
      else {
        /* scalar op vector: the commutative ops keep the vector in `a`; SubFV/DivFV are
         * defined as "scalar - vector", so there the scalar goes in `a`. */
        if (op == "+" || op == "*") {
          emit(op == "+" ? Op::AddVF : Op::MulVF, out.reg, right.reg, left.reg);
          return out;
        }
        if (op == "-" || op == "/") {
          emit(op == "-" ? Op::SubFV : Op::DivFV, out.reg, left.reg, right.reg);
          return out;
        }
        error(tok.line, tok.column, "unsupported vector operator '" + op + "'");
        return out;
      }
    }

    static const Op ops[] = {Op::AddF, Op::SubF, Op::MulF, Op::DivF, Op::ModF};
    /* `%` is `fmod` for floats (VEX allows it) and a truncated remainder for ints. */
    for (int k = 0; k < 5; k++) {
      if (op == names[k]) {
        emit(ops[k], out.reg, left.reg, right.reg);
        return out;
      }
    }
    error(tok.line, tok.column, "unsupported operator '" + op + "'");
    return out;
  }

  Value call_function(const Token &name_tok, std::vector<Value> &args);
  /** `WR-10`: the alias table applies to the string names too, so `fpoint(0, "P", i)` reads the
   * same attribute `@P` does. Without this the literal would bind a shadow attribute called "P". */
  std::string resolve_name(const std::string &name) const
  {
    const auto found = aliases_.find(name);
    return found == aliases_.end() ? name : found->second;
  }

  /** `WR-31`: the explicit-index reads - `fpoint`/`vprim`/`npoints`/... and their table. */
  struct ExplicitRead {
    const char *name;
    ReadDomain domain;
    Type type;
    bool count;
  };
  static const ExplicitRead *find_explicit_read(const std::string &name);
  Value explicit_read(const ExplicitRead &read, const Token &name_tok);
  /** `WR-36`: the channel reads - `chf` / `chi` / `chv` take a *literal* channel name and the
   * function name fixes the type (Float / Int / Vector). */
  struct ChannelRead {
    const char *name;
    Type type;
  };
  static const ChannelRead *find_channel_read(const std::string &name);
  Value channel_read(const ChannelRead &read, const Token &name_tok);
  int channel_slot(const std::string &name, Type type, const Token &tok);
  /** `WR-33`: the write functions - all of them take the literal `0` as their first argument. */
  struct IndexedWrite {
    const char *name;
    ReadDomain domain;
    /** The argument shape after the literal `0`: the attribute setters take a name, an index and
     * a value; `addpoint` takes nothing. `WR-36`'s `addprim` and `WR-37`'s removals join here. */
    enum class Shape : uint8_t { Attrib, AddPoint, AddPrim, RemovePoint, RemovePrim } shape;
  };
  static const IndexedWrite *find_indexed_write(const std::string &name);
  Value indexed_write_call(const IndexedWrite &write, const Token &name_tok);
  int indexed_write_slot(const std::string &name,
                         const ReadDomain domain,
                         const Component component,
                         const Type type,
                         const Token &tok);
  int explicit_slot(const int input,
                    const std::string &name,
                    const ReadDomain domain,
                    const Component component,
                    const Type type,
                    const Token &tok);
  /** After a bad argument: consume to the matching ')' so one typo is one error. */
  void skip_to_call_end();
  Value primary();
  Value postfix(Value value, const Token &start);
  Value expression(int min_precedence = 0);

  /* ---------------------------------------------------------- statements */
  void statement();
  void block();
  void if_statement();
  void for_statement();
  void declaration(const Token &type_tok);
  void assignment_or_call();
  struct LValue {
    bool valid = false;
    bool is_attribute = false;
    int slot = -1;
    int reg = -1;
    Type type = Type::Float;
    int component = -1;
    /**
     * `WR-47` / `WR-53`: which float of a quaternion (0…3) or matrix (0…15) is addressed, or -1
     * when the whole value is. `component` stays the addressable *place* (a vector component or a
     * quaternion slot), which is what an assignment can write.
     */
    int element = -1;
    std::string name;
  };
  LValue parse_lvalue();
  void store_lvalue(const LValue &target, const std::string &op, Value rhs, const Token &op_tok);
  /** `WR-47` / `WR-53`: reads one scalar component (0…3 of a rotation, 0…15 of a matrix). */
  Value read_component(Value value, int element, const Token &at, const char *what);
  /**
   * `WR-49` / `WR-58`: an argument written `&name` is an **out parameter** - the call writes its
   * result straight back into that variable or attribute. Only a plain place (a declared variable
   * or a whole `@attribute`) can be one.
   */
  struct OutArg {
    bool valid = false;
    bool is_attribute = false;
    int reg = -1;
    int slot = -1;
    int component = -1;
    Type type = Type::Float;
    std::string name;
  };
  /**
   * `WR-43` / `WR-50` / `WR-52`: the button words of the call being compiled. The argument parser
   * collects them as it consumes the argument list (a string literal is not an expression), so the
   * argument that reaches `call_function` keeps its correct *position*.
   */
  struct CallWord {
    bool valid = false;
    std::string text;
  };
  std::vector<CallWord> call_words_;
  int call_word_line_ = 0;
  int call_word_column_ = 0;
  /**
   * The head of one argument must be a string literal from `words` - this is how a Blender node's
   * *button* is written as an argument (the Space / Axis / Pivot menus of `WR-43`, `WR-50` and
   * `WR-52`). Returns the index into `words`, or -1 after reporting the error.
   */
  int literal_word_index(int argument,
                         std::initializer_list<const char *> words,
                         const char *label);
  Value call_function(const Token &name_tok,
                      std::vector<Value> &args,
                      std::vector<OutArg> &out_args);
  /**
   * Parses a comma separated argument list, stopping before the closing `)`.
   *
   * With `out_args` non-null a `&name` argument is recognised as an out parameter; `&&` is one
   * token, so a lone `&` can only mean "write back into this place".
   */
  void parse_arguments(std::vector<Value> *args,
                       std::vector<OutArg> *out_args,
                       const std::string *call_name);
};

/* ------------------------------------------------- precedence climbing */

inline int binary_precedence(const std::string &op)
{
  if (op == "||") return 1;
  if (op == "&&") return 2;
  if (op == "==" || op == "!=") return 3;
  if (op == "<" || op == "<=" || op == ">" || op == ">=") return 4;
  if (op == "+" || op == "-") return 5;
  if (op == "*" || op == "/" || op == "%") return 6;
  return -1;
}

inline bool is_binary_punct(const std::string &text)
{
  return binary_precedence(text) >= 0;
}

inline Value Compiler::expression(const int min_precedence)
{
  Value left = primary();
  while (true) {
    if (peek().kind != TokKind::Punct || !is_binary_punct(peek().text)) {
      return left;
    }
    const int precedence = binary_precedence(peek().text);
    if (precedence < min_precedence) {
      return left;
    }
    const Token op_tok = peek();
    advance();
    Value right = expression(precedence + 1);
    left = binary(op_tok.text, left, right, op_tok);
    if (panic_) {
      return left;
    }
  }
}

/* ------------------------------------------------------------ primaries */

inline const Compiler::ExplicitRead *Compiler::find_explicit_read(const std::string &name)
{
  /* WR-31: the return type comes from the function name, because a single-pass compiler cannot
   * know an attribute's storage type. WR-35 fixes the table: `prim` means the face table on a
   * mesh and the spline table on curves - never the `Edge` domain (an edge has two endpoints,
   * which cannot express a polyline). */
  static const ExplicitRead table[] = {
      {"fpoint", ReadDomain::Point, Type::Float, false},
      {"ipoint", ReadDomain::Point, Type::Int, false},
      {"vpoint", ReadDomain::Point, Type::Vector, false},
      {"qpoint", ReadDomain::Point, Type::Quaternion, false},
      {"mpoint", ReadDomain::Point, Type::Matrix, false},
      {"fprim", ReadDomain::Prim, Type::Float, false},
      {"iprim", ReadDomain::Prim, Type::Int, false},
      {"vprim", ReadDomain::Prim, Type::Vector, false},
      {"qprim", ReadDomain::Prim, Type::Quaternion, false},
      {"mprim", ReadDomain::Prim, Type::Matrix, false},
      {"npoints", ReadDomain::Point, Type::Int, true},
      {"nprimitives", ReadDomain::Prim, Type::Int, true},
  };
  for (const ExplicitRead &entry : table) {
    if (name == entry.name) {
      return &entry;
    }
  }
  return nullptr;
}

inline int Compiler::explicit_slot(const int input,
                                   const std::string &name,
                                   const ReadDomain domain,
                                   const Component component,
                                   const Type type,
                                   const Token &tok)
{
  /* A read-only binding: it never becomes a writer, so a missing attribute stays missing
   * (WR-20: reading creates nothing). The key carries the table class *and* the component, because
   * `@n` (this pass's own component), `fpoint(0, "mesh", "n", i)` and `fpoint(0, "curve", "n", i)`
   * are three different bindings (`WR-18`). */
  const std::string key = std::string(domain == ReadDomain::Prim ? "p:" : "q:") +
                          component_name(component) + ":" + attr_key(input, name);
  const auto found = attr_index_.find(key);
  if (found != attr_index_.end()) {
    AttrBinding &binding = program_.attrs[found->second];
    if (binding.type != type) {
      error(tok.line,
            tok.column,
            "attribute '" + name + "' is already read as " + type_name(binding.type) +
                ", here as " + type_name(type));
    }
    return found->second;
  }
  AttrBinding binding;
  binding.name = name;
  binding.type = type;
  binding.input = input;
  binding.domain = domain;
  binding.component = component;
  binding.read = true;
  binding.reg = new_reg(type);
  program_.attrs.push_back(binding);
  const int slot = int(program_.attrs.size()) - 1;
  attr_index_[key] = slot;
  return slot;
}

/** `WR-36`: the channel functions. The type comes from the name, exactly like the explicit-index
 * reads - the compiler cannot know a channel's type, and `WR-39` says a mismatch reads as 0, so
 * nothing here has to reject a name that does not exist. */
inline const Compiler::ChannelRead *Compiler::find_channel_read(const std::string &name)
{
  static const ChannelRead table[] = {
      {"chf", Type::Float},
      {"chi", Type::Int},
      {"chv", Type::Vector},
      /* `WR-35`/`WR-36`: rotations and matrices are channels too. Nothing here is special-cased -
       * the binding carries the function's type (`channel_slot`) and the host hands over the value
       * the socket holds; a channel of another type reads as zero (`WR-39`). */
      {"chq", Type::Quaternion},
      {"chm", Type::Matrix},
  };
  for (const ChannelRead &entry : table) {
    if (name == entry.name) {
      return &entry;
    }
  }
  return nullptr;
}

inline int Compiler::channel_slot(const std::string &name, const Type type, const Token &tok)
{
  /* A channel is a value the host reads from the node's own sockets, so the binding is
   * host-supplied and read-only (`WR-36`: none of the three functions writes). The key carries the
   * function's type as well as the name: `chf("h")` and `chi("h")` are *two* bindings, because the
   * host hands them different values (`WR-39`: a float channel read as an int truncates). */
  const std::string key = "c:" + std::string(type_name(type)) + ":" + name;
  const auto found = attr_index_.find(key);
  if (found != attr_index_.end()) {
    return found->second;
  }
  AttrBinding binding;
  binding.name = name;
  binding.type = type;
  binding.read = true;
  binding.host_supplied = true;
  binding.channel = true;
  binding.reg = new_reg(type);
  program_.attrs.push_back(binding);
  const int slot = int(program_.attrs.size()) - 1;
  attr_index_[key] = slot;
  (void)tok;
  return slot;
}

inline Value Compiler::channel_read(const ChannelRead &read, const Token &name_tok)
{
  advance(); /* the function name */
  advance(); /* '(' */
  Value value;
  value.type = read.type;

  const Token name = peek();
  if (name.kind != TokKind::String) {
    error(name.line,
          name.column,
          std::string(read.name) +
              ": the channel name must be a string literal, e.g. " + read.name + "(\"height\")");
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  advance();
  if (!check_punct(")")) {
    /* Houdini accepts a second argument and reads 0; here the signature is exactly one literal, so
     * a second argument is a compile error (the stricter reading of `WR-36`). */
    error(peek().line,
          peek().column,
          std::string(read.name) + " takes exactly one argument: the channel name, e.g. " +
              read.name + "(\"height\")");
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  advance(); /* ')' */

  const int slot = channel_slot(name.text, read.type, name_tok);
  value.reg = program_.attrs[slot].reg;
  return value;
}

inline void Compiler::skip_to_call_end()
{
  int depth = 0;
  while (peek().kind != TokKind::End) {
    if (peek().kind == TokKind::Punct) {
      if (peek().text == "(") {
        ++depth;
      }
      else if (peek().text == ")") {
        advance();
        if (depth == 0) {
          return;
        }
        --depth;
        continue;
      }
      else if (peek().text == ";" && depth == 0) {
        return;
      }
    }
    advance();
  }
}

inline Value Compiler::explicit_read(const ExplicitRead &read, const Token &name_tok)
{
  advance(); /* the function name */
  advance(); /* '(' */
  Value value;
  value.type = read.type;

  /* Argument 1: the geometry stream. A literal, because the binding has to exist before the
   * code runs (WR-31). */
  const Token geo = peek();
  if (geo.kind != TokKind::Number) {
    error(geo.line,
          geo.column,
          std::string(read.name) +
              ": the first argument must be a literal stream number (0 = this stream, "
              "1..3 = Input 1..3) - WR-31");
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  advance();
  int input = int(geo.number);
  if (input < 0 || input > 3) {
    error(geo.line,
          geo.column,
          std::string(read.name) + ": the stream must be 0..3, got " + std::to_string(input) +
              " (WR-31)");
    input = 0;
  }

  /* Argument 2 (`WR-18`/`WR-19`): the component word. Both the count and the value forms take it,
   * and it is a *literal* word from the class's vocabulary - anything else is a compile error. */
  if (!check_punct(",")) {
    error(peek().line,
          peek().column,
          std::string(read.name) + " takes (stream, domain, ...): the domain names the component, " +
              component_word_hint(read.domain));
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  advance();
  const Token word = peek();
  Component component = Component::Pass;
  if (word.kind != TokKind::String) {
    error(word.line,
          word.column,
          std::string(read.name) + ": the domain must be a string literal, one of " +
              component_word_hint(read.domain));
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  if (!component_word(read.domain, word.text, component)) {
    error(word.line,
          word.column,
          std::string(read.name) + ": unknown domain \"" + word.text + "\" - this table takes " +
              component_word_hint(read.domain));
  }
  advance();

  if (read.count) {
    /* npoints(geo, domain) / nprimitives(geo, domain): the host fills the size of that table, so
     * this is a host-supplied binding instead of a per-element read. */
    if (!check_punct(")")) {
      error(peek().line,
            peek().column,
            std::string(read.name) + " takes two arguments - the stream and the domain (WR-18)");
      skip_to_call_end();
    }
    else {
      advance();
    }
    const std::string key = std::string("n:") +
                            (read.domain == ReadDomain::Prim ? "prims:" : "points:") +
                            component_name(component) + ":" + std::to_string(input);
    const auto found = attr_index_.find(key);
    if (found != attr_index_.end()) {
      value.reg = program_.attrs[found->second].reg;
      return value;
    }
    AttrBinding binding;
    binding.name = read.domain == ReadDomain::Prim ? "#nprimitives" : "#npoints";
    binding.type = Type::Int;
    binding.input = input;
    binding.domain = read.domain;
    binding.component = component;
    binding.count = true;
    binding.read = true;
    binding.host_supplied = true;
    binding.reg = new_reg(Type::Int);
    program_.attrs.push_back(binding);
    const int slot = int(program_.attrs.size()) - 1;
    attr_index_[key] = slot;
    value.reg = binding.reg;
    return value;
  }

  /* Argument 3: the attribute name, a literal string. */
  if (!check_punct(",")) {
    error(peek().line,
          peek().column,
          std::string(read.name) + " takes (stream, domain, \"name\", index) - WR-18");
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  advance();
  const Token name = peek();
  if (name.kind != TokKind::String) {
    error(name.line,
          name.column,
          std::string(read.name) +
              ": the attribute name must be a string literal, e.g. " + read.name +
              "(0, \"mesh\", \"P\", 0) - WR-18");
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  advance();

  /* Argument 4: the element index - any expression: this is the run-time part. */
  if (!check_punct(",")) {
    error(peek().line,
          peek().column,
          std::string(read.name) + " takes (stream, domain, \"name\", index) - WR-18");
    skip_to_call_end();
    value.reg = new_reg(read.type);
    return value;
  }
  advance();
  const Value index = expression();
  expect_punct(")", "expected ')' after the index");
  if (index.type == Type::Vector) {
    error(name_tok.line,
          name_tok.column,
          std::string(read.name) + ": the index must be a number, not a vector");
  }

  const int slot = explicit_slot(
      input, resolve_name(name.text), read.domain, component, read.type, name_tok);
  value.reg = new_reg(read.type);
  const int at = emit(Op::LoadAttrIndex, value.reg);
  program_.code[size_t(at)].a = uint8_t(index.reg);
  program_.code[size_t(at)].imm = slot;
  return value;
}

inline const Compiler::IndexedWrite *Compiler::find_indexed_write(const std::string &name)
{
  /* WR-33: the target stream is always the one being processed, and only the literal `0` is
   * accepted. The topology functions join this table as M5 lands them. */
  static const IndexedWrite table[] = {
      {"setpointattrib", ReadDomain::Point, IndexedWrite::Shape::Attrib},
      {"setprimattrib", ReadDomain::Prim, IndexedWrite::Shape::Attrib},
      {"addpoint", ReadDomain::Point, IndexedWrite::Shape::AddPoint},
      {"addprim", ReadDomain::Prim, IndexedWrite::Shape::AddPrim},
      {"removepoint", ReadDomain::Point, IndexedWrite::Shape::RemovePoint},
      {"removeprim", ReadDomain::Prim, IndexedWrite::Shape::RemovePrim},
  };
  for (const IndexedWrite &entry : table) {
    if (name == entry.name) {
      return &entry;
    }
  }
  return nullptr;
}

inline int Compiler::indexed_write_slot(const std::string &name,
                                        const ReadDomain domain,
                                        const Component component,
                                        const Type type,
                                        const Token &tok)
{
  /* Its own key: `@w` (the run-over domain) and `setpointattrib(0, "w", …)` (the point domain)
   * are different writes - when `Run Over` is not Points they are even different Blender
   * attributes - and two writes into the same table share one binding. `WR-22` adds the component
   * to that key for the same reason the reads carry it: the named component need not be this
   * pass's. */
  const std::string key = std::string("w:") + (domain == ReadDomain::Prim ? "p:" : "q:") +
                          component_name(component) + ":" + name;
  const auto found = attr_index_.find(key);
  if (found != attr_index_.end()) {
    AttrBinding &binding = program_.attrs[found->second];
    if (binding.type != type) {
      error(tok.line,
            tok.column,
            "attribute '" + name + "' is already written as " + type_name(binding.type) +
                ", here as " + type_name(type));
    }
    return found->second;
  }
  AttrBinding binding;
  binding.name = name;
  binding.type = type;
  binding.input = 0;
  binding.domain = domain;
  binding.component = component;
  binding.indexed_write = true;
  binding.write = true;
  binding.reg = new_reg(type);
  program_.attrs.push_back(binding);
  const int slot = int(program_.attrs.size()) - 1;
  attr_index_[key] = slot;
  return slot;
}

inline Value Compiler::indexed_write_call(const IndexedWrite &write, const Token &name_tok)
{
  advance(); /* the function name */
  advance(); /* '(' */
  Value result;
  result.type = Type::Float;
  result.reg = new_scalar();

  const auto bad_call = [&](const std::string &message) {
    error(peek().line, peek().column, std::string(write.name) + ": " + message);
    skip_to_call_end();
  };

  /* WR-33: only the literal `0` is a valid target stream. */
  const Token geo = peek();
  if (geo.kind != TokKind::Number || geo.number != 0.0) {
    error(geo.line,
          geo.column,
          std::string(write.name) +
              ": the first argument must be the literal 0 - these functions only write the "
              "stream being processed (WR-33)");
    skip_to_call_end();
    return result;
  }
  advance();

  /* `WR-22`/`WR-23`: the two attribute setters take the domain word right after the stream, with
   * the same per-class vocabulary the reads use. The other indexed writes get their domain
   * arguments as their own slices land. */
  Component component = Component::Pass;
  if (write.shape == IndexedWrite::Shape::Attrib) {
    if (!check_punct(",")) {
      bad_call("takes (0, domain, \"name\", index, value) - WR-22");
      return result;
    }
    advance();
    const Token word = peek();
    if (word.kind != TokKind::String) {
      error(word.line,
            word.column,
            std::string(write.name) + ": the domain must be a string literal, one of " +
                component_word_hint(write.domain));
      skip_to_call_end();
      return result;
    }
    if (!component_word(write.domain, word.text, component)) {
      error(word.line,
            word.column,
            std::string(write.name) + ": unknown domain \"" + word.text + "\" - this table takes " +
                component_word_hint(write.domain));
    }
    advance();
  }

  if (write.shape == IndexedWrite::Shape::AddPoint) {
    /* WR-25: `addpoint(0, "mesh"|"pointcloud", pos)`. Both the domain and the position travel to
     * the host: the domain through the binding (the pre-checks read it before any writer exists),
     * the position through the op's register, because the register file is gone after the run. */
    if (!check_punct(",")) {
      bad_call("takes (0, \"mesh\"|\"pointcloud\", pos) - WR-25");
      return result;
    }
    advance();
    const Token domain = peek();
    bool cloud = false;
    if (domain.kind != TokKind::String) {
      error(domain.line, domain.column, "addpoint: the second argument must be \"mesh\" or \"pointcloud\" (WR-25)");
      skip_to_call_end();
      return result;
    }
    if (domain.text != "mesh" && domain.text != "pointcloud") {
      error(domain.line,
            domain.column,
            "addpoint: unknown domain \"" + domain.text +
                "\" - the point tables are \"mesh\" and \"pointcloud\" (WR-25)");
    }
    cloud = domain.text == "pointcloud";
    advance();
    if (!check_punct(",")) {
      bad_call("takes (0, \"mesh\"|\"pointcloud\", pos) - WR-25");
      return result;
    }
    advance();
    const Value position = expression();
    if (position.type != Type::Vector) {
      error(name_tok.line, name_tok.column, "addpoint: pos must be a vector (WR-25)");
    }
    expect_punct(")", "expected ')' after pos");

    AttrBinding binding;
    binding.name = cloud ? "#addpoint-cloud" : "#addpoint-mesh";
    binding.type = Type::Int;
    binding.domain = write.domain;
    binding.topology = AttrBinding::Topology::AddPoint;
    binding.addpoint_cloud = cloud;
    binding.reg = new_reg(Type::Int);
    program_.attrs.push_back(binding);
    const int slot = int(program_.attrs.size()) - 1;
    program_.creates_topology = true;
    result.type = Type::Int;
    result.reg = new_scalar();
    const int at = emit(Op::AddPoint, result.reg);
    program_.code[size_t(at)].a = uint8_t(position.reg);
    program_.code[size_t(at)].imm = cloud ? 1 : 0;
    (void)slot;
    return result;
  }

  if (write.shape == IndexedWrite::Shape::AddPrim) {
    /* `WR-27`: (0, domain, pointdomain, p0, p1, …). `domain` names the table the prim goes into -
     * a mesh face or a curve spline - and `pointdomain` the table the point numbers come from.
     * Both are literal words; `"mesh"` + `"mesh"` reuses the vertices, every other combination
     * copies the source points. */
    const auto parse_word = [&](const ReadDomain domain,
                                const char *hint,
                                Component &out) -> bool {
      if (!check_punct(",")) {
        bad_call(std::string("takes (0, domain, pointdomain, p0, p1, ...) - WR-27"));
        return false;
      }
      advance();
      const Token word = peek();
      if (word.kind != TokKind::String) {
        error(word.line,
              word.column,
              std::string(write.name) + ": " + hint + " must be a string literal, one of " +
                  component_word_hint(domain));
        skip_to_call_end();
        return false;
      }
      if (!component_word(domain, word.text, out)) {
        error(word.line,
              word.column,
              std::string(write.name) + ": unknown " + hint + " \"" + word.text +
                  "\" - this table takes " + component_word_hint(domain));
      }
      advance();
      return true;
    };
    Component target = Component::Pass;
    Component source = Component::Pass;
    if (!parse_word(ReadDomain::Prim, "domain", target)) {
      return result;
    }
    if (!parse_word(ReadDomain::Point, "pointdomain", source)) {
      return result;
    }
    if (!check_punct(",")) {
      bad_call("addprim needs at least one point number (WR-27)");
      return result;
    }
    std::vector<Value> points;
    while (check_punct(",")) {
      advance();
      points.push_back(expression());
    }
    expect_punct(")", "expected ')' after the point numbers");

    /* The point numbers go into consecutive registers: allocate them first (the expressions have
     * already run, so nothing can slip in between) and copy each value in. */
    int first = 0;
    for (size_t i = 0; i < points.size(); i++) {
      const int reg = new_scalar();
      if (i == 0) {
        first = reg;
      }
      if (points[i].type == Type::Vector) {
        error(name_tok.line, name_tok.column, "addprim: a point number must be a number");
      }
      const int copy = emit(Op::CopyF, reg);
      program_.code[size_t(copy)].a = uint8_t(points[i].reg);
    }

    /* `WR-27`'s floors: a mesh face needs three points, a curve spline at least one (the argument
     * list already guarantees one). Both are known here, so they are compile errors. */
    const bool polyline = target == Component::Curve;
    if (target == Component::Mesh && points.size() < 3) {
      error(name_tok.line,
            name_tok.column,
            "addprim: a mesh face needs at least 3 points, got " + std::to_string(points.size()) +
                " (WR-27)");
    }

    AttrBinding binding;
    binding.name = polyline ? "#polyline" : "#poly";
    binding.type = Type::Int;
    binding.domain = ReadDomain::Prim;
    binding.component = target;
    binding.point_component = source;
    binding.topology = AttrBinding::Topology::AddPrim;
    binding.polyline = polyline;
    binding.argc = int(points.size());
    binding.reg = new_reg(Type::Int);
    program_.attrs.push_back(binding);
    const int slot = int(program_.attrs.size()) - 1;
    program_.creates_topology = true;
    result.type = Type::Int;
    result.reg = new_scalar();
    const int at = emit(Op::AddPrim, result.reg);
    program_.code[size_t(at)].a = uint8_t(first);
    program_.code[size_t(at)].b = uint8_t(points.size());
    program_.code[size_t(at)].imm = slot;
    return result;
  }

  if (write.shape == IndexedWrite::Shape::RemovePoint ||
      write.shape == IndexedWrite::Shape::RemovePrim)
  {
    /* `WR-28`/`WR-29`: (0, domain, index) - the domain names the component to remove from, with the
     * same per-class vocabulary the rest of the family uses. */
    const bool point = write.shape == IndexedWrite::Shape::RemovePoint;
    if (!check_punct(",")) {
      bad_call(point ? "removepoint takes (0, domain, index) - WR-28" :
                       "removeprim takes (0, domain, index) - WR-29");
      return result;
    }
    advance();
    const Token word = peek();
    if (word.kind != TokKind::String) {
      error(word.line,
            word.column,
            std::string(write.name) + ": the domain must be a string literal, one of " +
                component_word_hint(write.domain));
      skip_to_call_end();
      return result;
    }
    if (!component_word(write.domain, word.text, component)) {
      error(word.line,
            word.column,
            std::string(write.name) + ": unknown domain \"" + word.text + "\" - this table takes " +
                component_word_hint(write.domain));
    }
    advance();
    if (!check_punct(",")) {
      bad_call(point ? "removepoint takes (0, domain, index) - WR-28" :
                       "removeprim takes (0, domain, index) - WR-29");
      return result;
    }
    advance();
    const Value index = expression();
    if (check_punct(",")) {
      bad_call("exactly three arguments, and no fourth one (WR-28/WR-29)");
      return result;
    }
    expect_punct(")", "expected ')' after the index");
    if (index.type == Type::Vector) {
      error(name_tok.line, name_tok.column, std::string(write.name) + ": the index must be a number");
    }

    AttrBinding binding;
    binding.name = point ? "#removepoint" : "#removeprim";
    binding.type = Type::Int;
    binding.domain = write.domain;
    binding.component = component;
    binding.topology = point ? AttrBinding::Topology::RemovePoint :
                               AttrBinding::Topology::RemovePrim;
    binding.reg = new_reg(Type::Int);
    program_.attrs.push_back(binding);
    const int slot = int(program_.attrs.size()) - 1;
    program_.creates_topology = true;
    const int at = emit(point ? Op::RemovePoint : Op::RemovePrim, 0);
    program_.code[size_t(at)].a = uint8_t(index.reg);
    program_.code[size_t(at)].imm = slot;
    return result;
  }

  if (!check_punct(",")) {
    bad_call("takes (0, domain, \"name\", index, value) - WR-22");
    return result;
  }
  advance();
  const Token name = peek();
  if (name.kind != TokKind::String) {
    error(name.line,
          name.column,
          std::string(write.name) + ": the attribute name must be a string literal, e.g. " +
              write.name + "(0, \"weight\", 3, 0.5)");
    skip_to_call_end();
    return result;
  }
  advance();

  if (!check_punct(",")) {
    bad_call("takes (0, domain, \"name\", index, value) - WR-22");
    return result;
  }
  advance();
  const Value index = expression();
  if (index.type == Type::Vector) {
    error(name_tok.line,
          name_tok.column,
          std::string(write.name) + ": the index must be a number, not a vector");
  }

  if (!check_punct(",")) {
    bad_call("takes (0, domain, \"name\", index, value) - WR-22");
    return result;
  }
  advance();
  const Value value = expression();
  expect_punct(")", "expected ')' after the value");

  const int slot = indexed_write_slot(
      resolve_name(name.text), write.domain, component, value.type, name_tok);
  const int at = emit(Op::StoreAttrIndex, 0);
  program_.code[size_t(at)].a = uint8_t(index.reg);
  program_.code[size_t(at)].b = uint8_t(value.reg);
  program_.code[size_t(at)].imm = slot;
  return result;
}

inline Value Compiler::primary()
{
  const Token &tok = peek();
  if (tok.kind == TokKind::Number) {
    advance();
    Value value;
    value.type = Type::Float;
    value.reg = new_scalar();
    const int at = emit(Op::LoadF, value.reg);
    set_fimm(at, float(tok.number));
    return value;
  }
  if (tok.kind == TokKind::Attr) {
    advance();
    return postfix(resolve_attribute(tok, false), tok);
  }
  if (tok.kind == TokKind::Ident) {
    if (peek(1).kind == TokKind::Punct && peek(1).text == "(") {
      /* WR-31: these take a literal stream number and a literal attribute name, so they cannot
       * go through the generic argument parsing - a string is not an expression. */
      if (const ExplicitRead *read = find_explicit_read(tok.text)) {
        return postfix(explicit_read(*read, tok), tok);
      }
      /* WR-33: the write functions have the same "literals in the head" shape. */
      if (const IndexedWrite *write = find_indexed_write(tok.text)) {
        return postfix(indexed_write_call(*write, tok), tok);
      }
      /* WR-36: `chf` / `chi` / `chv` read a channel of *this node*, named by a literal. */
      if (const ChannelRead *channel = find_channel_read(tok.text)) {
        return postfix(channel_read(*channel, tok), tok);
      }
      advance();
      advance();
      std::vector<Value> args;
      std::vector<OutArg> out_args;
      parse_arguments(&args, &out_args, &tok.text);
      expect_punct(")", "expected ')' after the arguments");
      return postfix(call_function(tok, args, out_args), tok);
    }
    advance();
    if (Local *local = find_local(tok.text)) {
      Value value;
      value.type = local->type;
      value.reg = local->reg;
      return postfix(value, tok);
    }
    error(tok.line,
          tok.column,
          "unknown name '" + tok.text + "' (attributes are written @name)");
    Value value;
    value.type = Type::Float;
    value.reg = new_scalar();
    return value;
  }
  if (tok.kind == TokKind::Punct && tok.text == "(") {
    advance();
    Value inner = expression();
    expect_punct(")", "expected ')'");
    return postfix(inner, tok);
  }
  if (tok.kind == TokKind::Punct && tok.text == "{") {
    advance();
    std::vector<Value> parts;
    while (true) {
      parts.push_back(cast_to(expression(), Type::Float));
      if (!accept_punct(",")) {
        break;
      }
    }
    expect_punct("}", "expected '}' to close the vector literal");
    Value value;
    value.type = Type::Vector;
    value.reg = new_vector();
    const int zero = new_scalar();
    const int zero_at = emit(Op::LoadF, zero);
    set_fimm(zero_at, 0.0f);
    const int px = parts.size() > 0 ? parts[0].reg : zero;
    const int py = parts.size() > 1 ? parts[1].reg : zero;
    const int pz = parts.size() > 2 ? parts[2].reg : zero;
    if (parts.size() > 3) {
      error(tok.line, tok.column, "a vector literal takes 1 to 3 components");
    }
    emit(Op::MakeV, value.reg, px, py, pz);
    return postfix(value, tok);
  }
  if (tok.kind == TokKind::Punct && (tok.text == "-" || tok.text == "!")) {
    advance();
    Value operand = primary();
    return unary(tok.text, operand, tok);
  }
  error(tok.line, tok.column, "expected an expression");
  panic_ = true;
  Value value;
  value.type = Type::Float;
  value.reg = new_scalar();
  return value;
}

/** Reads one scalar component out of a rotation or matrix value (`WR-47`, `WR-53`). */
inline int component_index(const std::string &name)
{
  if (name == "x" || name == "r") return 0;
  if (name == "y" || name == "g") return 1;
  if (name == "z" || name == "b") return 2;
  return -1;
}

/** `WR-47`: `math::Quaternion` is w, x, y, z - and the attribute is stored in that order. */
inline int quaternion_component_index(const std::string &name)
{
  if (name == "w") return 0;
  if (name == "x") return 1;
  if (name == "y") return 2;
  if (name == "z") return 3;
  return -1;
}

inline int Compiler::literal_word_index(const int argument,
                                        std::initializer_list<const char *> words,
                                        const char *label)
{
  const auto fail = [&](const std::string &message) {
    error(call_word_line_, call_word_column_, message);
    return -1;
  };
  if (argument >= int(call_words_.size()) || !call_words_[size_t(argument)].valid) {
    return fail(std::string("this function needs a '") + label + "' word");
  }
  const std::string &text = call_words_[size_t(argument)].text;
  int index = 0;
  for (const char *word : words) {
    if (text == word) {
      return index;
    }
    index++;
  }
  std::string allowed;
  for (const char *word : words) {
    if (!allowed.empty()) {
      allowed += ", ";
    }
    allowed += std::string("\"") + word + "\"";
  }
  return fail("'" + text + "' is not a valid " + label + "; use one of " + allowed);
}

inline void Compiler::parse_arguments(std::vector<Value> *args,
                                      std::vector<OutArg> *out_args,
                                      const std::string *call_name)
{
  if (check_punct(")")) {
    return;
  }
  /* `WR-43` / `WR-50` / `WR-52`: these take *button* words - string literals - in fixed
   * positions. A literal is not an expression, so it is consumed here and kept for
   * `literal_word_index()` to validate against the node's menu. */
  static const struct {
    const char *name;
    int words;
    int args;
  } kButtonWords[] = {
      {"rotate_rotation", 1, 3},
      {"axes_rotation", 2, 4},
      {"align_rotation_to_vector", 2, 5},
  };
  int word_count = 0;
  int declared_arguments = -1;
  if (call_name != nullptr) {
    for (const auto &entry : kButtonWords) {
      if (*call_name == entry.name) {
        word_count = entry.words;
        declared_arguments = entry.args;
      }
    }
  }
  if (word_count > 0) {
    call_words_.assign(size_t(word_count), CallWord{});
  }
  int argument = 0;
  while (true) {
    const bool word_slot = argument < word_count && argument < declared_arguments;
    if (word_slot && peek().kind == TokKind::String) {
      const Token &word = peek();
      advance();
      call_words_[size_t(argument)].valid = true;
      call_words_[size_t(argument)].text = word.text;
      call_word_line_ = word.line;
      call_word_column_ = word.column;
      if (args != nullptr) {
        args->push_back(Value{Type::Float, new_scalar()});
        if (out_args != nullptr) {
          out_args->push_back(OutArg{});
        }
      }
      argument++;
      if (!accept_punct(",")) {
        break;
      }
      continue;
    }
    if (out_args != nullptr && check_punct("&")) {
      const Token amp = peek();
      advance();
      const Token &place = peek();
      if (place.kind != TokKind::Ident && place.kind != TokKind::Attr) {
        error(amp.line, amp.column, "expected a variable or @attribute after '&'");
        panic_ = true;
        break;
      }
      advance();
      OutArg out;
      out.name = place.text;
      if (place.kind == TokKind::Attr) {
        const Value value = resolve_attribute(place, true);
        std::string stripped;
        const int input = split_input(place, stripped);
        const std::string mapped = aliases_.count(stripped) ? aliases_[stripped] : stripped;
        const std::string key = attr_key(input < 0 ? 0 : input, mapped);
        out.valid = true;
        out.is_attribute = true;
        out.reg = value.reg;
        out.type = value.type;
        out.slot = attr_index_.count(key) ? attr_index_[key] : -1;
      }
      else if (Local *local = find_local(place.text)) {
        out.valid = true;
        out.is_attribute = false;
        out.reg = local->reg;
        out.type = local->type;
      }
      else {
        error(place.line,
              place.column,
              "unknown name '" + place.text + "' (declare it first, e.g. float " + place.text +
                  ";)");
      }
      if (args != nullptr) {
        args->push_back(Value{out.type, out.reg});
      }
      out_args->push_back(std::move(out));
    }
    else if (args != nullptr) {
      args->push_back(expression());
      if (out_args != nullptr) {
        out_args->push_back(OutArg{});
      }
    }
    else {
      /* A statement level call: the value is discarded. */
      (void)expression();
    }
    argument++;
    if (!accept_punct(",")) {
      break;
    }
  }
}

inline Value Compiler::read_component(const Value value,
                                      const int element,
                                      const Token &at,
                                      const char *what)
{
  (void)at;
  (void)what;
  Value out{Type::Float, new_scalar()};
  const int instruction = emit(Op::TGet, out.reg, value.reg);
  set_imm(instruction, element_pack(value.type, element));
  return out;
}

inline Value Compiler::postfix(Value value, const Token &start)
{
  while (true) {
    if (check_punct(".")) {
      const Token dot = peek();
      advance();
      const Token &member = peek();
      if (member.kind != TokKind::Ident) {
        error(dot.line, dot.column, "expected a component name after '.'");
        return value;
      }
      advance();
      if (value.type == Type::Quaternion) {
        /* `WR-47`: a rotation is w, x, y, z - the order `math::Quaternion` stores them in. */
        const int index = quaternion_component_index(member.text);
        if (index < 0) {
          error(member.line, member.column, "unknown component '." + member.text + "'");
          return value;
        }
        value = read_component(value, index, member, "quaternion");
        continue;
      }
      if (value.type == Type::Matrix) {
        error(member.line, member.column, "a matrix has no named components; use m[column][row]");
        return value;
      }
      const int index = component_index(member.text);
      if (index < 0) {
        error(member.line, member.column, "unknown component '." + member.text + "'");
        return value;
      }
      if (value.type != Type::Vector) {
        error(dot.line, dot.column, "'.' needs a vector");
        return value;
      }
      Value out;
      out.type = Type::Float;
      out.reg = new_scalar();
      const int at = emit(Op::VGet, out.reg, value.reg);
      set_imm(at, index);
      value = out;
      continue;
    }
    if (check_punct("[")) {
      const Token bracket = peek();
      advance();
      int index = -1;
      if (peek().kind == TokKind::Number) {
        index = int(peek().number);
        advance();
      }
      else {
        error_here("a component index must be a constant whole number");
      }
      if (value.type == Type::Matrix) {
        /* Two subscripts address one entry: **`m[row][column]`**, which is how a matrix is written
         * on paper and what makes the translation read `m[0][3]`, `m[1][3]`, `m[2][3]`. The storage
         * is column-major (`float4x4`), so a row index is a *stride-1* step inside a column and the
         * flat entry is `row + column * 4` - not `column + row * 4`, which silently reads the
         * transposed entry (measured: `m[0][3]` came back as element 3 instead of the translation).
         *
         * One subscript is a *column*: `m[i]` is column `i`'s first three entries, matching
         * Blender's own `float4x4[i]` and keeping the translation readable as `m[3]`. That is
         * deliberately not the same axis as the first of two subscripts - a 4x4 column is the
         * meaningful thing to hand back, and this language has no float4 for a row.
         *
         * The first `]` is consumed before the second subscript is looked for: the second one
         * *follows* it, so testing for `[` first would always miss. */
        if (index < 0 || index > 3) {
          error(bracket.line, bracket.column, "a matrix row index must be 0 to 3");
          index = 0;
        }
        expect_punct("]", "expected ']' after the matrix row index");
        if (check_punct("[")) {
          const Token column_bracket = peek();
          advance(); /* '[' */
          int column = -1;
          if (peek().kind == TokKind::Number) {
            column = int(peek().number);
            advance();
          }
          else {
            error_here("a matrix column index must be a constant whole number");
          }
          expect_punct("]", "expected ']' after the matrix column index");
          if (column < 0 || column > 3) {
            error(column_bracket.line,
                  column_bracket.column,
                  "a matrix column index must be 0 to 3");
            column = 0;
          }
          value = read_component(value, index + column * 4, column_bracket, "matrix");
          continue;
        }
        Value out{Type::Vector, new_vector()};
        const int column_at = emit(Op::MatrixColumn, out.reg, value.reg);
        set_imm(column_at, index);
        value = out;
        continue;
      }
      expect_punct("]", "expected ']'");
      if (value.type == Type::Quaternion) {
        if (index < 0 || index > 3) {
          error(bracket.line, bracket.column, "a quaternion index must be 0 (w) to 3 (z)");
          index = 0;
        }
        value = read_component(value, index, bracket, "quaternion");
        continue;
      }
      if (value.type != Type::Vector) {
        error(bracket.line, bracket.column, "'[]' needs a vector");
        return value;
      }
      if (index < 0 || index > 2) {
        error(bracket.line, bracket.column, "a component index must be 0, 1 or 2");
        index = 0;
      }
      Value out;
      out.type = Type::Float;
      out.reg = new_scalar();
      const int at = emit(Op::VGet, out.reg, value.reg);
      set_imm(at, index);
      value = out;
      continue;
    }
    (void)start;
    return value;
  }
}

/* -------------------------------------------------------------- calls */

inline Value Compiler::call_function(const Token &name_tok,
                                     std::vector<Value> &args,
                                     std::vector<OutArg> &out_args)
{
  const std::string &name = name_tok.text;
  const auto report = [&](std::string message) {
    error(name_tok.line, name_tok.column, std::move(message));
  };
  const auto arity = [&](const int expected) {
    if (int(args.size()) != expected) {
      report("'" + name + "' takes " + std::to_string(expected) + " argument(s), got " +
             std::to_string(args.size()));
      return false;
    }
    return true;
  };
  const auto fallback = [&]() {
    return Value{Type::Float, new_scalar()};
  };

  /* casts */
  if (name == "float" || name == "int" || name == "vector") {
    const Type target =
        name == "vector" ? Type::Vector : (name == "int" ? Type::Int : Type::Float);
    if (target == Type::Vector) {
      if (!arity(3) && !arity(1)) {
        return fallback();
      }
      std::vector<Value> parts;
      for (Value &argument : args) {
        parts.push_back(cast_to(argument, Type::Float));
      }
      Value out{Type::Vector, new_vector()};
      const int zero = new_scalar();
      set_fimm(emit(Op::LoadF, zero), 0.0f);
      while (parts.size() < 3) {
        parts.push_back(Value{Type::Float, zero});
      }
      emit(Op::MakeV, out.reg, parts[0].reg, parts[1].reg, parts[2].reg);
      return out;
    }
    if (!arity(1)) {
      return fallback();
    }
    return cast_to(args[0], target);
  }

  /* ------------------------------------------------------------------ */
  /* `WR-43` … `WR-58`: the rotation and matrix functions.                */
  /*                                                                     */
  /* Each one stands for a Blender *function node*: the function carries  */
  /* the node's meaning, and its arguments are the node's sockets in      */
  /* order, with the node's buttons first. This language has no           */
  /* overloading, so one name means one signature - a wrong argument      */
  /* count or type is a compile error here, never a silent 0 later.       */
  /* ------------------------------------------------------------------ */
  {
    static const std::unordered_set<std::string> kRotationFunctionNames = {
        "rotate_rotation",          "rotate_vector",     "axis_angle_rotation",
        "euler_rotation",           "quaternion",        "to_euler",
        "to_axis_angle",            "axes_rotation",     "invert_rotation",
        "align_rotation_to_vector", "combine_transform", "matrix_multiply",
        "invert_matrix",            "transpose_matrix",  "matrix_determinant",
        "separate_transform",
    };
    if (kRotationFunctionNames.count(name) != 0) {
      const auto fail = [&]() {
        return Value{Type::Float, new_scalar()};
      };
      const auto type_error = [&](const Type want_type, const int index, const Type got) {
        report("'" + name + "' argument " + std::to_string(index + 1) + " must be " +
               type_name(want_type) + ", got " + type_name(got));
      };
      const auto want = [&](const int index, const Type want_type) {
        if (index >= int(args.size())) {
          return false;
        }
        if (args[size_t(index)].type != want_type) {
          type_error(want_type, index, args[size_t(index)].type);
          return false;
        }
        return true;
      };
      const auto out = [&](const int index, const Type want_type) -> OutArg * {
        if (index >= int(out_args.size()) || !out_args[size_t(index)].valid) {
          report("'" + name + "' argument " + std::to_string(index + 1) +
                 " must be an out parameter, written '&name'");
          return nullptr;
        }
        OutArg &slot = out_args[size_t(index)];
        if (slot.component >= 0) {
          report("'" + name + "' cannot write back into a single component; pass the whole place");
          return nullptr;
        }
        if (slot.type != want_type) {
          report("'" + name + "' writes back a " + type_name(want_type) + ", but '" + slot.name +
                 "' is " + type_name(slot.type));
          return nullptr;
        }
        return &slot;
      };

      if (name == "quaternion") {
        if (!arity(4)) {
          return fail();
        }
        for (int i = 0; i < 4; i++) {
          if (!is_scalar(args[size_t(i)].type)) {
            type_error(Type::Float, i, args[size_t(i)].type);
            return fail();
          }
        }
        Value parts[4] = {cast_to(args[0], Type::Float),
                          cast_to(args[1], Type::Float),
                          cast_to(args[2], Type::Float),
                          cast_to(args[3], Type::Float)};
        Value result{Type::Quaternion, new_quat()};
        emit(Op::QuatFromComponents,
             result.reg,
             parts[0].reg,
             parts[1].reg,
             parts[2].reg,
             parts[3].reg);
        return result;
      }
      if (name == "euler_rotation") {
        if (!arity(1) || !want(0, Type::Vector)) {
          return fail();
        }
        Value result{Type::Quaternion, new_quat()};
        emit(Op::QuatFromEuler, result.reg, args[0].reg);
        return result;
      }
      if (name == "axis_angle_rotation") {
        if (!arity(2) || !want(0, Type::Vector) || !want(1, Type::Float)) {
          return fail();
        }
        Value result{Type::Quaternion, new_quat()};
        emit(Op::QuatFromAxisAngle, result.reg, args[0].reg, args[1].reg);
        return result;
      }
      if (name == "rotate_vector") {
        if (!arity(2) || !want(0, Type::Vector) || !want(1, Type::Quaternion)) {
          return fail();
        }
        Value result{Type::Vector, new_vector()};
        emit(Op::QuatRotateV, result.reg, args[1].reg, args[0].reg);
        return result;
      }
      if (name == "rotate_rotation") {
        /* `WR-43`: `(space, base, by)` - the Space button leads, as it is drawn above the
         * sockets. Global is `by * base`, local is `base * by`; the VM picks with `imm`. */
        if (!arity(3)) {
          return fail();
        }
        const int space = literal_word_index(0, {"global", "local"}, "space");
        if (space < 0 || !want(1, Type::Quaternion) || !want(2, Type::Quaternion)) {
          return fail();
        }
        Value result{Type::Quaternion, new_quat()};
        const int at = emit(Op::QuatMultiply, result.reg, args[1].reg, args[2].reg);
        set_imm(at, space);
        return result;
      }
      if (name == "to_euler") {
        if (!arity(1) || !want(0, Type::Quaternion)) {
          return fail();
        }
        Value result{Type::Vector, new_vector()};
        emit(Op::QuatToEuler, result.reg, args[0].reg);
        return result;
      }
      if (name == "to_axis_angle") {
        /* `WR-49`: the two outputs are out parameters, in the node's order. */
        if (!arity(3) || !want(0, Type::Quaternion)) {
          return fail();
        }
        OutArg *axis = out(1, Type::Vector);
        OutArg *angle = out(2, Type::Float);
        if (axis == nullptr || angle == nullptr) {
          return fail();
        }
        emit(Op::QuatToAxisAngle, axis->reg, args[0].reg, angle->reg);
        return fail();
      }
      if (name == "invert_rotation") {
        if (!arity(1) || !want(0, Type::Quaternion)) {
          return fail();
        }
        Value result{Type::Quaternion, new_quat()};
        emit(Op::QuatInvert, result.reg, args[0].reg);
        return result;
      }
      if (name == "axes_rotation") {
        /* `WR-50`: `(primary_axis, secondary_axis, primary, secondary)`; both buttons lead and
         * the node offers X/Y/Z only. The two axis words ride in `imm`. */
        if (!arity(4)) {
          return fail();
        }
        const int primary_axis = literal_word_index(0, {"x", "y", "z"}, "primary_axis");
        const int secondary_axis = literal_word_index(1, {"x", "y", "z"}, "secondary_axis");
        if (primary_axis < 0 || secondary_axis < 0 || !want(2, Type::Vector) ||
            !want(3, Type::Vector))
        {
          return fail();
        }
        Value result{Type::Quaternion, new_quat()};
        const int at = emit(Op::QuatAxes, result.reg, args[2].reg, args[3].reg);
        set_imm(at, primary_axis * 3 + secondary_axis);
        return result;
      }
      if (name == "align_rotation_to_vector") {
        /* `WR-52`: the Axis and Pivot buttons lead, then Rotation / Factor / Vector. */
        if (!arity(5)) {
          return fail();
        }
        const int axis = literal_word_index(0, {"x", "y", "z"}, "axis");
        const int pivot = literal_word_index(1, {"auto", "x", "y", "z"}, "pivot");
        if (axis < 0 || pivot < 0 || !want(2, Type::Quaternion) || !want(3, Type::Float) ||
            !want(4, Type::Vector))
        {
          return fail();
        }
        const Value factor = cast_to(args[3], Type::Float);
        Value result{Type::Quaternion, new_quat()};
        const int at = emit(Op::QuatAlignVector, result.reg, args[2].reg, factor.reg, args[4].reg);
        set_imm(at, axis * 4 + pivot);
        return result;
      }
      if (name == "combine_transform") {
        /* `WR-53`: `(translation, rotation, scale)` - the three sockets, in order. */
        if (!arity(3) || !want(0, Type::Vector) || !want(1, Type::Quaternion) ||
            !want(2, Type::Vector))
        {
          return fail();
        }
        Value result{Type::Matrix, new_matrix()};
        emit(Op::CombineTransform, result.reg, args[0].reg, args[1].reg, args[2].reg);
        return result;
      }
      if (name == "matrix_multiply") {
        /* `WR-54`: the node returns `second × first` under Blender's row-vector convention, which
         * is `first × second` under the column-vector convention everything else here uses (and
         * which `float4x4 * float3` in Blender also means). Measured against the node: the call's
         * product transforms a point as `first(second(v))`, so the later argument applies first.
         * That is one ordinary matrix product, in argument order. */
        if (!arity(2) || !want(0, Type::Matrix) || !want(1, Type::Matrix)) {
          return fail();
        }
        Value result{Type::Matrix, new_matrix()};
        emit(Op::MatrixMultiply, result.reg, args[0].reg, args[1].reg);
        return result;
      }
      if (name == "invert_matrix") {
        if (!arity(1) || !want(0, Type::Matrix)) {
          return fail();
        }
        Value result{Type::Matrix, new_matrix()};
        emit(Op::InvertMatrix, result.reg, args[0].reg);
        return result;
      }
      if (name == "transpose_matrix") {
        if (!arity(1) || !want(0, Type::Matrix)) {
          return fail();
        }
        Value result{Type::Matrix, new_matrix()};
        emit(Op::TransposeMatrix, result.reg, args[0].reg);
        return result;
      }
      if (name == "matrix_determinant") {
        if (!arity(1) || !want(0, Type::Matrix)) {
          return fail();
        }
        Value result{Type::Float, new_scalar()};
        emit(Op::MatrixDeterminant, result.reg, args[0].reg);
        return result;
      }
      if (name == "separate_transform") {
        /* `WR-58`: `(matrix, &translation, &rotation, &scale)`, the node's output order. */
        if (!arity(4) || !want(0, Type::Matrix)) {
          return fail();
        }
        OutArg *translation = out(1, Type::Vector);
        OutArg *rotation = out(2, Type::Quaternion);
        OutArg *scale = out(3, Type::Vector);
        if (translation == nullptr || rotation == nullptr || scale == nullptr) {
          return fail();
        }
        emit(Op::SeparateTransform, translation->reg, args[0].reg, rotation->reg, scale->reg);
        return fail();
      }
    }
  }

  /* vector specific */
  if (name == "length" || name == "normalize" || name == "lensq") {
    if (!arity(1)) {
      return fallback();
    }
    if (args[0].type != Type::Vector) {
      report("'" + name + "' needs a vector");
      return fallback();
    }
    if (name == "normalize") {
      Value out{Type::Vector, new_vector()};
      emit(Op::NormalizeV, out.reg, args[0].reg);
      return out;
    }
    Value out{Type::Float, new_scalar()};
    emit(name == "length" ? Op::LenV : Op::LenSqV, out.reg, args[0].reg);
    return out;
  }
  if (name == "distance" || name == "dot" || name == "cross") {
    if (!arity(2)) {
      return fallback();
    }
    if (args[0].type != Type::Vector || args[1].type != Type::Vector) {
      report("'" + name + "' needs two vectors");
      return fallback();
    }
    if (name == "cross") {
      Value out{Type::Vector, new_vector()};
      emit(Op::CrossV, out.reg, args[0].reg, args[1].reg);
      return out;
    }
    Value out{Type::Float, new_scalar()};
    emit(name == "dot" ? Op::DotV : Op::DistV, out.reg, args[0].reg, args[1].reg);
    return out;
  }
  if (name == "rand" || name == "noise") {
    if (!arity(1)) {
      return fallback();
    }
    Value out{Type::Float, new_scalar()};
    if (args[0].type == Type::Vector) {
      emit(name == "noise" ? Op::NoiseV : Op::LenSqV, out.reg, args[0].reg);
      if (name == "rand") {
        /* rand(vector) seeds on the vector's squared length: deterministic, documented. */
        emit(Op::RandF, out.reg, out.reg);
      }
      return out;
    }
    Value seed = cast_to(args[0], Type::Float);
    emit(name == "noise" ? Op::NoiseF : Op::RandF, out.reg, seed.reg);
    return out;
  }
  /* fit(x, oldmin, oldmax, newmin, newmax): expanded, scalar only. */
  if (name == "fit") {
    if (!arity(5)) {
      return fallback();
    }
    for (Value &argument : args) {
      argument = cast_to(argument, Type::Float);
    }
    const float one_like = 0.0f;
    (void)one_like;
    Value span = binary("-", args[2], args[1], name_tok);
    Value t = binary("/", binary("-", args[0], args[1], name_tok), span, name_tok);
    Value out_span = binary("-", args[4], args[3], name_tok);
    return binary("+", binary("*", t, out_span, name_tok), args[3], name_tok);
  }

  /* vector-capable three argument functions */
  if (name == "lerp" || name == "clamp" || name == "smoothstep") {
    if (!arity(3)) {
      return fallback();
    }
    const bool vector_args = args[0].type == Type::Vector || args[1].type == Type::Vector ||
                             args[2].type == Type::Vector;
    if (!vector_args) {
      for (Value &argument : args) {
        argument = cast_to(argument, Type::Float);
      }
      Value out{Type::Float, new_scalar()};
      const int at = emit(Op::CallF3, out.reg, args[0].reg, args[1].reg, args[2].reg);
      set_imm(at, int(fn3_from_name(name)));
      return out;
    }
    /* Vector forms are expanded from the vector arithmetic ops:
     *   lerp(a, b, t)      -> a + (b - a) * t          (t scalar or vector)
     *   clamp(x, lo, hi)   -> min(max(x, lo), hi)
     *   smoothstep(e0, e1, x) -> t = clamp((x-e0)/(e1-e0), 0, 1); t*t*(3-2t)   [scalar t]
     */
    if (name == "lerp") {
      if (args[2].type == Type::Vector && args[0].type != Type::Vector) {
        report("'lerp' needs vector ends for a vector blend");
        return fallback();
      }
      args[0] = cast_to(args[0], Type::Vector);
      args[1] = cast_to(args[1], Type::Vector);
      Value delta = binary("-", args[1], args[0], name_tok);
      Value scaled = args[2].type == Type::Vector ? binary("*", delta, args[2], name_tok) :
                                                    binary("*", delta, args[2], name_tok);
      return binary("+", args[0], scaled, name_tok);
    }
    if (name == "clamp") {
      args[0] = cast_to(args[0], Type::Vector);
      for (int k = 1; k <= 2; k++) {
        args[k] = cast_to(args[k], args[k].type == Type::Vector ? Type::Vector : Type::Float);
        if (args[k].type == Type::Float) {
          Value splat{Type::Vector, new_vector()};
          emit(Op::MakeV, splat.reg, args[k].reg, args[k].reg, args[k].reg);
          args[k] = splat;
        }
      }
      Value lower{Type::Vector, new_vector()};
      const int at1 = emit(Op::MapV2, lower.reg, args[0].reg, args[1].reg);
      set_imm(at1, int(Fn2::Max));
      Value out{Type::Vector, new_vector()};
      const int at2 = emit(Op::MapV2, out.reg, lower.reg, args[2].reg);
      set_imm(at2, int(Fn2::Min));
      return out;
    }
    /* smoothstep on vectors: scalar edges, vector x. */
    if (args[2].type != Type::Vector) {
      report("'smoothstep' needs a vector x for vector edges");
      return fallback();
    }
    Value e0 = cast_to(args[0], Type::Vector);
    Value e1 = cast_to(args[1], Type::Vector);
    Value t = binary("/", binary("-", args[2], e0, name_tok),
                     binary("-", e1, e0, name_tok), name_tok);
    Value zero{Type::Float, new_scalar()};
    set_fimm(emit(Op::LoadF, zero.reg), 0.0f);
    Value one{Type::Float, new_scalar()};
    set_fimm(emit(Op::LoadF, one.reg), 1.0f);
    Value clamped = binary("+", zero, t, name_tok); /* copy so the next step can clamp */
    Value lower{Type::Vector, new_vector()};
    Value upper{Type::Vector, new_vector()};
    emit(Op::MakeV, lower.reg, zero.reg, zero.reg, zero.reg);
    emit(Op::MakeV, upper.reg, one.reg, one.reg, one.reg);
    Value c1{Type::Vector, new_vector()};
    set_imm(emit(Op::MapV2, c1.reg, clamped.reg, lower.reg), int(Fn2::Max));
    Value c2{Type::Vector, new_vector()};
    set_imm(emit(Op::MapV2, c2.reg, c1.reg, upper.reg), int(Fn2::Min));
    Value three{Type::Float, new_scalar()};
    set_fimm(emit(Op::LoadF, three.reg), 3.0f);
    Value two{Type::Float, new_scalar()};
    set_fimm(emit(Op::LoadF, two.reg), 2.0f);
    Value t2 = binary("*", c2, c2, name_tok);
    Value t3 = binary("*", t2, c2, name_tok);
    Value a = binary("*", t2, three, name_tok);
    Value b = binary("*", t3, two, name_tok);
    return binary("*", binary("-", a, b, name_tok), c2, name_tok);
  }

  /* table lookup */
  const std::vector<FunctionInfo> &table = function_table();
  const FunctionInfo *info = nullptr;
  for (const FunctionInfo &entry : table) {
    if (name == entry.name) {
      info = &entry;
      break;
    }
  }
  if (info == nullptr) {
    report("unknown function '" + name + "'");
    return fallback();
  }
  if (!arity(info->args)) {
    return fallback();
  }
  bool any_vector = false;
  for (const Value &argument : args) {
    if (argument.type == Type::Vector) {
      any_vector = true;
    }
  }
  if (any_vector) {
    if (!info->componentwise) {
      report("'" + name + "' does not take vectors");
      return fallback();
    }
    for (Value &argument : args) {
      argument = cast_to(argument, argument.type == Type::Vector ? Type::Vector : Type::Float);
    }
    Value out{Type::Vector, new_vector()};
    if (info->args == 1) {
      const int at = emit(Op::MapV1, out.reg, args[0].reg);
      set_imm(at, int(fn1_from_name(name)));
      return out;
    }
    /* `min`/`max` with one vector and one scalar: splat the scalar. */
    if (args[0].type == Type::Vector && args[1].type == Type::Float) {
      Value splat{Type::Vector, new_vector()};
      emit(Op::MakeV, splat.reg, args[1].reg, args[1].reg, args[1].reg);
      const int at = emit(Op::MapV2, out.reg, args[0].reg, splat.reg);
      set_imm(at, int(fn2_from_name(name)));
      return out;
    }
    if (args[1].type == Type::Vector && args[0].type == Type::Float) {
      Value splat{Type::Vector, new_vector()};
      emit(Op::MakeV, splat.reg, args[0].reg, args[0].reg, args[0].reg);
      const int at = emit(Op::MapV2, out.reg, splat.reg, args[1].reg);
      set_imm(at, int(fn2_from_name(name)));
      return out;
    }
    if (args[0].type == Type::Vector && args[1].type == Type::Vector) {
      const int at = emit(Op::MapV2, out.reg, args[0].reg, args[1].reg);
      set_imm(at, int(fn2_from_name(name)));
      return out;
    }
    report("'" + name + "' cannot mix vectors and scalars here");
    return fallback();
  }

  for (Value &argument : args) {
    argument = cast_to(argument, Type::Float);
  }
  Value out{Type::Float, new_scalar()};
  if (info->args == 1) {
    const int at = emit(Op::CallF1, out.reg, args[0].reg);
    set_imm(at, int(fn1_from_name(name)));
  }
  else if (info->args == 2) {
    const int at = emit(Op::CallF2, out.reg, args[0].reg, args[1].reg);
    set_imm(at, int(fn2_from_name(name)));
  }
  else {
    const int at = emit(Op::CallF3, out.reg, args[0].reg, args[1].reg, args[2].reg);
    set_imm(at, int(fn3_from_name(name)));
  }
  return out;
}

/* ----------------------------------------------------------- statements */

inline Compiler::LValue Compiler::parse_lvalue()
{
  LValue target;
  const Token &tok = peek();
  if (tok.kind == TokKind::Attr) {
    advance();
    const Value value = resolve_attribute(tok, true);
    target.valid = true;
    target.is_attribute = true;
    std::string stripped;
    const int input = split_input(tok, stripped);
    const std::string mapped = aliases_.count(stripped) ? aliases_[stripped] : stripped;
    const std::string key = attr_key(input < 0 ? 0 : input, mapped);
    target.slot = attr_index_.count(key) ? attr_index_[key] : -1;
    target.reg = value.reg;
    target.type = value.type;
    target.name = tok.text;
  }
  else if (tok.kind == TokKind::Ident) {
    advance();
    Local *local = find_local(tok.text);
    if (local == nullptr) {
      error(tok.line,
            tok.column,
            "unknown name '" + tok.text + "' (declare it first, e.g. float " + tok.text + ";)");
      return target;
    }
    target.valid = true;
    target.is_attribute = false;
    target.reg = local->reg;
    target.type = local->type;
    target.name = tok.text;
  }
  else {
    error(tok.line, tok.column, "expected an attribute or a variable to assign to");
    return target;
  }

  if (check_punct(".")) {
    const Token dot = peek();
    advance();
    const Token &member = peek();
    if (member.kind != TokKind::Ident) {
      error(dot.line, dot.column, "expected a component name after '.'");
      return target;
    }
    advance();
    if (target.type == Type::Matrix) {
      error(member.line, member.column, "a matrix has no named components; use m[row][column]");
      return target;
    }
    if (target.type == Type::Quaternion) {
      /* `WR-47`: a rotation is w, x, y, z - the order `math::Quaternion` stores them in. */
      const int index = quaternion_component_index(member.text);
      if (index < 0) {
        error(member.line, member.column, "unknown component '." + member.text + "'");
        return target;
      }
      target.component = index;
      target.element = index;
      target.name = target.name + "." + member.text;
      return target;
    }
    const int index = component_index(member.text);
    if (index < 0) {
      error(member.line, member.column, "unknown component '." + member.text + "'");
      return target;
    }
    if (target.type != Type::Vector) {
      error(dot.line, dot.column, "'" + target.name + "' is not a vector");
      return target;
    }
    target.component = index;
  }
  else if (check_punct("[")) {
    const Token bracket = peek();
    advance();
    int index = -1;
    if (peek().kind == TokKind::Number) {
      index = int(peek().number);
      advance();
    }
    else {
      error_here("a component index must be a constant whole number");
    }
    expect_punct("]", "expected ']'");
    if (target.type == Type::Quaternion) {
      if (index < 0 || index > 3) {
        error(bracket.line, bracket.column, "a quaternion index must be 0 (w) to 3 (z)");
        index = 0;
      }
      target.component = index;
      target.element = index;
      target.name = target.name + "[" + std::to_string(index) + "]";
      return target;
    }
    if (target.type == Type::Matrix) {
      /* `m[row][column]` for one entry (`m[0][3]` is the translation's x), `m[i]` alone for column
       * `i`'s first three entries - the same two forms `postfix` reads, and they have to agree or a
       * value read from one path would be written to a different entry. The entry is
       * `m[column + row * 4]` over the column-major storage. */
      if (index < 0 || index > 3) {
        error(bracket.line, bracket.column, "a matrix row index must be 0 to 3");
        index = 0;
      }
      target.component = index;
      if (check_punct("[")) {
        const Token column_bracket = peek();
        advance();
        int column = -1;
        if (peek().kind == TokKind::Number) {
          column = int(peek().number);
          advance();
        }
        else {
          error_here("a matrix column index must be a constant whole number");
        }
        expect_punct("]", "expected ']'");
        if (column < 0 || column > 3) {
          error(column_bracket.line,
                column_bracket.column,
                "a matrix column index must be 0 to 3");
          column = 0;
        }
        target.component = -1;
        target.element = index + column * 4;
        target.name = target.name + "[" + std::to_string(index) + "][" +
                      std::to_string(column) + "]";
        return target;
      }
      target.name = target.name + "[" + std::to_string(index) + "]";
      return target;
    }
    if (target.type != Type::Vector) {
      error(bracket.line, bracket.column, "'" + target.name + "' is not a vector");
      return target;
    }
    if (index < 0 || index > 2) {
      error(bracket.line, bracket.column, "a component index must be 0, 1 or 2");
      index = 0;
    }
    target.component = index;
  }
  return target;
}

inline void Compiler::store_lvalue(const LValue &target,
                                   const std::string &op,
                                   Value rhs,
                                   const Token &op_tok)
{
  if (!target.valid) {
    return;
  }
  if (target.is_attribute && op != "=" && target.slot >= 0 && target.slot < int(program_.attrs.size()))
  {
    /* A compound assignment needs the attribute's current value, so the host must load it
     * even if the program never reads it as an expression. */
    program_.attrs[target.slot].read = true;
  }
  if (target.component >= 0 && target.type == Type::Vector) {
    /* Read the current component, combine, write it back into the same vector register. */
    Value current{Type::Float, new_scalar()};
    const int at = emit(Op::VGet, current.reg, target.reg);
    set_imm(at, target.component);
    Value result;
    if (op == "=") {
      result = cast_to(rhs, Type::Float);
    }
    else {
      const std::string arith = op.substr(0, op.size() - 1);
      result = binary(arith, current, rhs, op_tok);
      result = cast_to(result, Type::Float);
    }
    const int set_at = emit(Op::VSet, target.reg, target.reg, result.reg);
    set_imm(set_at, target.component);
    return;
  }
  if (target.element >= 0 && (target.type == Type::Quaternion || target.type == Type::Matrix)) {
    /* `WR-47` / `WR-53`: one component of a rotation or a matrix. A compound assignment reads it
     * first, exactly like the vector case above. */
    Value current{Type::Float, new_scalar()};
    const int at = emit(Op::TGet, current.reg, target.reg);
    set_imm(at, element_pack(target.type, target.element));
    Value result;
    if (op == "=") {
      result = cast_to(rhs, Type::Float);
    }
    else {
      const std::string arith = op.substr(0, op.size() - 1);
      result = binary(arith, current, rhs, op_tok);
      result = cast_to(result, Type::Float);
    }
    const int set_at = emit(Op::TSet, target.reg, result.reg);
    set_imm(set_at, element_pack(target.type, target.element));
    return;
  }
  Value result;
  if (op == "=") {
    result = cast_to(rhs, target.type);
  }
  else {
    const std::string arith = op.substr(0, op.size() - 1);
    Value current;
    current.type = target.type;
    current.reg = target.reg;
    result = binary(arith, current, rhs, op_tok);
    result = cast_to(result, target.type);
  }
  if (target.type == Type::Int && result.type != Type::Int) {
    result = cast_to(result, Type::Int);
  }
  if (result.reg != target.reg || result.type != target.type) {
    emit(copy_op(target.type), target.reg, result.reg);
  }
}

inline void Compiler::assignment_or_call()
{
  /* An expression statement is either an assignment or a bare call. Both start with an
   * attribute, an identifier or a cast call. */
  const Token start = peek();
  if (start.kind == TokKind::Ident && peek(1).kind == TokKind::Punct && peek(1).text == "(") {
    /* WR-31/WR-33: the literal-head functions take a string argument, so a bare call statement
     * has to go through their own parsing - this path would treat `"name"` as an expression. */
    if (const IndexedWrite *write = find_indexed_write(start.text)) {
      (void)indexed_write_call(*write, start);
      return;
    }
    if (const ExplicitRead *read = find_explicit_read(start.text)) {
      (void)explicit_read(*read, start);
      return;
    }
    advance();
    advance();
    std::vector<Value> args;
    std::vector<OutArg> out_args;
    /* A bare call statement: the function's name still selects the signature, but there is no
     * position a bad button word could be reported at, so the argument words are not saved. */
    parse_arguments(&args, &out_args, &start.text);
    expect_punct(")", "expected ')' after the arguments");
    (void)call_function(start, args, out_args);
    return;
  }
  const LValue target = parse_lvalue();
  if (!target.valid || panic_) {
    /* Skip to the end of the statement so one bad line does not cascade. */
    while (!at_end() && !check_punct(";")) {
      advance();
    }
    return;
  }
  /* postfix `++` / `--`, the usual loop step */
  if (check_punct("++") || check_punct("--")) {
    const Token inc = peek();
    advance();
    Value one{Type::Float, new_scalar()};
    set_fimm(emit(Op::LoadF, one.reg), 1.0f);
    store_lvalue(target, inc.text == "++" ? "+=" : "-=", one, inc);
    return;
  }
  const Token &op_tok = peek();
  const bool is_assignment = op_tok.kind == TokKind::Punct &&
                             (op_tok.text == "=" || op_tok.text == "+=" || op_tok.text == "-=" ||
                              op_tok.text == "*=" || op_tok.text == "/=" || op_tok.text == "%=");
  if (!is_assignment) {
    error(op_tok.line,
          op_tok.column,
          "expected an assignment operator after '" + target.name + "'");
    panic_ = true;
    return;
  }
  advance();
  Value rhs = expression();
  store_lvalue(target, op_tok.text, rhs, op_tok);
}

inline void Compiler::declaration(const Token &type_tok)
{
  advance(); /* the type keyword */
  Type type = Type::Float;
  if (type_tok.text == "int") {
    type = Type::Int;
  }
  /* `WR-47` / `WR-53`: the rotation and matrix declarations. They are the attribute types of the
   * same name (`q@` / `m@`), so a value can move between a variable and an attribute unchanged.
   * The rotation type is `quat` - see the note on `enum class Type`. */
  else if (type_tok.text == "quat") {
    type = Type::Quaternion;
  }
  else if (type_tok.text == "matrix") {
    type = Type::Matrix;
  }
  else if (type_tok.text == "vector") {
    type = Type::Vector;
  }
  while (true) {
    const Token &name_tok = peek();
    if (name_tok.kind != TokKind::Ident) {
      error_here("expected a variable name");
      panic_ = true;
      return;
    }
    advance();
    if (find_local_here(name_tok.text) != nullptr) {
      error(name_tok.line, name_tok.column, "'" + name_tok.text + "' is already declared here");
    }
    Local local;
    local.type = type;
    local.reg = new_reg(type);
    /* A declared rotation or matrix starts out as the identity: an all-zero matrix is not a
     * meaningful default, and the identity is what "no transform" means everywhere else. */
    const int default_at = type == Type::Quaternion ?
                               emit(Op::QuatIdentity, local.reg) :
                               (type == Type::Matrix ?
                                    emit(Op::MatrixIdentity, local.reg) :
                                    emit(type == Type::Vector ? Op::MakeV : Op::LoadF, local.reg));
    if (type == Type::Vector) {
      const int zero = new_scalar();
      set_fimm(emit(Op::LoadF, zero), 0.0f);
      /* MakeV needs three operands; patch after the fact. */
      program_.code[default_at].a = uint8_t(zero);
      program_.code[default_at].b = uint8_t(zero);
      program_.code[default_at].c = uint8_t(zero);
    }
    else {
      set_fimm(default_at, 0.0f);
    }
    scopes_.back()[name_tok.text] = local;
    if (accept_punct("=")) {
      Value rhs = expression();
      Value result = cast_to(rhs, type);
      if (type == Type::Int && result.type != Type::Int) {
        result = cast_to(result, Type::Int);
      }
      if (result.reg != local.reg || result.type != type) {
        emit(copy_op(type), local.reg, result.reg);
      }
    }
    if (!accept_punct(",")) {
      break;
    }
  }
}

inline void Compiler::block()
{
  expect_punct("{", "expected '{'");
  push_scope();
  while (!at_end() && !check_punct("}")) {
    if (accept_punct(";")) {
      continue;
    }
    if (panic_) {
      /* resynchronize on ';' or '}' */
      while (!at_end() && !check_punct(";") && !check_punct("}")) {
        advance();
      }
      accept_punct(";");
      panic_ = false;
      continue;
    }
    statement();
    if (panic_) {
      while (!at_end() && !check_punct(";") && !check_punct("}")) {
        advance();
      }
      accept_punct(";");
      panic_ = false;
    }
  }
  expect_punct("}", "expected '}' to close the block");
  pop_scope();
}

inline void Compiler::if_statement()
{
  const Token if_tok = peek();
  advance();
  expect_punct("(", "expected '(' after 'if'");
  Value condition = cast_to(expression(), Type::Int);
  expect_punct(")", "expected ')' after the condition");
  const int jump_false = emit(Op::JmpIfZero, 0, condition.reg);
  statement();
  if (check_ident("else")) {
    advance();
    const int jump_end = emit(Op::Jmp);
    patch_jump(jump_false, here());
    statement();
    patch_jump(jump_end, here());
  }
  else {
    patch_jump(jump_false, here());
  }
  (void)if_tok;
}

inline void Compiler::for_statement()
{
  advance(); /* for */
  expect_punct("(", "expected '(' after 'for'");
  push_scope();
  /* init */
  if (check_punct(";")) {
    advance();
  }
  else if (check_ident("float") || check_ident("int") || check_ident("vector")) {
    declaration(peek());
    expect_punct(";", "expected ';' after the loop initializer");
  }
  else {
    assignment_or_call();
    expect_punct(";", "expected ';' after the loop initializer");
  }
  const int loop_start = here();
  int jump_false = -1;
  if (check_punct(";")) {
    advance();
  }
  else {
    Value condition = cast_to(expression(), Type::Int);
    expect_punct(";", "expected ';' after the loop condition");
    jump_false = emit(Op::JmpIfZero, 0, condition.reg);
  }
  /* step runs after the body: emit it after a jump over it. */
  const int jump_to_body = emit(Op::Jmp);
  const int step_start = here();
  if (!check_punct(")")) {
    assignment_or_call();
  }
  expect_punct(")", "expected ')' to close the loop header");
  emit(Op::Jmp, 0);
  patch_jump(program_.code.size() - 1, loop_start);
  const int body_start = here();
  patch_jump(jump_to_body, body_start);
  statement();
  emit(Op::Jmp, 0);
  patch_jump(program_.code.size() - 1, step_start);
  if (jump_false >= 0) {
    patch_jump(jump_false, here());
  }
  pop_scope();
}

inline void Compiler::statement()
{
  if (check_punct("{")) {
    block();
    return;
  }
  if (check_ident("if")) {
    if_statement();
    return;
  }
  if (check_ident("for")) {
    for_statement();
    return;
  }
  if ((check_ident("float") || check_ident("int") || check_ident("vector") ||
       check_ident("quat") || check_ident("matrix")) &&
      peek(1).kind == TokKind::Ident)
  {
    declaration(peek());
    expect_punct(";", "expected ';' after the declaration");
    return;
  }
  assignment_or_call();
  expect_punct(";", "expected ';' after the statement");
}

inline void Compiler::compile_program()
{
  scopes_.emplace_back();
  while (!at_end()) {
    if (accept_punct(";")) {
      continue;
    }
    const size_t before = pos_;
    statement();
    if (panic_) {
      while (!at_end() && !check_punct(";")) {
        advance();
      }
      accept_punct(";");
      panic_ = false;
    }
    if (pos_ == before && !at_end()) {
      advance(); /* never loop forever on an unparsable token */
    }
    if (program_.errors.size() > 40) {
      error_here("too many errors, stopping");
      break;
    }
  }
  if (!failed()) {
    emit(Op::Halt);
  }
}

}  // namespace detail

/* ---------------------------------------------------------- public API */

inline Program compile(std::string_view code, const bool detail)
{
  Program program;
  std::vector<Error> lex_errors;
  const std::vector<Token> tokens = detail::lex(code, lex_errors);
  program.errors = lex_errors;
  if (!program.errors.empty()) {
    return program;
  }
  detail::Compiler compiler(tokens, program, detail);
  compiler.compile_program();
  if (!program.ok()) {
    program.code.clear();
    program.attrs.clear();
  }
  return program;
}

inline const std::vector<FunctionInfo> &kFunctions()
{
  return detail::function_table();
}

inline std::string Program::error_report() const
{
  std::string text;
  for (const Error &error : errors) {
    text += error.format();
    text += "\n";
  }
  return text;
}

inline RunStatus run(const Program &program, Frame &frame, uint32_t step_budget)
{
  using detail::apply_fn1;
  using detail::apply_fn2;
  using detail::apply_fn3;
  using detail::hash_float;
  using detail::value_noise;
  const Instr *code = program.code.data();
  float *s = frame.s.data();
  Vec3 *v = frame.v.data();
  /* `WR-43`…`WR-58`: the rotation and matrix register files. A register index names its slot, and
   * the operand helpers (`q + reg * 4`, `m + reg * 16`) walk its components. */
  float *q = frame.q.data();
  float *m = frame.m.data();
  uint32_t pc = 0;
  uint32_t steps = 0;
  while (true) {
    const Instr &in = code[pc];
    if (++steps > step_budget) {
      return RunStatus::BudgetExceeded;
    }
    switch (in.op) {
      case Op::Halt:
        return RunStatus::Ok;
      case Op::CopyQ:
        for (int i = 0; i < 4; i++) {
          q[in.dst * 4 + i] = q[in.a * 4 + i];
        }
        break;
      case Op::CopyM:
        for (int i = 0; i < 16; i++) {
          m[in.dst * 16 + i] = m[in.a * 16 + i];
        }
        break;
      case Op::QuatIdentity:
        q[in.dst * 4 + 0] = 1.0f;
        q[in.dst * 4 + 1] = q[in.dst * 4 + 2] = q[in.dst * 4 + 3] = 0.0f;
        break;
      case Op::MatrixIdentity:
        detail::mat_identity(m + in.dst * 16);
        break;
      case Op::QuatFromComponents:
        q[in.dst * 4 + 0] = s[in.a];
        q[in.dst * 4 + 1] = s[in.b];
        q[in.dst * 4 + 2] = s[in.c];
        q[in.dst * 4 + 3] = s[in.d];
        break;
      case Op::QuatFromAxisAngle:
        detail::quat_from_axis_angle(v[in.a], s[in.b], q + in.dst * 4);
        break;
      case Op::QuatFromEuler:
        detail::quat_from_euler(v[in.a], q + in.dst * 4);
        break;
      case Op::QuatToEuler:
        v[in.dst] = detail::euler_from_quat(q + in.a * 4);
        break;
      case Op::QuatToAxisAngle: {
        /* `dst` is the axis (vector), `b` the angle (scalar) - `WR-49`'s two out parameters. */
        Vec3 axis;
        float angle = 0.0f;
        detail::axis_angle_from_quat(q + in.a * 4, axis, angle);
        v[in.dst] = axis;
        s[in.b] = angle;
        break;
      }
      case Op::QuatMultiply: {
        /* `WR-43`: `imm` 0 is global (`by * base`), 1 is local (`base * by`). */
        const float *base = q + in.a * 4;
        const float *by = q + in.b * 4;
        float result[4];
        if (in.imm == 1) {
          detail::quat_mul(base, by, result);
        }
        else {
          detail::quat_mul(by, base, result);
        }
        for (int i = 0; i < 4; i++) {
          q[in.dst * 4 + i] = result[i];
        }
        break;
      }
      case Op::QuatRotateV:
        v[in.dst] = detail::quat_rotate_vec(q + in.a * 4, v[in.b]);
        break;
      case Op::QuatInvert:
        detail::quat_invert(q + in.a * 4, q + in.dst * 4);
        break;
      case Op::QuatAlignVector:
        /* `imm` = axis * 4 + pivot. Only the node's auto-pivot path is implemented, so the pivot
         * word is validated at compile time and ignored here. */
        detail::quat_align_to_vector(q + in.a * 4, in.imm / 4, v[in.c], s[in.b], q + in.dst * 4);
        break;
      case Op::QuatAxes:
        /* `imm` = primary_axis * 3 + secondary_axis. */
        detail::quat_from_axes(in.imm / 3, in.imm % 3, v[in.a], v[in.b], q + in.dst * 4);
        break;
      case Op::CombineTransform:
        detail::mat_from_transform(v[in.a], q + in.b * 4, v[in.c], m + in.dst * 16);
        break;
      case Op::MatrixMultiply:
        /* `dst = a * b`; the compiler passes the node's inputs in the order this product needs
         * (`WR-54`). */
        detail::mat_mul(m + in.a * 16, m + in.b * 16, m + in.dst * 16);
        break;
      case Op::InvertMatrix: {
        bool success = true;
        detail::mat_invert(m + in.a * 16, m + in.dst * 16, success);
        break;
      }
      case Op::TransposeMatrix:
        detail::mat_transpose(m + in.a * 16, m + in.dst * 16);
        break;
      case Op::MatrixDeterminant:
        s[in.dst] = detail::mat_determinant(m + in.a * 16);
        break;
      case Op::SeparateTransform: {
        /* `dst` is the translation (vector), `b` the rotation (quaternion), `c` the scale. */
        Vec3 translation, scale;
        detail::mat_separate_transform(m + in.a * 16, translation, q + in.b * 4, scale);
        v[in.dst] = translation;
        v[in.c] = scale;
        break;
      }
      case Op::MatrixColumn: {
        /* `m[column]` as a vector, taken along the *column*: entry `row` of the requested column is
         * `m[column * 4 + row]`, so this reads that column and returns its first three entries.
         * The offset is computed in `int` on purpose - `size_t(negative)` reads out of bounds and
         * quietly yields 0. */
        const int column = (in.imm >= 0 && in.imm <= 3) ? in.imm : 0;
        v[in.dst] = Vec3{m[in.a * 16 + column * 4 + 0],
                         m[in.a * 16 + column * 4 + 1],
                         m[in.a * 16 + column * 4 + 2]};
        break;
      }
      case Op::MatrixTransformV: {
        /* `m * v` on a 4x4, column-major like `float4x4`: the result is `R*S*v + t`, so the
         * translation in the last column does apply. The matrix is `a`, the vector `b`. */
        const float *matrix = m + in.a * 16;
        const Vec3 point = v[in.b];
        v[in.dst] = Vec3{matrix[0] * point.x + matrix[4] * point.y + matrix[8] * point.z +
                             matrix[12],
                         matrix[1] * point.x + matrix[5] * point.y + matrix[9] * point.z +
                             matrix[13],
                         matrix[2] * point.x + matrix[6] * point.y + matrix[10] * point.z +
                             matrix[14]};
        break;
      }
      case Op::TGet: {
        /* `imm` packs the source: bit 12 selects the matrix file, the upper bits the element. */
        const bool is_matrix = (in.imm & 0x1000) != 0;
        const int element = (in.imm >> 16) & 0xff;
        if (is_matrix) {
          s[in.dst] = (element < 16) ? m[in.a * 16 + element] : 0.0f;
        }
        else {
          s[in.dst] = (element < 4) ? q[in.a * 4 + element] : 0.0f;
        }
        break;
      }
      case Op::TSet: {
        /* Same packing: `a` is the rotation or matrix register, `b` the scalar to store. */
        const bool is_matrix = (in.imm & 0x1000) != 0;
        const int element = (in.imm >> 16) & 0xff;
        if (is_matrix) {
          if (element < 16) {
            m[in.a * 16 + element] = s[in.b];
          }
        }
        else if (element < 4) {
          q[in.a * 4 + element] = s[in.b];
        }
        break;
      }
      case Op::LoadAttrIndex: {
        /* WR-31/WR-32: the index is a run-time value, so this is the one read the host cannot
         * pre-load into a register. Everything missing - no slot, no source, a negative index,
         * an index past the end - reads 0 and touches nothing. */
        const int slot = in.imm;
        const AttrBinding *binding = (slot >= 0 && slot < int(program.attrs.size())) ?
                                         &program.attrs[size_t(slot)] :
                                         nullptr;
        const int index = int(s[in.a]);
        const ReadSource *source = (binding != nullptr && slot < frame.n_sources) ?
                                       &frame.sources[slot] :
                                       nullptr;
        const bool have = source != nullptr && index >= 0 && index < source->size;
        if (binding != nullptr && binding->type == Type::Quaternion) {
          /* `WR-47`: four floats into the rotation register file; a miss reads all zeros. */
          if (have && source->read_q != nullptr) {
            source->read_q(source->ctx, index, q + in.dst * 4);
          }
          else {
            for (int k = 0; k < 4; k++) {
              q[in.dst * 4 + k] = 0.0f;
            }
          }
          break;
        }
        if (binding != nullptr && binding->type == Type::Matrix) {
          /* `WR-53`: sixteen floats into the matrix register file; a miss reads all zeros. */
          if (have && source->read_m != nullptr) {
            source->read_m(source->ctx, index, m + in.dst * 16);
          }
          else {
            for (int k = 0; k < 16; k++) {
              m[in.dst * 16 + k] = 0.0f;
            }
          }
          break;
        }
        float x = 0.0f, y = 0.0f, z = 0.0f;
        if (have) {
          if (binding->type == Type::Vector) {
            if (source->read_v != nullptr) {
              float out[3] = {0.0f, 0.0f, 0.0f};
              source->read_v(source->ctx, index, out);
              x = out[0];
              y = out[1];
              z = out[2];
            }
          }
          else if (binding->type == Type::Int) {
            if (source->read_i != nullptr) {
              x = float(source->read_i(source->ctx, index));
            }
          }
          else if (source->read_f != nullptr) {
            x = source->read_f(source->ctx, index);
          }
        }
        if (binding != nullptr && binding->type == Type::Vector) {
          v[in.dst] = Vec3{x, y, z};
        }
        else {
          s[in.dst] = x;
        }
        break;
      }
      case Op::StoreAttrIndex: {
        /* WR-34: do not write the attribute here. Buffering it (a) keeps the read view frozen
         * for the rest of this element and (b) lets the host merge every element's writes in
         * element order, which is the only way a parallel run stays deterministic. */
        const int slot = in.imm;
        if (slot >= 0 && slot < int(program.attrs.size())) {
          WriteEntry entry;
          entry.slot = slot;
          entry.index = int(s[in.a]);
          const Type type = program.attrs[size_t(slot)].type;
          if (type == Type::Vector) {
            entry.value = v[in.b];
          }
          else if (type == Type::Quaternion) {
            for (int k = 0; k < 4; k++) {
              entry.quat[k] = q[in.b * 4 + k];
            }
          }
          else if (type == Type::Matrix) {
            for (int k = 0; k < 16; k++) {
              entry.matrix[k] = m[in.b * 16 + k];
            }
          }
          else {
            entry.scalar = s[in.b];
          }
          frame.writes.push_back(entry);
        }
        break;
      }
      case Op::AddPoint: {
        /* WR-34 promise 1: consecutive numbers from this element's base. The host has already
         * counted how many the elements before this one create, so these are the final numbers.
         * WR-25: `in.imm` picks the point table and the position argument rides along in a vector
         * register - the host applies it after the run, once the table has its final size. */
        const int table = (in.imm != 0) ? 1 : 0;
        const int number = frame.new_point_base[table] + frame.new_points[table];
        frame.added_points.push_back(Frame::AddPointEntry{number, table != 0, frame.v[in.a]});
        s[in.dst] = float(number);
        frame.new_points[table]++;
        break;
      }
      case Op::AddPrim: {
        /* WR-36: the number is handed out exactly like addpoint's (WR-34 promise 1), and the
         * point numbers are copied out of their consecutive registers so the host can build the
         * primitive after the run. */
        TopologyEntry entry;
        entry.slot = in.imm;
        entry.number = frame.new_prim_base + frame.new_prims++;
        entry.points.reserve(size_t(in.b));
        for (int i = 0; i < int(in.b); i++) {
          entry.points.push_back(int(s[in.a + i]));
        }
        s[in.dst] = float(entry.number);
        frame.topology.push_back(std::move(entry));
        break;
      }
      case Op::RemovePoint:
        frame.removed_points.push_back(Frame::RemovalEntry{in.imm, int(s[in.a])});
        break;
      case Op::RemovePrim:
        frame.removed_prims.push_back(Frame::RemovalEntry{in.imm, int(s[in.a])});
        break;
      case Op::LoadF:
        s[in.dst] = in.fimm;
        break;
      case Op::CopyF:
        s[in.dst] = s[in.a];
        break;
      case Op::CopyV:
        v[in.dst] = v[in.a];
        break;
      case Op::MakeV:
        v[in.dst] = Vec3{s[in.a], s[in.b], s[in.c]};
        break;
      case Op::AddF:
        s[in.dst] = s[in.a] + s[in.b];
        break;
      case Op::SubF:
        s[in.dst] = s[in.a] - s[in.b];
        break;
      case Op::MulF:
        s[in.dst] = s[in.a] * s[in.b];
        break;
      case Op::DivF:
        s[in.dst] = s[in.a] / s[in.b];
        break;
      case Op::ModF:
        s[in.dst] = std::fmod(s[in.a], s[in.b]);
        break;
      case Op::NegF:
        s[in.dst] = -s[in.a];
        break;
      case Op::TruncF:
        s[in.dst] = std::trunc(s[in.a]);
        break;
      case Op::AddV:
        v[in.dst] = Vec3{v[in.a].x + v[in.b].x, v[in.a].y + v[in.b].y, v[in.a].z + v[in.b].z};
        break;
      case Op::SubV:
        v[in.dst] = Vec3{v[in.a].x - v[in.b].x, v[in.a].y - v[in.b].y, v[in.a].z - v[in.b].z};
        break;
      case Op::MulV:
        v[in.dst] = Vec3{v[in.a].x * v[in.b].x, v[in.a].y * v[in.b].y, v[in.a].z * v[in.b].z};
        break;
      case Op::DivV:
        v[in.dst] = Vec3{v[in.a].x / v[in.b].x, v[in.a].y / v[in.b].y, v[in.a].z / v[in.b].z};
        break;
      case Op::NegV:
        v[in.dst] = Vec3{-v[in.a].x, -v[in.a].y, -v[in.a].z};
        break;
      case Op::AddVF:
        v[in.dst] = Vec3{v[in.a].x + s[in.b], v[in.a].y + s[in.b], v[in.a].z + s[in.b]};
        break;
      case Op::SubVF:
        v[in.dst] = Vec3{v[in.a].x - s[in.b], v[in.a].y - s[in.b], v[in.a].z - s[in.b]};
        break;
      case Op::MulVF:
        v[in.dst] = Vec3{v[in.a].x * s[in.b], v[in.a].y * s[in.b], v[in.a].z * s[in.b]};
        break;
      case Op::DivVF:
        v[in.dst] = Vec3{v[in.a].x / s[in.b], v[in.a].y / s[in.b], v[in.a].z / s[in.b]};
        break;
      case Op::SubFV:
        v[in.dst] = Vec3{s[in.a] - v[in.b].x, s[in.a] - v[in.b].y, s[in.a] - v[in.b].z};
        break;
      case Op::DivFV:
        v[in.dst] = Vec3{s[in.a] / v[in.b].x, s[in.a] / v[in.b].y, s[in.a] / v[in.b].z};
        break;
      case Op::CallF1:
        s[in.dst] = apply_fn1(Fn1(in.imm), s[in.a]);
        break;
      case Op::CallF2:
        s[in.dst] = apply_fn2(Fn2(in.imm), s[in.a], s[in.b]);
        break;
      case Op::CallF3:
        s[in.dst] = apply_fn3(Fn3(in.imm), s[in.a], s[in.b], s[in.c]);
        break;
      case Op::MapV1: {
        const Vec3 a = v[in.a];
        const Fn1 fn = Fn1(in.imm);
        v[in.dst] = Vec3{apply_fn1(fn, a.x), apply_fn1(fn, a.y), apply_fn1(fn, a.z)};
        break;
      }
      case Op::MapV2: {
        const Vec3 a = v[in.a], b = v[in.b];
        const Fn2 fn = Fn2(in.imm);
        v[in.dst] = Vec3{apply_fn2(fn, a.x, b.x), apply_fn2(fn, a.y, b.y), apply_fn2(fn, a.z, b.z)};
        break;
      }
      case Op::MapV2S: {
        const Vec3 a = v[in.a];
        const float b = s[in.b];
        const Fn2 fn = Fn2(in.imm);
        v[in.dst] = Vec3{apply_fn2(fn, a.x, b), apply_fn2(fn, a.y, b), apply_fn2(fn, a.z, b)};
        break;
      }
      case Op::LenV: {
        const Vec3 a = v[in.a];
        s[in.dst] = std::sqrt(a.x * a.x + a.y * a.y + a.z * a.z);
        break;
      }
      case Op::LenSqV: {
        const Vec3 a = v[in.a];
        s[in.dst] = a.x * a.x + a.y * a.y + a.z * a.z;
        break;
      }
      case Op::DistV: {
        const Vec3 a = v[in.a], b = v[in.b];
        const float dx = a.x - b.x, dy = a.y - b.y, dz = a.z - b.z;
        s[in.dst] = std::sqrt(dx * dx + dy * dy + dz * dz);
        break;
      }
      case Op::DotV: {
        const Vec3 a = v[in.a], b = v[in.b];
        s[in.dst] = a.x * b.x + a.y * b.y + a.z * b.z;
        break;
      }
      case Op::CrossV: {
        const Vec3 a = v[in.a], b = v[in.b];
        v[in.dst] = Vec3{a.y * b.z - a.z * b.y, a.z * b.x - a.x * b.z, a.x * b.y - a.y * b.x};
        break;
      }
      case Op::NormalizeV: {
        const Vec3 a = v[in.a];
        const float length = std::sqrt(a.x * a.x + a.y * a.y + a.z * a.z);
        v[in.dst] = length > 0.0f ? Vec3{a.x / length, a.y / length, a.z / length} : Vec3{0, 0, 0};
        break;
      }
      case Op::LtF:
        s[in.dst] = s[in.a] < s[in.b] ? 1.0f : 0.0f;
        break;
      case Op::LeF:
        s[in.dst] = s[in.a] <= s[in.b] ? 1.0f : 0.0f;
        break;
      case Op::GtF:
        s[in.dst] = s[in.a] > s[in.b] ? 1.0f : 0.0f;
        break;
      case Op::GeF:
        s[in.dst] = s[in.a] >= s[in.b] ? 1.0f : 0.0f;
        break;
      case Op::EqF:
        s[in.dst] = s[in.a] == s[in.b] ? 1.0f : 0.0f;
        break;
      case Op::NeF:
        s[in.dst] = s[in.a] != s[in.b] ? 1.0f : 0.0f;
        break;
      case Op::AndI:
        s[in.dst] = (s[in.a] != 0.0f && s[in.b] != 0.0f) ? 1.0f : 0.0f;
        break;
      case Op::OrI:
        s[in.dst] = (s[in.a] != 0.0f || s[in.b] != 0.0f) ? 1.0f : 0.0f;
        break;
      case Op::NotI:
        s[in.dst] = s[in.a] == 0.0f ? 1.0f : 0.0f;
        break;
      case Op::Jmp:
        pc = uint32_t(in.imm);
        continue;
      case Op::JmpIfZero:
        if (s[in.a] == 0.0f) {
          pc = uint32_t(in.imm);
          continue;
        }
        break;
      case Op::VGet:
        s[in.dst] = in.imm == 0 ? v[in.a].x : (in.imm == 1 ? v[in.a].y : v[in.a].z);
        break;
      case Op::VSet: {
        Vec3 out = v[in.a];
        const float value = s[in.b];
        if (in.imm == 0) {
          out.x = value;
        }
        else if (in.imm == 1) {
          out.y = value;
        }
        else {
          out.z = value;
        }
        v[in.dst] = out;
        break;
      }
      case Op::RandF:
        s[in.dst] = hash_float(s[in.a]);
        break;
      case Op::NoiseF:
        s[in.dst] = value_noise(s[in.a], 0.0f, 0.0f);
        break;
      case Op::NoiseV:
        s[in.dst] = value_noise(v[in.a].x, v[in.a].y, v[in.a].z);
        break;
    }
    pc++;
  }
}

inline std::string Program::disassemble() const
{
  static const char *op_names[] = {
      "halt",    "loadf",   "copyf",   "addf",   "subf",    "mulf",    "divf",   "modf",
      "negf",    "truncf",  "copyv",   "makev",  "addv",    "subv",    "mulv",   "divv",
      "negv",    "addvf",   "subvf",   "mulvf",  "divvf",   "subfv",   "divfv",  "callf1",
      "callf2",  "callf3",  "mapv1",   "mapv2",  "mapv2s",  "lenv",    "lensqv", "distv",
      "dotv",    "crossv",  "nrmv",    "ltf",    "lef",     "gtf",     "gef",    "eqf",
      "nef",     "andi",    "ori",     "noti",   "jmp",     "jmpz",    "vget",   "vset",
      "randf",   "noisef",  "noisev",  "ldattr",  "stattr",  "addpt",   "addprim",
      "rmpoint", "rmprim",
  };
  std::string text;
  for (size_t i = 0; i < code.size(); i++) {
    const Instr &in = code[i];
    const int index = int(in.op);
    const char *name = (index >= 0 && index < int(sizeof(op_names) / sizeof(op_names[0])))
                           ? op_names[index]
                           : "?";
    char line[160];
    std::snprintf(line,
                  sizeof(line),
                  "%4zu  %-8s d=%-3u a=%-3u b=%-3u c=%-3u imm=%-5d f=%.4g\n",
                  i,
                  name,
                  in.dst,
                  in.a,
                  in.b,
                  in.c,
                  in.imm,
                  double(in.fimm));
    text += line;
  }
  for (size_t i = 0; i < attrs.size(); i++) {
    const AttrBinding &binding = attrs[i];
    text += "attr[" + std::to_string(i) + "] " + binding.name + " : " + type_name(binding.type) +
            " reg=" + std::to_string(binding.reg) +
            (binding.read ? " r" : "") + (binding.write ? " w" : "") +
            (binding.host_supplied ? " host" : "") +
            (binding.input == 0 ? "" : " input" + std::to_string(binding.input)) +
            (binding.domain == ReadDomain::Point ? " readpoint" : "") +
            (binding.domain == ReadDomain::Prim ? " readprim" : "") +
            (binding.count ? " count" : "") + "\n";
  }
  return text;
}

}  // namespace pdt_wrangle
