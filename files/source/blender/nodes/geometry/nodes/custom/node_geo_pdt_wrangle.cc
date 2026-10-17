/* SPDX-FileCopyrightText: 2026 PDT Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup geo_nodes
 *
 * PDT Wrangle: run user-written VEX-like code over geometry elements.
 *
 * This file is only the **host**: the language, the compiler and the VM live in
 * `pdt_wrangle_vm.hh` (self-contained, no Blender headers), which is what makes the
 * engine testable in seconds by `tools/pdt/tests/wrangle_engine_selftest.cpp`.
 *
 * What the host does per evaluation:
 *   1. compile the code (cached by content, thread-safe - evaluation can run concurrently
 *      for several objects),
 *   2. resolve every attribute the program touches into a Blender attribute writer
 *      (creating it on first write, exactly like `v@Cd = ...` in VEX),
 *   3. load -> run -> store per element under `threading::parallel_for`,
 *   4. report compile errors with `line:column`, and a step-budget error for a runaway loop.
 *
 * M1 scope: **Run Over = Points and Vertices** and the math library in `pdt_wrangle_vm.hh`.
 * Other element kinds pass the geometry through with a warning; `@P` is read-only when
 * running over Vertices (several corners share one point, so writing it would race).
 */

#include <array>
#include "BKE_customdata.hh"
#include "BKE_geometry_fields.hh"
#include "GEO_join_geometries.hh"
#include "GEO_separate_geometry.hh"#include <atomic>
#include <cmath>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

#include "BLI_array.hh"
#include "BLI_enumerable_thread_specific.hh"
#include "BLI_index_mask.hh"
#include "BLI_map.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_task.hh"

#include "BLT_translation.hh"

#include "BKE_attribute.hh"
#include "BKE_curves.hh"
#include "BKE_geometry_fields.hh"
#include "BKE_geometry_set.hh"
#include "BKE_instances.hh"
#include "BKE_mesh.hh"
#include "BKE_pointcloud.hh"

#include "FN_field.hh"

#include "UI_interface_layout.hh"

#include "node_geometry_util.hh"

#include "NOD_geo_pdt_wrangle.hh"
#include "NOD_socket_items_blend.hh"
#include "NOD_socket_items_ops.hh"
#include "NOD_socket_items_ui.hh"

#include "RNA_access.hh"
#include "RNA_prototypes.hh"

#include "BLO_read_write.hh"

#include "pdt_wrangle_vm.hh"

namespace blender::nodes::node_geo_pdt_wrangle_cc {

namespace vm = pdt_wrangle;

/* WR-35..WR-40: the channel list lives in the node's storage (the shared `socket_items::`
 * machinery above the node's own code). */
NODE_STORAGE_FUNCS(NodeGeometryPDTWrangle)

/**
 * Which element kind the snippet runs on (`WR-8`). The ints are the menu socket values and
 * follow the order that spec lists them in (`WR-24`) - they are what a saved .blend stores,
 * so they are append-only.
 */
enum class RunOver : int16_t {
  /* WR-10: one kind per component - the order here *is* the menu order and the int stored in a
   * .blend (WR-11 makes reordering a known, accepted break for older files). */
  Vertex = 0,
  Edges = 1,
  Faces = 2,
  FaceCorners = 3,
  ControlPoint = 4,
  Splines = 5,
  Point = 6,
  Instances = 7,
  Detail = 8,
};

static const EnumPropertyItem run_over_items[] = {
    {int(RunOver::Vertex),
     "VERTEX",
     0,
     N_("Vertex"),
     N_("Run once per mesh vertex")},
    {int(RunOver::Edges),
     "EDGES",
     0,
     N_("Edges"),
     N_("Run once per mesh edge (no @P: an edge has no position of its own)")},
    {int(RunOver::Faces),
     "FACES",
     0,
     N_("Faces"),
     N_("Run once per mesh face (no @P: a face has no position of its own)")},
    {int(RunOver::FaceCorners),
     "CORNERS",
     0,
     N_("Face Corners"),
     N_("Run once per face corner; @P reads (but cannot write) the corner's point")},
    {int(RunOver::ControlPoint),
     "CONTROL_POINT",
     0,
     N_("Control Point"),
     N_("Run once per curve control point")},
    {int(RunOver::Splines),
     "SPLINES",
     0,
     N_("Splines"),
     N_("Run once per curve spline (no @P)")},
    {int(RunOver::Point),
     "POINT",
     0,
     N_("Point"),
     N_("Run once per point cloud point")},
    {int(RunOver::Instances),
     "INSTANCES",
     0,
     N_("Instances"),
     N_("Run once per instance; @P is the instance's translation")},
    {int(RunOver::Detail),
     "DETAIL",
     0,
     N_("Detail"),
     N_("Run once for the whole geometry; no @ at all")},
    {0, nullptr, 0, nullptr, nullptr},
};

/** Code lines visible before the box starts scrolling. */
static constexpr int code_visible_lines = 12;

/** How many compiled programs to keep. One entry per distinct code string. */
static constexpr int max_cached_programs = 32;

/* ------------------------------------------------------------------ */
/** \name Compilation cache                                             */
/* ------------------------------------------------------------------ */

/**
 * Compiled programs, keyed by the code itself. Node evaluation can run for several
 * objects at once, so this is behind a mutex; the string key makes the cache immune to
 * which .blend or node the code came from.
 */
struct ProgramCache {
  std::mutex mutex;
  std::unordered_map<std::string, std::shared_ptr<const vm::Program>> programs;
  std::deque<std::string> order;
};

static std::shared_ptr<const vm::Program> get_program(const std::string &code, const bool detail)
{
  static ProgramCache cache;
  /* The Detail rule is a compile-time rule (`WR-8`/`WR-30`), so the mode is part of the key. */
  const std::string key = detail ? ("\x01" + code) : code;
  std::lock_guard<std::mutex> lock(cache.mutex);
  const auto found = cache.programs.find(key);
  if (found != cache.programs.end()) {
    return found->second;
  }
  auto program = std::make_shared<const vm::Program>(vm::compile(code, detail));
  if (cache.order.size() >= max_cached_programs) {
    cache.programs.erase(cache.order.front());
    cache.order.pop_front();
  }
  cache.order.push_back(key);
  cache.programs.emplace(key, program);
  return program;
}

/* ------------------------------------------------------------------ */
/** \name Attribute binding                                             */
/* ------------------------------------------------------------------ */

/** The attribute domain the code reads and writes (`WR-8`). */
enum class WrangleDomain : uint8_t { Point, Edge, Face, Corner, Curve, Instance };

static bke::AttrDomain to_attr_domain(const WrangleDomain domain)
{
  switch (domain) {
    case WrangleDomain::Point:
      return bke::AttrDomain::Point;
    case WrangleDomain::Edge:
      return bke::AttrDomain::Edge;
    case WrangleDomain::Face:
      return bke::AttrDomain::Face;
    case WrangleDomain::Corner:
      return bke::AttrDomain::Corner;
    case WrangleDomain::Curve:
      return bke::AttrDomain::Curve;
    case WrangleDomain::Instance:
      return bke::AttrDomain::Instance;
  }
  return bke::AttrDomain::Point;
}

/** True when `@P` exists on this domain at all (`WR-23`). */
static bool domain_has_position(const WrangleDomain domain)
{
  return ELEM(domain, WrangleDomain::Point, WrangleDomain::Corner);
}

/** One attribute, resolved to the Blender storage the VM will read and write. */
struct Slot {
  const vm::AttrBinding *attr = nullptr;

  enum class Kind : uint8_t {
    Host,   /* filled by the host: @elemnum / @numelem */
    F,      /* FLOAT */
    I,      /* INT */
    B,      /* BOOLEAN */
    V3,     /* FLOAT_VECTOR */
    V2,     /* FLOAT2, read/written as .x/.y */
    /* `WR-47` / `WR-53`: Blender's own `QUATERNION` and `FLOAT4X4` attribute types - the two the
     * rotation and matrix functions are built on (`math::Quaternion` is w, x, y, z). */
    Q4,     /* QUATERNION, 4 floats */
    M4,     /* FLOAT4X4, 16 floats, four columns */
    ZeroF,  /* referenced but missing, never written: reads as 0 (VEX behaviour) */
    ZeroV,
    ZeroQ,
    ZeroM,
    Missing,     /* not stored on this domain */
    Unsupported, /* stored, but with a type the VM cannot bind */
    PositionViaCorner,   /* @P in Face Corners mode: the corner's point (read-only) */
    InstancePosition,    /* @P in Instances mode: the transform's translation */
    ExplicitRead,        /* fpoint(…)/fprim(…): read-only at a run-time index (WR-31) */
    Count,               /* npoints(…)/nprimitives(…): one number the host fills (WR-31) */
    Channel,             /* chf(…)/chi(…): one channel value the host filled (WR-36) */
    ChannelV,            /* chv(…): the same, in the vector register file */
    ChannelQ,            /* chq(…): the same, in the rotation register file (WR-36) */
    ChannelM,            /* chm(…): the same, in the matrix register file (WR-36) */
    IndexedWrite,        /* setpointattrib/setprimattrib: buffered, merged in element order (WR-34) */
    DroppedWrite,        /* …and the component has no such table, so every write is dropped (WR-35) */
  };
  Kind kind = Kind::ZeroF;
  /** `Kind::Count`: the size of the table `npoints` / `nprimitives` reports (`WR-35`). */
  float value = 0.0f;
  /** `Kind::Channel` / `Kind::ChannelV`: the channel value this evaluation read (`WR-37`). */
  vm::Vec3 value_v{0.0f, 0.0f, 0.0f};
  /** `Kind::ChannelQ` / `Kind::ChannelM`: the same, in the two transform register files. */
  float value_q[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  float value_m[16] = {0.0f};

  /* Writers keep the attribute alive and tag it as modified in `finish()`. */
  SpanAttributeWriter<float> wf;
  SpanAttributeWriter<int> wi;
  SpanAttributeWriter<bool> wb;
  SpanAttributeWriter<float3> wv3;
  SpanAttributeWriter<float2> wv2;
  /* `WR-47` / `WR-53`: the rotation and matrix writers. `math::Quaternion` keeps the real part
   * first (w, x, y, z), while `float4` is (x, y, z, w) - the two are not interchangeable. */
  SpanAttributeWriter<math::Quaternion> wq4;
  SpanAttributeWriter<float4x4> wm4;

  MutableSpan<float> f;
  MutableSpan<int> i;
  MutableSpan<bool> b;
  MutableSpan<float3> v3;
  MutableSpan<float2> v2;
  MutableSpan<math::Quaternion> q4;
  MutableSpan<float4x4> m4;
  Span<int> corner_verts;
  MutableSpan<float4x4> transforms; /* Instances mode: `@P` lives here, not in an attribute */

  /* `Kind::ExplicitRead`: a read-only view of a table `fpoint(…)` / `fprim(…)` names, possibly on
   * another geometry input. `read_size` is that table's element count, so an index it does not
   * have reads as 0 instead of walking off the array. */
  GAttributeReader reader;
  VArray<float> rf;
  VArray<int> ri;
  VArray<float3> rv3;
  VArray<float2> rv2;
  VArray<math::Quaternion> rq4;
  VArray<float4x4> rm4;
  int read_size = 0;
};

static const char *stored_type_name(const Slot::Kind kind)
{
  switch (kind) {
    case Slot::Kind::F:
      return "FLOAT";
    case Slot::Kind::I:
      return "INT";
    case Slot::Kind::B:
      return "BOOLEAN";
    case Slot::Kind::V3:
      return "FLOAT_VECTOR";
    case Slot::Kind::V2:
      return "FLOAT2";
    case Slot::Kind::Q4:
      return "QUATERNION";
    case Slot::Kind::M4:
      return "FLOAT4X4";
    default:
      return "unsupported";
  }
}

/**
 * The stored type of `name` on `domain`.
 *
 * Reads the type off the attribute itself rather than probing `lookup<float>(...)`: that
 * one *converts* (an INT attribute reads as float), which made the later
 * `lookup_or_add_for_write_span<float>` fail on an INT attribute and left an empty span
 * behind - which then crashed the element loop (measured: access violation reading 0x0).
 */
/** The kind a stored attribute's own type maps to (cross-stream reads use it too). */
static Slot::Kind kind_of_type(const CPPType &type)
{
  if (type.is<float>()) {
    return Slot::Kind::F;
  }
  if (type.is<int>()) {
    return Slot::Kind::I;
  }
  if (type.is<bool>()) {
    return Slot::Kind::B;
  }
  if (type.is<float3>()) {
    return Slot::Kind::V3;
  }
  if (type.is<float2>()) {
    return Slot::Kind::V2;
  }
  /* `WR-47` / `WR-53`: `QUATERNION` is `math::Quaternion` and `FLOAT4X4` is `float4x4`
   * (`attribute_access.cc`). Both are ordinary attribute types, so they bind like the rest. */
  if (type.is<math::Quaternion>()) {
    return Slot::Kind::Q4;
  }
  if (type.is<float4x4>()) {
    return Slot::Kind::M4;
  }
  return Slot::Kind::Unsupported;
}

static Slot::Kind detect_kind(const bke::AttributeAccessor &attributes,
                              const StringRef name,
                              const bke::AttrDomain domain)
{
  const GAttributeReader reader = attributes.lookup(name, domain);
  if (!reader) {
    return Slot::Kind::Missing;
  }
  return kind_of_type(reader.varray.type());
}

/** A writer is only usable when it exists and covers every element. */
static bool writer_ok(const bool valid,
                      const size_t size,
                      const int element_count,
                      const std::string &name,
                      std::string &error)
{
  if (!valid || (element_count > 0 && int(size) < element_count)) {
    error = "could not bind attribute '" + name + "' for writing (unexpected storage)";
    return false;
  }
  return true;
}

static bool declared_type_fits(const vm::Type declared, const Slot::Kind kind)
{
  switch (declared) {
    case vm::Type::Float:
    case vm::Type::Int:
      return ELEM(kind, Slot::Kind::F, Slot::Kind::I, Slot::Kind::B);
    case vm::Type::Vector:
      return ELEM(kind, Slot::Kind::V3, Slot::Kind::V2);
    /* `WR-47` / `WR-53`: these two carry no conversion - a rotation binds only to a stored
     * `QUATERNION`, a matrix only to a `FLOAT4X4`. */
    case vm::Type::Quaternion:
      return kind == Slot::Kind::Q4;
    case vm::Type::Matrix:
      return kind == Slot::Kind::M4;
  }
  return false;
}

/**
 * `WR-41` / `WR-34` promise 3: one of the processed stream's element tables **as it was when this
 * evaluation started**. The tables themselves are tables of *numbers* (how many points / prims),
 * so this is all that has to be frozen - the attribute readers stay live.
 */
struct StartTable {
  int size = 0;
  bool valid = false;
};

/**
 * `WR-41`: the two tables `npoints` / `nprimitives` report, and the bound an own-stream
 * `fpoint` / `fprim` index is checked against, for the stream being processed. Only that stream
 * can change inside this node (removals, `addpoint` growth, a built curve component), so nothing
 * else needs recording.
 */
struct StartTables {
  StartTable point;
  StartTable prim;
  /* `WR-18`/`WR-19`: an explicit `domain` names the component it reads, and every input can be
   * named - so the frozen size is recorded per (input 0..3, component mesh..instance). A read of an
   * element *this evaluation created* is 0 (`WR-20`), and that has to hold for a named component
   * too, not only for the one this pass runs over. */
  StartTable point_by_input[4][4];
  StartTable prim_by_input[4][4];

  const StartTable &operator[](const vm::ReadDomain want) const
  {
    return want == vm::ReadDomain::Prim ? prim : point;
  }
  const StartTable &by(const int input, const vm::Component component, const vm::ReadDomain want) const
  {
    const int index = int(component) - int(vm::Component::Mesh);
    if (input < 0 || input > 3 || index < 0 || index > 3) {
      return (*this)[want];
    }
    return (want == vm::ReadDomain::Prim) ? prim_by_input[input][index] :
                                            point_by_input[input][index];
  }
};

/**
 * The other geometry streams (`WR-29`): `Input 1`..`Input 3`, plus which component of them a
 * cross-stream read looks at - the *same component type* as the one being processed (`WR-30`).
 */
/**
 * `WR-37`: one channel's value for this evaluation. The host reads every channel the program
 * mentions **once**, before the parallel run, so the per-element work is only a register load.
 */
struct ChannelValue {
  /** The scalar register file: `chf` / `chi` on a float or int channel (`WR-39` folds the
   * conversions in: ints are already truncated, a vector channel read as a scalar is 0). */
  float scalar = 0.0f;
  /** The vector register file: `chv`. A scalar channel is `(value, 0, 0)` (`WR-39`). */
  vm::Vec3 vector{0.0f, 0.0f, 0.0f};
  /** `WR-36`: the rotation and matrix register files, `chq` / `chm`. Both start at zero - that is
   * what a channel of another type (or a missing one) reports, by the same "reads as 0" rule the
   * scalars follow (`WR-38`). */
  float quat[4] = {0.0f, 0.0f, 0.0f, 0.0f};
  float matrix[16] = {0.0f};
};

struct PassInputs {
  /** The stream being processed (input 0) - `fpoint(0, …)` reads this one (`WR-31`). */
  const GeometrySet *own = nullptr;
  const std::array<const GeometrySet *, 3> *other = nullptr;
  GeometryComponent::Type component = GeometryComponent::Type::Mesh;
  /** `WR-34` promise 1: the point table this pass may grow (`addpoint`). Null when the component
   * cannot grow points, in which case `addpoint` has nowhere to put them. */
  Mesh *growth = nullptr;
  /* WR-37: separate_geometry replaces the mesh component, so the removals need a mutable geometry
   * set - and the mesh pointer has to be taken again afterwards. */
  GeometrySet *own_write = nullptr;
  /* WR-37: only our own node_geo_exec can produce this. */
  const NodeAttributeFilter *attribute_filter = nullptr;
  /* `WR-41`: the processed stream's tables at the start of this evaluation. Null when nothing in
   * this call can change them (the code creates no topology), in which case they are read live -
   * one less thing to keep in step. */
  const StartTables *start_tables = nullptr;
  /* `WR-36`/`WR-37`: the channel values, indexed like `Program::attrs` (empty when the program
   * reads none). Read once in `node_geo_exec` - the passes only load registers from here. */
  Span<const ChannelValue> channels;
};
/**
 * The reader for `name` on `domain` in `other`'s component of `type` (`WR-30`: the *same*
 * component type as the one being processed). `r_domain_size` receives that component's
 * element count so an index it does not have can read as 0. An empty reader means "nothing
 * there", which is not an error (`WR-32`).
 */
static GAttributeReader other_reader(const GeometrySet &other,
                                     const GeometryComponent::Type type,
                                     const StringRef name,
                                     const bke::AttrDomain domain,
                                     int &r_domain_size)
{
  switch (type) {
    case GeometryComponent::Type::Mesh:
      if (const Mesh *mesh = other.get_mesh()) {
        const bke::AttributeAccessor attributes = mesh->attributes();
        r_domain_size = attributes.domain_size(domain);
        return attributes.lookup(name, domain);
      }
      break;
    case GeometryComponent::Type::Curve:
      if (const Curves *curves = other.get_curves()) {
        const bke::AttributeAccessor attributes = curves->geometry.wrap().attributes();
        r_domain_size = attributes.domain_size(domain);
        return attributes.lookup(name, domain);
      }
      break;
    case GeometryComponent::Type::PointCloud:
      if (const PointCloud *points = other.get_pointcloud()) {
        const bke::AttributeAccessor attributes = points->attributes();
        r_domain_size = attributes.domain_size(domain);
        return attributes.lookup(name, domain);
      }
      break;
    case GeometryComponent::Type::Instance:
      if (const bke::Instances *instances = other.get_instances()) {
        const bke::AttributeAccessor attributes = instances->attributes();
        r_domain_size = attributes.domain_size(domain);
        return attributes.lookup(name, domain);
      }
      break;
    default:
      break;
  }
  r_domain_size = 0;
  return {};
}

/** The table an explicit-index read (`WR-31`) looks in. `WR-35` fixes it by the *processing*
 * component rather than by the run-over domain: a mesh's face table, a curve's spline table,
 * and nothing at all for a point cloud or an instance set. `valid == false` means every read is
 * 0 (`WR-32`). */
struct ReadTable {
  bke::AttrDomain domain = bke::AttrDomain::Point;
  int size = 0;
  bool valid = false;
};

/** `WR-18`/`WR-19`: the `domain` word a read named, as Blender's component type. */
static GeometryComponent::Type component_of(const vm::Component component)
{
  switch (component) {
    case vm::Component::Mesh:
      return GeometryComponent::Type::Mesh;
    case vm::Component::Curve:
      return GeometryComponent::Type::Curve;
    case vm::Component::PointCloud:
      return GeometryComponent::Type::PointCloud;
    case vm::Component::Instance:
      return GeometryComponent::Type::Instance;
    case vm::Component::Pass:
      break;
  }
  return GeometryComponent::Type::Mesh;
}

/** The inverse of `component_of`: index 0..3 is mesh, curve, point cloud, instance. */
static GeometryComponent::Type component_type_of(const int index)
{
  switch (index) {
    case 1:
      return GeometryComponent::Type::Curve;
    case 2:
      return GeometryComponent::Type::PointCloud;
    case 3:
      return GeometryComponent::Type::Instance;
    default:
      break;
  }
  return GeometryComponent::Type::Mesh;
}

static ReadTable resolve_read_table(const GeometrySet &geometry,
                                    const GeometryComponent::Type component,
                                    const vm::ReadDomain want)
{
  ReadTable table;
  const bool prim = want == vm::ReadDomain::Prim;
  switch (component) {
    case GeometryComponent::Type::Mesh:
      if (const Mesh *mesh = geometry.get_mesh()) {
        table.valid = true;
        table.domain = prim ? bke::AttrDomain::Face : bke::AttrDomain::Point;
        table.size = prim ? mesh->faces_num : mesh->verts_num;
      }
      break;
    case GeometryComponent::Type::Curve:
      if (const Curves *curves = geometry.get_curves()) {
        const bke::CurvesGeometry &curves_geometry = curves->geometry.wrap();
        table.valid = true;
        table.domain = prim ? bke::AttrDomain::Curve : bke::AttrDomain::Point;
        table.size = prim ? curves_geometry.curves_num() : curves_geometry.points_num();
      }
      break;
    case GeometryComponent::Type::PointCloud:
      /* WR-35: a point cloud has points but no prim table. */
      if (const PointCloud *points = geometry.get_pointcloud()) {
        if (!prim) {
          table.valid = true;
          table.domain = bke::AttrDomain::Point;
          table.size = points->totpoint;
        }
      }
      break;
    case GeometryComponent::Type::Instance:
      /* WR-35: an instance set has no prim table either; its "points" are the instances - the
       * same reading of `@P` that `WR-23` uses for `Run Over = Instances`. */
      if (const bke::Instances *instances = geometry.get_instances()) {
        if (!prim) {
          table.valid = true;
          table.domain = bke::AttrDomain::Instance;
          table.size = instances->instances_num();
        }
      }
      break;
    default:
      break;
  }
  return table;
}

/** `WR-35`: in Detail nothing is being processed, so the table is chosen by a fixed priority -
 * the mesh face table first, then the curve spline table. */
static GeometryComponent::Type detail_component(const GeometrySet &geometry)
{
  if (geometry.get_mesh() != nullptr) {
    return GeometryComponent::Type::Mesh;
  }
  if (geometry.get_curves() != nullptr) {
    return GeometryComponent::Type::Curve;
  }
  if (geometry.get_pointcloud() != nullptr) {
    return GeometryComponent::Type::PointCloud;
  }
  return GeometryComponent::Type::Instance;
}

/* The VM's accessors (`WR-31`): `ctx` is the bound `Slot`, so a read goes straight into the
 * reader the host already holds - no copy, and the same missing/absent rules as `WR-32`. */
static float wrangle_read_f(const void *ctx, const int index)
{
  return static_cast<const Slot *>(ctx)->rf[index];
}
static int wrangle_read_i(const void *ctx, const int index)
{
  return static_cast<const Slot *>(ctx)->ri[index];
}
static void wrangle_read_v(const void *ctx, const int index, float *out)
{
  const Slot &slot = *static_cast<const Slot *>(ctx);
  if (!slot.rv2.is_empty()) {
    const float2 value = slot.rv2[index];
    out[0] = value.x;
    out[1] = value.y;
    out[2] = 0.0f;
  }
  else {
    const float3 value = slot.rv3[index];
    out[0] = value.x;
    out[1] = value.y;
    out[2] = value.z;
  }
}
/** `WR-47`: four floats, w first - the component order of `math::Quaternion`. */
static void wrangle_read_q(const void *ctx, const int index, float *out)
{
  const math::Quaternion value = static_cast<const Slot *>(ctx)->rq4[index];
  out[0] = value.w;
  out[1] = value.x;
  out[2] = value.y;
  out[3] = value.z;
}
/** `WR-53`: sixteen floats, column by column - the layout of `float4x4`. */
static void wrangle_read_m(const void *ctx, const int index, float *out)
{
  const float4x4 value = static_cast<const Slot *>(ctx)->rm4[index];
  for (int column = 0; column < 4; column++) {
    for (int row = 0; row < 4; row++) {
      out[column * 4 + row] = value[column][row];
    }
  }
}

/**
 * `WR-31`/`WR-32`/`WR-35`: bind one explicit-index read. `attr.input` names the stream (0 = this
 * one), the table comes from the processing component, and everything missing - an unconnected
 * stream, no component of that type, no such table, no such attribute, an index past the end -
 * reads 0 without creating anything. A stored type that does not fit is a node error, because a
 * storage type is only knowable once the geometry is there.
 */
static bool bind_explicit_read(Slot &slot,
                               const vm::AttrBinding &attr,
                               const PassInputs &pass,
                               vm::ReadSource *r_source,
                               std::string &error)
{
  slot.attr = &attr;
  slot.kind = attr.type == vm::Type::Vector ?
                  Slot::Kind::ZeroV :
                  (attr.type == vm::Type::Quaternion ?
                       Slot::Kind::ZeroQ :
                       (attr.type == vm::Type::Matrix ? Slot::Kind::ZeroM : Slot::Kind::ZeroF));

  const GeometrySet *geometry = pass.own;
  if (attr.input > 0) {
    geometry = (pass.other == nullptr) ? nullptr : (*pass.other)[attr.input - 1];
  }
  /* `WR-18`/`WR-19`: an explicit-index read names the component it reads, which need not be the
   * one this pass runs over. */
  const ReadTable table = (geometry == nullptr) ?
                              ReadTable{} :
                              resolve_read_table(*geometry, component_of(attr.component), attr.domain);
  /* `WR-20`/`WR-41`: the table size this evaluation started with. For the component this pass
   * runs over that is the `@` form's frozen view; a `domain` word names a component of any input,
   * whose frozen size is recorded per (input, component). */
  const StartTable *start = nullptr;
  if (pass.start_tables != nullptr) {
    start = (attr.component == vm::Component::Pass) ?
                ((attr.input == 0) ? &(*pass.start_tables)[attr.domain] : nullptr) :
                &pass.start_tables->by(attr.input, attr.component, attr.domain);
  }

  if (attr.count) {
    /* `WR-34` promise 3 / `WR-41`: a component with no prim table reports 0 - that is a value,
     * not an error. The number is the one this evaluation started with: the counting pass grows
     * the real table long before the code runs, so asking the live table here reported the grown
     * count (measured: 16 where `WR-41` says 8) - and worse, the counting pass and the real pass
     * would disagree about a program whose path depends on the count. */
    slot.kind = Slot::Kind::Count;
    slot.value = (start != nullptr) ? float(start->size) :
                 (table.valid ? float(table.size) : 0.0f);
    return true;
  }
  if (geometry == nullptr || !table.valid) {
    return true;
  }

  int domain_size = 0;
  const GAttributeReader reader = other_reader(
      *geometry, component_of(attr.component), attr.name, table.domain, domain_size);
  if (!reader) {
    /* WR-32: no such attribute there. */
    return true;
  }
  const Slot::Kind stored = kind_of_type(reader.varray.type());
  if (stored == Slot::Kind::Unsupported || !declared_type_fits(attr.type, stored)) {
    const std::string where = attr.input == 0 ?
                                  std::string("this stream's") :
                                  ("input " + std::to_string(attr.input) + "'s");
    error = where + " attribute '" + attr.name + "' is stored as " + stored_type_name(stored) +
            ", but the code uses it as " + vm::type_name(attr.type);
    return false;
  }

  slot.reader = reader;
  /* `WR-41`: an index that only exists because *this* run created the element reads as 0, so the
   * bound is the frozen one. Never past the live reader, though: a removal makes the real table
   * (and its attributes) shorter than the count this evaluation started with. */
  const int frozen_size = (start != nullptr) ? start->size : domain_size;
  slot.read_size = frozen_size < domain_size ? frozen_size : domain_size;
  slot.kind = Slot::Kind::ExplicitRead;
  switch (attr.type) {
    case vm::Type::Float:
      slot.rf = reader.varray.typed<float>();
      break;
    case vm::Type::Int:
      slot.ri = reader.varray.typed<int>();
      break;
    case vm::Type::Vector:
      if (stored == Slot::Kind::V2) {
        slot.rv2 = reader.varray.typed<float2>();
      }
      else {
        slot.rv3 = reader.varray.typed<float3>();
      }
      break;
    /* `WR-47` / `WR-53`: the stored type already matches (the fit check above), so these are the
     * exact views - no conversion, which is the point of giving them their own kinds. */
    case vm::Type::Quaternion:
      slot.rq4 = reader.varray.typed<math::Quaternion>();
      break;
    case vm::Type::Matrix:
      slot.rm4 = reader.varray.typed<float4x4>();
      break;
  }

  if (r_source != nullptr) {
    r_source->ctx = &slot;
    r_source->size = slot.read_size;
    r_source->read_f = attr.type == vm::Type::Float ? wrangle_read_f : nullptr;
    r_source->read_i = attr.type == vm::Type::Int ? wrangle_read_i : nullptr;
    r_source->read_v = attr.type == vm::Type::Vector ? wrangle_read_v : nullptr;
    r_source->read_q = attr.type == vm::Type::Quaternion ? wrangle_read_q : nullptr;
    r_source->read_m = attr.type == vm::Type::Matrix ? wrangle_read_m : nullptr;
  }
  return true;
}

/**
 * `WR-35`: which Blender domain an explicit write targets. `AttrDomain::Auto` means this
 * component has no such table at all (a point cloud or an instance set has no prim table), so
 * every write into it is dropped - the write-side twin of "reads 0".
 */
static bke::AttrDomain write_target_domain(const vm::ReadDomain want,
                                           const GeometryComponent::Type component)
{
  if (want == vm::ReadDomain::Point) {
    return component == GeometryComponent::Type::Instance ? bke::AttrDomain::Instance :
                                                           bke::AttrDomain::Point;
  }
  switch (component) {
    case GeometryComponent::Type::Mesh:
      return bke::AttrDomain::Face;
    case GeometryComponent::Type::Curve:
      return bke::AttrDomain::Curve;
    default:
      return bke::AttrDomain::Auto;
  }
}

/**
 * `WR-24` (the silent list): an indexed write does nothing when the component the `domain` word
 * named has no such table, when the attribute is stored with a type the node cannot write, or when
 * the value's type does not fit what is stored. None of those is an error - the caller binds a
 * dropped write and the geometry is untouched.
 */
static bool indexed_write_dropped(bke::MutableAttributeAccessor &attributes,
                                  const vm::AttrBinding &attr,
                                  const GeometryComponent::Type component)
{
  const bke::AttrDomain target = write_target_domain(attr.domain, component);
  if (target == bke::AttrDomain::Auto) {
    return true;
  }
  const Slot::Kind existing = detect_kind(attributes, attr.name, target);
  if (existing == Slot::Kind::Unsupported) {
    return true;
  }
  return existing != Slot::Kind::Missing && !declared_type_fits(attr.type, existing);
}

/**
 * `WR-33`/`WR-34`: bind a `setpointattrib` / `setprimattrib` target. The attribute itself is not
 * touched here and not during the run either - the VM buffers each element's writes and
 * `run_domain_on_component` merges them in element order, which is what makes a parallel run
 * deterministic.
 *
 * The stored type wins when the attribute exists (so `WR-6`'s conversions apply); a new
 * attribute is created with the type the written value implies. A span shorter than the domain
 * is not an error: the merge drops out-of-range writes, which is exactly what a component with
 * no such table (an empty span) wants.
 */
/** `WR-22`: the writable attribute storage of one component, or an invalid accessor when the
 * stream does not carry that component (which `WR-24` turns into "do nothing"). */
static std::optional<bke::MutableAttributeAccessor> component_attributes_for_write(
    GeometrySet &geometry, const GeometryComponent::Type component)
{
  switch (component) {
    case GeometryComponent::Type::Mesh:
      if (Mesh *mesh = geometry.get_mesh_for_write()) {
        return mesh->attributes_for_write();
      }
      break;
    case GeometryComponent::Type::Curve:
      if (Curves *curves = geometry.get_curves_for_write()) {
        return curves->geometry.wrap().attributes_for_write();
      }
      break;
    case GeometryComponent::Type::PointCloud:
      if (PointCloud *points = geometry.get_pointcloud_for_write()) {
        return points->attributes_for_write();
      }
      break;
    case GeometryComponent::Type::Instance:
      if (bke::Instances *instances = geometry.get_instances_for_write()) {
        return instances->attributes_for_write();
      }
      break;
    default:
      break;
  }
  return std::nullopt;
}

static bool bind_indexed_write(Slot &slot,
                               const vm::AttrBinding &attr,
                               GeometrySet &geometry,
                               const PassInputs &pass,
                               std::string &error)
{
  slot.attr = &attr;
  slot.kind = Slot::Kind::DroppedWrite;
  /* `WR-22`: the write goes to the component the `domain` word named, which need not be the one
   * this pass runs over - so both the table and the attribute storage come from *that* component.
   * A component the stream does not have is `WR-24`'s silent case: nothing is written. */
  const GeometryComponent::Type component = component_of(attr.component);
  std::optional<bke::MutableAttributeAccessor> storage =
      component_attributes_for_write(geometry, component);
  if (!storage.has_value()) {
    return true;
  }
  bke::MutableAttributeAccessor attributes = *storage;
  const bke::AttrDomain target = write_target_domain(attr.domain, component);
  if (target == bke::AttrDomain::Auto) {
    return true; /* `WR-24`: no such table - do nothing */
  }
  if (indexed_write_dropped(attributes, attr, component)) {
    return true; /* `WR-24`: the stored type does not fit - do nothing */
  }
  (void)error;
  (void)pass;
  const Slot::Kind existing = detect_kind(attributes, attr.name, target);
  const Slot::Kind kind = existing == Slot::Kind::Missing ? (
                              attr.type == vm::Type::Vector ?
                                  Slot::Kind::V3 :
                                  (attr.type == vm::Type::Quaternion ?
                                       Slot::Kind::Q4 :
                                       (attr.type == vm::Type::Matrix ? Slot::Kind::M4 :
                                                                        Slot::Kind::F))) :
                                                          existing;
  slot.kind = kind;
  switch (kind) {
    case Slot::Kind::F:
      slot.wf = attributes.lookup_or_add_for_write_span<float>(attr.name, target);
      slot.f = slot.wf.span;
      break;
    case Slot::Kind::I:
      slot.wi = attributes.lookup_or_add_for_write_span<int>(attr.name, target);
      slot.i = slot.wi.span;
      break;
    case Slot::Kind::B:
      slot.wb = attributes.lookup_or_add_for_write_span<bool>(attr.name, target);
      slot.b = slot.wb.span;
      break;
    case Slot::Kind::V3:
      slot.wv3 = attributes.lookup_or_add_for_write_span<float3>(attr.name, target);
      slot.v3 = slot.wv3.span;
      break;
    case Slot::Kind::V2:
      slot.wv2 = attributes.lookup_or_add_for_write_span<float2>(attr.name, target);
      slot.v2 = slot.wv2.span;
      break;
    /* `WR-47` / `WR-53`: the two attribute types the rotation and matrix functions write. */
    case Slot::Kind::Q4:
      slot.wq4 = attributes.lookup_or_add_for_write_span<math::Quaternion>(attr.name, target);
      slot.q4 = slot.wq4.span;
      break;
    case Slot::Kind::M4:
      slot.wm4 = attributes.lookup_or_add_for_write_span<float4x4>(attr.name, target);
      slot.m4 = slot.wm4.span;
      break;
    default:
      break;
  }
  return true;
}

/**
 * `WR-36`…`WR-39`: a channel is a **value of the node itself** - not an attribute, not geometry, and
 * nothing in a pass can change it. The host read it once before the run (`resolve_channels`), so the
 * bind only has to put it in the register file the function name fixed. Everything that can go
 * wrong (no such name, a type that does not fit) already reads as 0 by then.
 */
static void bind_channel(Slot &slot,
                         const vm::AttrBinding &attr,
                         const int attr_index,
                         const PassInputs &pass)
{
  slot.attr = &attr;
  const ChannelValue value = (attr_index < pass.channels.size()) ? pass.channels[attr_index] :
                                                                   ChannelValue{};
  if (attr.type == vm::Type::Vector) {
    slot.kind = Slot::Kind::ChannelV;
    slot.value_v = value.vector;
  }
  else if (attr.type == vm::Type::Quaternion) {
    slot.kind = Slot::Kind::ChannelQ;
    for (int k = 0; k < 4; k++) {
      slot.value_q[k] = value.quat[k];
    }
  }
  else if (attr.type == vm::Type::Matrix) {
    slot.kind = Slot::Kind::ChannelM;
    for (int k = 0; k < 16; k++) {
      slot.value_m[k] = value.matrix[k];
    }
  }
  else {
    slot.kind = Slot::Kind::Channel;
    slot.value = value.scalar;
  }
}

static bool bind_slot(Slot &slot,
                      const vm::AttrBinding &attr,
                      const int attr_index,
                      bke::MutableAttributeAccessor &attributes,
                      const WrangleDomain domain,
                      const Span<int> corner_verts,
                      const int element_count,
                      std::string &error,
                      const MutableSpan<float4x4> transforms,
                      const PassInputs &pass,
                      vm::ReadSource *r_source = nullptr,
                      /* WR-34: the numbering pass must not touch any attribute. */
                      const bool count_only = false)
{
  const bke::AttrDomain attr_domain = to_attr_domain(domain);
  slot.attr = &attr;

  /* `WR-36`: a channel is a value of the node, so no attribute is created, read or written for it. */
  if (attr.channel) {
    bind_channel(slot, attr, attr_index, pass);
    return true;
  }

  if (count_only && attr.write && !attr.count && !attr.indexed_write) {
    /* `WR-34`: the counting pass only counts, so a `@` write becomes a slot that does nothing -
     * which also keeps it from creating the attribute before the real pass can size it. */
    slot.kind = Slot::Kind::DroppedWrite;
    return true;
  }

  /* WR-33: `setpointattrib` / `setprimattrib` are neither a per-element `@` write nor a read:
   * they write into their own table (WR-35) and the host merges them after the run. */
  if (attr.indexed_write) {
    /* `WR-22`: the write target is the named component of the geometry being written (input 0). */
    if (pass.own_write == nullptr) {
      slot.kind = Slot::Kind::DroppedWrite;
      return true; /* `WR-24`: nothing writable in this pass - do nothing */
    }
    return bind_indexed_write(slot, attr, *pass.own_write, pass, error);
  }

  /* WR-31: the explicit-index functions carry their own table (WR-35) instead of the run-over
   * domain - which is exactly why they are the way to read geometry from a Detail run. */
  if (attr.count || attr.domain != vm::ReadDomain::RunOver) {
    return bind_explicit_read(slot, attr, pass, r_source, error);
  }
  if (attr.host_supplied) {
    slot.kind = Slot::Kind::Host;
    return true;
  }

  if (attr.name == "position") {
    if (domain == WrangleDomain::Instance) {
      /* WR-23: an instance's `@P` is its translation, so it is read and written through the
       * transform instead of through a stored attribute. */
      if (transforms.is_empty()) {
        error = "the instance transforms are not available in this pass";
        return false;
      }
      slot.kind = Slot::Kind::InstancePosition;
      slot.transforms = transforms;
      return true;
    }
    /* WR-23: an edge, a face or a spline has no position of its own, so `@P` does not exist
     * there at all - using it is a compile error rather than a silent zero. */
    if (!domain_has_position(domain)) {
      error =
          "@P does not exist for this Run Over kind: only Points (read and write), "
          "Face Corners (read-only) and Instances have a position";
      return false;
    }
    if (domain == WrangleDomain::Corner) {
      if (attr.write) {
        error =
            "@P is read-only when Run Over is Face Corners: several corners share one point, "
            "so writing it would be a race. Move points with Run Over = Points instead.";
        return false;
      }
      slot.kind = Slot::Kind::PositionViaCorner;
      slot.corner_verts = corner_verts;
      slot.wv3 = attributes.lookup_or_add_for_write_span<float3>("position", bke::AttrDomain::Point);
      slot.v3 = slot.wv3.span;
      return writer_ok(bool(slot.wv3), slot.v3.size(), corner_verts.is_empty() ? 0 : element_count,
                       "position", error);
    }
    slot.kind = Slot::Kind::V3;
    slot.wv3 = attributes.lookup_or_add_for_write_span<float3>("position", attr_domain);
    slot.v3 = slot.wv3.span;
    return writer_ok(bool(slot.wv3), slot.v3.size(), element_count, "position", error);
  }

  const Slot::Kind existing = detect_kind(attributes, attr.name, attr_domain);
  if (existing == Slot::Kind::Unsupported) {
    error = "attribute '" + attr.name +
            "' is stored with a type this node cannot bind (FLOAT, INT, BOOLEAN, FLOAT_VECTOR, "
            "FLOAT2, QUATERNION and FLOAT4X4 are supported)";
    return false;
  }
  if (existing != Slot::Kind::Missing && !declared_type_fits(attr.type, existing)) {
    error = "attribute '" + attr.name + "' is stored as " + stored_type_name(existing) +
            ", but the code uses it as " + vm::type_name(attr.type);
    return false;
  }
  if (existing == Slot::Kind::Missing) {
    if (!attr.write) {
      /* VEX reads an unknown attribute as zero; nothing is created. */
      slot.kind = attr.type == vm::Type::Vector ?
                      Slot::Kind::ZeroV :
                      (attr.type == vm::Type::Quaternion ?
                           Slot::Kind::ZeroQ :
                           (attr.type == vm::Type::Matrix ? Slot::Kind::ZeroM : Slot::Kind::ZeroF));
      return true;
    }
    /* `v@Cd = ...` creates the attribute, with the type the code implies. */
    switch (attr.type) {
      case vm::Type::Float:
        slot.kind = Slot::Kind::F;
        slot.wf = attributes.lookup_or_add_for_write_span<float>(attr.name, attr_domain);
        slot.f = slot.wf.span;
        return writer_ok(bool(slot.wf), slot.f.size(), element_count, attr.name, error);
      case vm::Type::Int:
        slot.kind = Slot::Kind::I;
        slot.wi = attributes.lookup_or_add_for_write_span<int>(attr.name, attr_domain);
        slot.i = slot.wi.span;
        return writer_ok(bool(slot.wi), slot.i.size(), element_count, attr.name, error);
      case vm::Type::Vector:
        slot.kind = Slot::Kind::V3;
        slot.wv3 = attributes.lookup_or_add_for_write_span<float3>(attr.name, attr_domain);
        slot.v3 = slot.wv3.span;
        return writer_ok(bool(slot.wv3), slot.v3.size(), element_count, attr.name, error);
      /* `WR-47` / `WR-53`: `q@rot = ...` / `m@xform = ...` create Blender's own QUATERNION and
       * FLOAT4X4 attributes - no new data types anywhere. */
      case vm::Type::Quaternion:
        slot.kind = Slot::Kind::Q4;
        slot.wq4 = attributes.lookup_or_add_for_write_span<math::Quaternion>(attr.name, attr_domain);
        slot.q4 = slot.wq4.span;
        return writer_ok(bool(slot.wq4), slot.q4.size(), element_count, attr.name, error);
      case vm::Type::Matrix:
        slot.kind = Slot::Kind::M4;
        slot.wm4 = attributes.lookup_or_add_for_write_span<float4x4>(attr.name, attr_domain);
        slot.m4 = slot.wm4.span;
        return writer_ok(bool(slot.wm4), slot.m4.size(), element_count, attr.name, error);
    }
    return true;
  }

  slot.kind = existing;
  switch (existing) {
    case Slot::Kind::F:
      slot.wf = attributes.lookup_or_add_for_write_span<float>(attr.name, attr_domain);
      slot.f = slot.wf.span;
      return writer_ok(bool(slot.wf), slot.f.size(), element_count, attr.name, error);
    case Slot::Kind::I:
      slot.wi = attributes.lookup_or_add_for_write_span<int>(attr.name, attr_domain);
      slot.i = slot.wi.span;
      return writer_ok(bool(slot.wi), slot.i.size(), element_count, attr.name, error);
    case Slot::Kind::B:
      slot.wb = attributes.lookup_or_add_for_write_span<bool>(attr.name, attr_domain);
      slot.b = slot.wb.span;
      return writer_ok(bool(slot.wb), slot.b.size(), element_count, attr.name, error);
    case Slot::Kind::V3:
      slot.wv3 = attributes.lookup_or_add_for_write_span<float3>(attr.name, attr_domain);
      slot.v3 = slot.wv3.span;
      return writer_ok(bool(slot.wv3), slot.v3.size(), element_count, attr.name, error);
    case Slot::Kind::V2:
      slot.wv2 = attributes.lookup_or_add_for_write_span<float2>(attr.name, attr_domain);
      slot.v2 = slot.wv2.span;
      return writer_ok(bool(slot.wv2), slot.v2.size(), element_count, attr.name, error);
    case Slot::Kind::Q4:
      slot.wq4 = attributes.lookup_or_add_for_write_span<math::Quaternion>(attr.name, attr_domain);
      slot.q4 = slot.wq4.span;
      return writer_ok(bool(slot.wq4), slot.q4.size(), element_count, attr.name, error);
    case Slot::Kind::M4:
      slot.wm4 = attributes.lookup_or_add_for_write_span<float4x4>(attr.name, attr_domain);
      slot.m4 = slot.wm4.span;
      return writer_ok(bool(slot.wm4), slot.m4.size(), element_count, attr.name, error);
    default:
      break;
  }
  return true;
}

/* ------------------------------------------------------------------ */
/** \name Per-element load / store / run                                */
/* ------------------------------------------------------------------ */

static void load_element(const vm::Program &program,
                         const Span<Slot> slots,
                         const int index,
                         const int count,
                         vm::Frame &frame)
{
  for (const int slot_index : IndexRange(program.attrs.size())) {
    const Slot &slot = slots[slot_index];
    const int reg = slot.attr->reg;
    switch (slot.kind) {
      case Slot::Kind::Count:
        /* `npoints` / `nprimitives`: one number, so it is a host-filled register (`WR-31`). */
        frame.s[reg] = slot.value;
        break;
      case Slot::Kind::Channel:
        /* `WR-36`: the channel value the host read before the run - a register load per element. */
        frame.s[reg] = slot.value;
        break;
      case Slot::Kind::ChannelV:
        frame.v[reg] = slot.value_v;
        break;
      /* `WR-36`: `chq` / `chm` land in the same register files a `q@` / `m@` read does. */
      case Slot::Kind::ChannelQ:
        for (int k = 0; k < 4; k++) {
          frame.q[reg * 4 + k] = slot.value_q[k];
        }
        break;
      case Slot::Kind::ChannelM:
        for (int k = 0; k < 16; k++) {
          frame.m[reg * 16 + k] = slot.value_m[k];
        }
        break;
      case Slot::Kind::ExplicitRead:
        /* `WR-31`: the VM fetches these itself, at the index the code computes. */
        break;
      case Slot::Kind::IndexedWrite:
      case Slot::Kind::DroppedWrite:
        /* `WR-33`/`WR-34`: nothing to pre-load - the value comes from the store op and is
         * applied after the run, in element order. */
        break;
      case Slot::Kind::Host:
        frame.s[reg] = slot.attr->name == "numelem" ? float(count) : float(index);
        break;
      case Slot::Kind::F:
        frame.s[reg] = slot.f[index];
        break;
      case Slot::Kind::I:
        frame.s[reg] = float(slot.i[index]);
        break;
      case Slot::Kind::B:
        frame.s[reg] = slot.b[index] ? 1.0f : 0.0f;
        break;
      case Slot::Kind::V3: {
        const float3 value = slot.v3[index];
        frame.v[reg] = vm::Vec3{value.x, value.y, value.z};
        break;
      }
      case Slot::Kind::V2: {
        const float2 value = slot.v2[index];
        frame.v[reg] = vm::Vec3{value.x, value.y, 0.0f};
        break;
      }
      /* `WR-47` / `WR-53`: the two transform attribute types. `math::Quaternion` keeps the real
       * part first (w, x, y, z) and `float4x4` is four columns - the same order the VM uses. */
      case Slot::Kind::Q4: {
        const math::Quaternion value = slot.q4[index];
        for (int k = 0; k < 4; k++) {
          frame.q[reg * 4 + k] = k == 0 ? value.w : (k == 1 ? value.x : (k == 2 ? value.y : value.z));
        }
        break;
      }
      case Slot::Kind::M4: {
        const float4x4 value = slot.m4[index];
        for (int column = 0; column < 4; column++) {
          for (int row = 0; row < 4; row++) {
            frame.m[reg * 16 + column * 4 + row] = value[column][row];
          }
        }
        break;
      }
      case Slot::Kind::PositionViaCorner: {
        const float3 value = slot.v3[slot.corner_verts[index]];
        frame.v[reg] = vm::Vec3{value.x, value.y, value.z};
        break;
      }
      case Slot::Kind::InstancePosition: {
        const float3 value = slot.transforms[index].location();
        frame.v[reg] = vm::Vec3{value.x, value.y, value.z};
        break;
      }
      case Slot::Kind::ZeroF:
      case Slot::Kind::Missing:
      case Slot::Kind::Unsupported:
        frame.s[reg] = 0.0f;
        break;
      case Slot::Kind::ZeroV:
        frame.v[reg] = vm::Vec3{0.0f, 0.0f, 0.0f};
        break;
      case Slot::Kind::ZeroQ:
        /* `WR-47`: reading an absent rotation gives (0,0,0,0) - the all-zero quaternion. */
        for (int k = 0; k < 4; k++) {
          frame.q[reg * 4 + k] = 0.0f;
        }
        break;
      case Slot::Kind::ZeroM:
        /* `WR-53`: reading an absent matrix gives all zeros. */
        for (int k = 0; k < 16; k++) {
          frame.m[reg * 16 + k] = 0.0f;
        }
        break;
    }
  }
}

static void store_element(const vm::Program &program,
                          const Span<Slot> slots,
                          const int index,
                          const vm::Frame &frame)
{
  for (const int slot_index : IndexRange(program.attrs.size())) {
    const Slot &slot = slots[slot_index];
    if (!slot.attr->write) {
      continue;
    }
    const int reg = slot.attr->reg;
    switch (slot.kind) {
      case Slot::Kind::F:
        slot.f[index] = frame.s[reg];
        break;
      case Slot::Kind::I:
        slot.i[index] = int(std::trunc(frame.s[reg]));
        break;
      case Slot::Kind::B:
        slot.b[index] = frame.s[reg] != 0.0f;
        break;
      case Slot::Kind::V3:
        slot.v3[index] = float3(frame.v[reg].x, frame.v[reg].y, frame.v[reg].z);
        break;
      case Slot::Kind::InstancePosition:
        /* WR-23: writing `@P` on an instance moves it (only the translation, not the
         * rotation or scale that the transform also carries). */
        slot.transforms[index].location() = float3(
            frame.v[reg].x, frame.v[reg].y, frame.v[reg].z);
        break;
      case Slot::Kind::V2:
        slot.v2[index] = float2(frame.v[reg].x, frame.v[reg].y);
        break;
      /* `WR-47` / `WR-53`: the two transform attribute types, written back unchanged. */
      case Slot::Kind::Q4:
        slot.q4[index] = math::Quaternion(frame.q[reg * 4 + 0],
                                          frame.q[reg * 4 + 1],
                                          frame.q[reg * 4 + 2],
                                          frame.q[reg * 4 + 3]);
        break;
      case Slot::Kind::M4: {
        float4x4 value;
        for (int column = 0; column < 4; column++) {
          for (int row = 0; row < 4; row++) {
            value[column][row] = frame.m[reg * 16 + column * 4 + row];
          }
        }
        slot.m4[index] = value;
        break;
      }
      default:
        break;
    }
  }
}

/** `WR-34`: one buffered write plus the element that made it. Sorting these by element is what
 * turns a parallel run into the documented order (source element, then call order inside it). */
struct TaggedWrite {
  int element = 0;
  vm::WriteEntry write;
};

/** `WR-36`: one `addprim` request, with the element that made it. */
struct TaggedTopology {
  int element = 0;
  vm::TopologyEntry entry;
};

/** `WR-28`/`WR-29`: one removal request, with the element that made it and the component its
 * `domain` word named (which need not be the one this pass runs over). */
struct TaggedRemoval {
  int element = 0;
  vm::AttrBinding::Topology kind = vm::AttrBinding::Topology::None;
  int index = 0;
  vm::Component component = vm::Component::Pass;
};

struct RunResult {
  bool ran = false;
  bool budget_exceeded = false;
  std::string error;
  /** `WR-34`: everything the elements buffered, ready to be merged in element order. */
  std::vector<TaggedWrite> writes;
  /** `WR-34` promise 1: how many points each element created, indexed by element number. Filled
   * by the counting pass; the real pass reports nothing here. `WR-25` gives one pair per point
   * table, so a program that adds to both gets both tables grown. */
  std::vector<int> new_points;
  std::vector<int> new_points_cloud;
  /** `WR-25`: every `addpoint` call of the run, with the position it asked for. */
  std::vector<vm::Frame::AddPointEntry> added_points;
  /** `WR-36`: the same for new primitives. */
  std::vector<int> new_prims;
  /** `WR-36`/`WR-37`: the topology requests, in element order, ready for the merge. */
  std::vector<TaggedTopology> topology;
  std::vector<TaggedRemoval> removals;
};

/** Loads, runs and stores the program for every element of `range`. */
/**
 * The scope of one pass (`WR-26`): the field wired into `Selection`, or "everything" when
 * nothing is wired - and in that case no field is evaluated at all.
 */
struct SelectionFilter {
  const Field<bool> *field = nullptr;
  bool linked = false;
};

/** The elements to run on: all of them when `Selection` is empty (`WR-26`). */
static IndexMask selection_mask(const SelectionFilter &filter,
                               const fn::FieldContext &context,
                               const int domain_size,
                               IndexMaskMemory &memory)
{
  if (!filter.linked || domain_size == 0) {
    return IndexMask(IndexRange(domain_size));
  }
  fn::FieldEvaluator evaluator{context, domain_size};
  evaluator.add(*filter.field);
  evaluator.evaluate();
  return IndexMask::from_bools(evaluator.get_evaluated<bool>(0), memory);
}

/**
 * Loads, runs and stores the program for every element of `mask`.
 *
 * `element_count` is the size of the *whole* domain, not of the mask: `@numelem` must not
 * change just because a selection narrowed the work (`WR-27`), and the indices the mask
 * yields are the real element numbers, which is what `@elemnum` reports.
 */
static RunResult run_over_range(const vm::Program &program,
                                const IndexMask &mask,
                                const int element_count,
                                const Span<int> corner_verts,
                                const Span<Slot> slots,
                                const Span<vm::ReadSource> sources,
                                /* `WR-34` promise 1: the numbering base of each element, one
                                 * span per point table (`WR-25`). Empty for a run that cannot
                                 * create anything. */
                                const Span<int> new_point_bases = {},
                                const Span<int> new_point_cloud_bases = {},
                                /* `WR-34`: count what the elements would create, touch nothing. */
                                const bool count_only = false)
{
  RunResult result;
  if (mask.is_empty()) {
    result.ran = true;
    return result;
  }

  threading::EnumerableThreadSpecific<vm::Frame> frames;
  std::atomic<bool> budget_hit{false};
  /* WR-34: the elements' buffered writes, tagged with the element that made them. */
  std::mutex writes_mutex;
  std::vector<TaggedWrite> collected_writes;
  std::vector<vm::Frame::AddPointEntry> collected_points;
  std::vector<int> new_point_counts(size_t(element_count), 0);
  std::vector<int> new_point_cloud_counts(size_t(element_count), 0);
  std::vector<int> new_prim_counts(size_t(element_count), 0);
  std::vector<TaggedTopology> collected_topology;
  std::vector<TaggedRemoval> collected_removals;
  /* Iterate the mask (not `parallel_for(range, …)`: there is no IndexMask overload of that)
   * so a narrowed `Selection` costs only the elements it selects (`WR-26`). */
  mask.foreach_segment(
      [&](const IndexMaskSegment segment, const int64_t /*segment_pos*/) {
        vm::Frame &frame = frames.local();
        /* WR-31: the read sources are per component, so they are installed here rather than
         * once per thread. */
        frame.ensure(program, sources.data(), int(sources.size()));
        for (const int64_t index : segment) {
          /* WR-25: one base per point table, so the numbers of the mesh's new points and the point
           * cloud's new points are handed out independently. */
          frame.new_point_base[0] = (index < int64_t(new_point_bases.size())) ?
                                        new_point_bases[int(index)] :
                                        0;
          frame.new_point_base[1] = (index < int64_t(new_point_cloud_bases.size())) ?
                                        new_point_cloud_bases[int(index)] :
                                        0;
          frame.new_points[0] = 0;
          frame.new_points[1] = 0;
          load_element(program, slots, int(index), element_count, frame);
          if (vm::run(program, frame) != vm::RunStatus::Ok) {
            budget_hit = true;
            return;
          }
          store_element(program, slots, int(index), frame);
          if (count_only) {
            /* `WR-34` promise 1: this pass only records how many points the element would create
             * and throws its writes away, so nothing is created before the table is sized. */
            frame.writes.clear();
            frame.added_points.clear();
            std::lock_guard<std::mutex> lock(writes_mutex);
            if (int(index) < int(new_point_counts.size())) {
              new_point_counts[int(index)] = frame.new_points[0];
              new_point_cloud_counts[int(index)] = frame.new_points[1];
              new_prim_counts[int(index)] = frame.new_prims;
            }
            /* WR-36: the counting pass is also where the primitives get built, so its requests
             * are the ones the host applies (the real pass sees the same ones). */
            for (const vm::TopologyEntry &entry : frame.topology) {
              collected_topology.push_back(TaggedTopology{int(index), entry});
            }
            for (const vm::Frame::RemovalEntry &removed : frame.removed_points) {
              collected_removals.push_back(TaggedRemoval{int(index),
                                                         vm::AttrBinding::Topology::RemovePoint,
                                                         removed.index,
                                                         program.attrs[size_t(removed.slot)].component});
            }
            for (const vm::Frame::RemovalEntry &removed : frame.removed_prims) {
              collected_removals.push_back(TaggedRemoval{int(index),
                                                         vm::AttrBinding::Topology::RemovePrim,
                                                         removed.index,
                                                         program.attrs[size_t(removed.slot)].component});
            }
            frame.topology.clear();
            frame.removed_points.clear();
            frame.removed_prims.clear();
            continue;
          }
          /* WR-34: harvest this element's buffered writes. The lock is taken once per element
           * that actually wrote, and the element index rides along so the merge can sort - the
           * order the writes are collected in therefore does not matter. */
          if (!frame.writes.empty() || !frame.topology.empty() || !frame.removed_points.empty() ||
              !frame.removed_prims.empty() || !frame.added_points.empty())
          {
            std::lock_guard<std::mutex> lock(writes_mutex);
            for (const vm::WriteEntry &entry : frame.writes) {
              collected_writes.push_back(TaggedWrite{int(index), entry});
            }
            for (const vm::TopologyEntry &entry : frame.topology) {
              collected_topology.push_back(TaggedTopology{int(index), entry});
            }
            for (const vm::Frame::RemovalEntry &removed : frame.removed_points) {
              collected_removals.push_back(TaggedRemoval{int(index),
                                                         vm::AttrBinding::Topology::RemovePoint,
                                                         removed.index,
                                                         program.attrs[size_t(removed.slot)].component});
            }
            for (const vm::Frame::RemovalEntry &removed : frame.removed_prims) {
              collected_removals.push_back(TaggedRemoval{int(index),
                                                         vm::AttrBinding::Topology::RemovePrim,
                                                         removed.index,
                                                         program.attrs[size_t(removed.slot)].component});
            }
            if (!frame.added_points.empty()) {
              /* WR-25: the positions are applied after the run, in the same element order the
               * other topology work uses. */
              for (const vm::Frame::AddPointEntry &entry : frame.added_points) {
                collected_points.push_back(entry);
              }
              frame.added_points.clear();
            }
            frame.writes.clear();
            frame.topology.clear();
            frame.removed_points.clear();
            frame.removed_prims.clear();
          }
        }
      },
      exec_mode::parallel);

  /* WR-34: element order first, and a stable sort keeps each element's own call order. */
  std::stable_sort(collected_writes.begin(),
                   collected_writes.end(),
                   [](const TaggedWrite &a, const TaggedWrite &b) {
                     return a.element < b.element;
                   });
  std::stable_sort(collected_topology.begin(),
                   collected_topology.end(),
                   [](const TaggedTopology &a, const TaggedTopology &b) {
                     return a.element < b.element;
                   });
  std::stable_sort(collected_removals.begin(),
                   collected_removals.end(),
                   [](const TaggedRemoval &a, const TaggedRemoval &b) {
                     return a.element < b.element;
                   });
  result.writes = std::move(collected_writes);
  result.new_points = std::move(new_point_counts);
  result.new_points_cloud = std::move(new_point_cloud_counts);
  result.new_prims = std::move(new_prim_counts);
  result.topology = std::move(collected_topology);
  result.removals = std::move(collected_removals);
  result.added_points = std::move(collected_points);

  result.ran = true;
  result.budget_exceeded = budget_hit;
  if (result.budget_exceeded) {
    result.error =
        "the code did not finish for some elements (step budget exceeded) - check for an "
        "endless loop";
  }
  return result;
}

/* ------------------------------------------------------------------ */
/** \name Node                                                          */
/* ------------------------------------------------------------------ */


namespace {

/** `WR-32`: where the compiler said the error is, keyed by the code it compiled.
 *
 * The position is a pure function of the code, so the code is the right key: the exec fills this in
 * when a compile fails and the draw - which holds the same string - reads it back. Nothing is stored
 * on the node, and code that compiles simply has no entry. */
std::mutex g_highlight_error_mutex;
std::unordered_map<std::string, std::pair<int, int>> g_highlight_error_positions;

void remember_highlight_error(const std::string &code, const std::vector<vm::Error> &errors)
{
  std::lock_guard<std::mutex> lock(g_highlight_error_mutex);
  if (errors.empty()) {
    g_highlight_error_positions.erase(code);
    return;
  }
  g_highlight_error_positions[code] = {errors.front().line, errors.front().column};
}

std::pair<int, int> highlight_error_position(const std::string &code)
{
  std::lock_guard<std::mutex> lock(g_highlight_error_mutex);
  const auto found = g_highlight_error_positions.find(code);
  return (found == g_highlight_error_positions.end()) ? std::pair<int, int>{0, 0} : found->second;
}

}  // namespace

static void draw_code_socket(CustomSocketDrawParams &params)
{
  /* A linked string socket must show the incoming value rather than an editor that
   * silently has no effect. */
  if (params.socket.is_logically_linked()) {
    params.draw_standard(params.layout);
    return;
  }
  /* `WR-31`: the one text box in Blender that asks for syntax colouring. `WR-32`: if this exact
   * code was the one the compiler rejected, the position it complained about comes along - the
   * highlight itself still never judges the code. `WR-41`: and the one that asks for a
   * line-number gutter. */
  const auto *code_value = static_cast<const bNodeSocketValueString *>(params.socket.default_value);
  const std::string code_text = (code_value != nullptr) ? code_value->value : "";
  const std::pair<int, int> error_position = highlight_error_position(code_text);
  params.layout.textbox(&params.C,
                        &params.socket_ptr,
                        "default_value",
                        IFACE_("// VEX-like code, e.g. @P.y += sin(@P.x) * 0.5;"),
                        code_visible_lines,
                        true,
                        error_position.first,
                        error_position.second,
                        true);
}

static void node_declare(NodeDeclarationBuilder &b)
{
  const bNodeTree *tree = b.tree_or_null();
  const bNode *node = b.node_or_null();
  b.add_input<decl::Geometry>("Geometry"_ustr)
      .is_default_link_socket()
      .description("Geometry stream the code runs on");
  /* WR-16/WR-20: three more streams, read-only, reachable through the explicit-index functions
   * with geo 1..3 (`fpoint(1, "mesh", "P", i)`). An unconnected one is empty geometry. The
   * `@opinput<n>_…` syntax is retired (明确不做). */
  b.add_input<decl::Geometry>("Input 1"_ustr, "INPUT_1"_ustr)
      .description("Second geometry stream, read with fpoint/fprim(1, ...) (read-only)");
  b.add_input<decl::Geometry>("Input 2"_ustr, "INPUT_2"_ustr)
      .description("Third geometry stream, read with fpoint/fprim(2, ...) (read-only)");
  b.add_input<decl::Geometry>("Input 3"_ustr, "INPUT_3"_ustr)
      .description("Fourth geometry stream, read with fpoint/fprim(3, ...) (read-only)");
  /* WR-26: unwired = every element, so an existing tree keeps behaving exactly as before. */
  b.add_input<decl::Bool>("Selection"_ustr)
      .default_value(true)
      .evaluated_geometry_field()
      .description("Which elements the code runs on; empty means all of them");
  b.add_input<decl::Menu>("Run Over"_ustr)
      .static_items(run_over_items)
      .default_value(MenuValue(RunOver::Vertex))
      .optional_label()
      .description("Which element kind the code runs on");
  /* WR-35: one value socket per channel, named by the item. The name follows the item's own name
   * (`.socket_name_ptr`), so renaming a channel in the panel renames its socket. */
  if (node != nullptr && node->storage != nullptr) {
    const NodeGeometryPDTWrangle &storage = node_storage(*node);
    for (const NodeGeometryPDTWrangleItem &item : Span(storage.items, storage.items_num)) {
      const UString name(item.name);
      const UString identifier(PDTWrangleItemsAccessor::socket_identifier_for_item(item));
      b.add_input(PDTWrangleItemsAccessor::get_socket_type(item), name, identifier)
          .socket_name_ptr(tree != nullptr ? &tree->id : nullptr,
                           *PDTWrangleItemsAccessor::item_srna,
                           &item,
                           "name")
          .structure_type(StructureType::Single)
          .description("Channel the code reads with chf / chi / chv");
    }
  }
  b.add_input<decl::String>("Code"_ustr)
      .custom_draw(draw_code_socket)
      .description("Code to run over the selected elements");
  /* The extend socket ("+" / drop target for `insert_link`) must be the **last** input: the lazy
   * function graph skips sockets whose type has no `geometry_nodes_default_value`
   * (`NodeSocketVirtual` is one) while `GeoNodeExecParams::get_input_index()` only skips
   * unavailable ones - a value socket *after* it would be off by one and crash the evaluation. */
  if (node != nullptr && node->storage != nullptr) {
    b.add_input<decl::Extend>(""_ustr, "__extend__"_ustr)
        .structure_type(StructureType::Single)
        .custom_draw(socket_items::ui::draw_extend_socket_fn<PDTWrangleItemsAccessor>());
  }
  b.add_output<decl::Geometry>("Geometry"_ustr);
}

/** Binds the program for one component and runs it over `range`. */
/**
 * `WR-37`: apply the removal requests to a mesh or a curve.
 *
 * Blender's own delete is a separation whose kept part is written back into the geometry set, so
 * the component is replaced. `WR-37` checks the request against the *input* tables separately
 * (`validate_removals`), so a bad request is still a node error before any writer exists.
 * The selection has to be a field, so the computed indices go through a temporary boolean
 * attribute.
 */
/** `WR-28`/`WR-29`: the two tables of one component (`valid` is false when the stream does not
 * carry it). */
struct TableSizes {
  int points = 0;
  int prims = 0;
  bool valid = false;
};

static TableSizes table_sizes_in(const GeometrySet &geometry, const vm::Component component)
{
  switch (component) {
    case vm::Component::Mesh:
      if (const Mesh *mesh = geometry.get_mesh()) {
        return TableSizes{mesh->verts_num, mesh->faces_num, true};
      }
      break;
    case vm::Component::Curve:
      if (const Curves *curves = geometry.get_curves()) {
        const bke::CurvesGeometry &curves_geometry = curves->geometry.wrap();
        return TableSizes{curves_geometry.points_num(), curves_geometry.curves_num(), true};
      }
      break;
    case vm::Component::PointCloud:
      if (const PointCloud *points = geometry.get_pointcloud()) {
        return TableSizes{points->totpoint, 0, true};
      }
      break;
    case vm::Component::Instance:
      if (const bke::Instances *instances = geometry.get_instances()) {
        return TableSizes{instances->instances_num(), 0, true};
      }
      break;
    default:
      break;
  }
  return TableSizes{};
}

/**
 * `WR-28`/`WR-29`: the requests that survive. A component the stream does not have, and an index its
 * table does not have, both do nothing - they are the L0's silent cases (they used to be node
 * errors). The `Pass` component (an old-style request) falls back to the stream's mesh or curve.
 */
static Vector<TaggedRemoval> filter_removals(const GeometrySet &geometry,
                                             const Span<TaggedRemoval> removals)
{
  Vector<TaggedRemoval> kept;
  for (const TaggedRemoval &tagged : removals) {
    vm::Component component = tagged.component;
    if (component == vm::Component::Pass) {
      component = (geometry.get_mesh() != nullptr) ? vm::Component::Mesh : vm::Component::Curve;
    }
    const TableSizes sizes = table_sizes_in(geometry, component);
    if (!sizes.valid) {
      continue;
    }
    const int limit = (tagged.kind == vm::AttrBinding::Topology::RemovePoint) ? sizes.points :
                                                                               sizes.prims;
    if (tagged.index < 0 || tagged.index >= limit) {
      continue;
    }
    kept.append(tagged);
  }
  return kept;
}

static bool apply_removals(GeometrySet &geometry,
                           const Span<TaggedRemoval> removals,
                           const NodeAttributeFilter &attribute_filter,
                           std::string &error)
{
  if (removals.is_empty()) {
    return true;
  }
  /* The requests were filtered against the input tables before the run; filtering again here is
   * cheap and keeps this function safe on its own (a valid input number stays valid when the table
   * has only grown since). */
  const Vector<TaggedRemoval> valid = filter_removals(geometry, removals);
  if (valid.is_empty()) {
    return true;
  }
  /* `WR-28`: the target is the component the request named - a mesh, a curve, a point cloud or an
   * instance set. Its two tables are the point-like one and, where it has one, the prim-like one. */
  const vm::Component target = valid[0].component;
  std::optional<bke::MutableAttributeAccessor> storage;
  bke::AttrDomain point_domain = bke::AttrDomain::Point;
  bke::AttrDomain prim_domain = bke::AttrDomain::Face;
  switch (target) {
    case vm::Component::Mesh:
      if (Mesh *mesh = geometry.get_mesh_for_write()) {
        storage = mesh->attributes_for_write();
      }
      break;
    case vm::Component::Curve:
      if (Curves *curves = geometry.get_curves_for_write()) {
        storage = curves->geometry.wrap().attributes_for_write();
        prim_domain = bke::AttrDomain::Curve;
      }
      break;
    case vm::Component::PointCloud:
      if (PointCloud *points = geometry.get_pointcloud_for_write()) {
        storage = points->attributes_for_write();
      }
      break;
    case vm::Component::Instance:
      /* `WR-10`: an instance set's "points" are the instances. */
      if (bke::Instances *instances = geometry.get_instances_for_write()) {
        storage = instances->attributes_for_write();
        point_domain = bke::AttrDomain::Instance;
      }
      break;
    default:
      break;
  }
  if (!storage.has_value()) {
    return true; /* the component went away between the filter and here: nothing to do */
  }

  /* WR-37: the two tables are separate, and removing a face keeps its points - exactly what
   * Blender's own deletion does (measured: point deletion takes the faces with it, face deletion
   * keeps the points). */
  const vm::AttrBinding::Topology kinds[2] = {vm::AttrBinding::Topology::RemovePoint,
                                              vm::AttrBinding::Topology::RemovePrim};
  /* `WR-28`/`WR-29`: a curve's spline table is the Curve domain and its control points go with it;
   * a point cloud and an instance set have only the point-like table, whose domain is theirs. */
  const bke::AttrDomain domains[2] = {point_domain, prim_domain};
  for (int which = 0; which < 2; which++) {
    Vector<int> indices;
    for (const TaggedRemoval &tagged : valid) {
      if (tagged.kind == kinds[which]) {
        indices.append(tagged.index);
      }
    }
    if (indices.is_empty()) {
      continue;
    }
    bke::MutableAttributeAccessor attributes = *storage;
    SpanAttributeWriter<bool> mask = attributes.lookup_or_add_for_write_span<bool>(".pdt_remove",
                                                                                  domains[which]);
    /* `separate_geometry` keeps what the field selects - in every domain, instances included (the
     * Delete Geometry *node* inverts its own selection before calling it, which is what makes its
     * UI read as "delete these"). So the mask is the complement: everything but the removals. */
    mask.span.fill(true);
    for (const int index : indices) {
      if (index < mask.span.size()) {
        mask.span[index] = false;
      }
    }
    mask.finish();
    const Field<bool> selection = bke::AttributeFieldInput::from<bool>(".pdt_remove");
    bool is_error = false;
    geometry::separate_geometry(geometry,
                                domains[which],
                                GEO_NODE_DELETE_GEOMETRY_MODE_ALL,
                                selection,
                                attribute_filter,
                                is_error);
    storage->remove(".pdt_remove");
    if (is_error) {
      error = std::string("the ") + (which == 0 ? "point" : "prim") +
              " removal could not be applied (WR-37)";
      return false;
    }

  }
  return true;
}

/**
 * `WR-36`: the writable attribute accessor of the component `type` **currently** in `geometry`.
 *
 * `join_geometries` replaces or re-copies components, so an accessor taken before it can point at
 * storage the stream no longer carries - every later write then lands in the orphaned copy.
 * Measured on a curve and on a point cloud: `i@np = npoints(0);` next to
 * `addprim(0, "polyline", …)` lost the write on both. Falls back to `previous` when the component
 * is not of that type.
 */
static bke::MutableAttributeAccessor live_attributes(GeometrySet &geometry,
                                                     const GeometryComponent::Type type,
                                                     bke::MutableAttributeAccessor previous)
{
  switch (type) {
    case GeometryComponent::Type::Mesh:
      if (Mesh *mesh = geometry.get_mesh_for_write()) {
        return mesh->attributes_for_write();
      }
      break;
    case GeometryComponent::Type::Curve:
      if (Curves *curves = geometry.get_curves_for_write()) {
        return curves->geometry.wrap().attributes_for_write();
      }
      break;
    case GeometryComponent::Type::PointCloud:
      if (PointCloud *points = geometry.get_pointcloud_for_write()) {
        return points->attributes_for_write();
      }
      break;
    case GeometryComponent::Type::Instance:
      if (bke::Instances *instances = geometry.get_instances_for_write()) {
        return instances->attributes_for_write();
      }
      break;
    default:
      break;
  }
  return previous;
}

/**
 * `WR-36`: build the `polyline` requests into a curve component, in element order.
 *
 * The recipe is Blender's own Points to Curves (`node_geo_points_to_curves.cc:87-113`): create the
 * component with `curves_new_nomain`, set the spline types and offsets, bring the point-domain
 * attributes across with `gather_attributes`, and attach it with `replace_curves`. The referenced
 * numbers are the *source* component's points, whose data is copied into the new splines' own point
 * segments - a curve spline cannot reference points it does not own.
 */
/** `WR-27`: the point table of one component, as the `addprim` source (`pointdomain`). */
struct SourcePoints {
  int size = 0;
  bke::AttributeAccessor attributes;
  /** The domain those attributes live on (`Instance` for an instance set, `Point` otherwise). */
  bke::AttrDomain domain = bke::AttrDomain::Point;
  /** Set when the source is an instance set: `WR-10` reads an instance's `@P` as its translation,
   * and an instance set stores no position attribute, so the copied points take theirs from here. */
  const bke::Instances *instances = nullptr;
};

/**
 * `WR-27`: copy the referenced points' data (their positions included) into a new component's point
 * domain. The work is one `gather_attributes` call - the same one the curve path has always used -
 * plus the instance case, whose positions come from the transforms.
 */
static void copy_point_data(const SourcePoints &source,
                            const Span<int> indices,
                            bke::MutableAttributeAccessor destination,
                            const bke::AttributeFilter &filter)
{
  if (source.instances != nullptr) {
    /* Measured: `gather_attributes` cannot read the *instance* domain - it takes a null read inside
     * Blender's own instance accessor (attribute_access.cc:756 through instances_attributes.cc:114,
     * EXCEPTION_ACCESS_VIOLATION). So an instance source contributes the one thing an instance has
     * that a control point or a vertex can hold: its position, which `WR-10` reads as `@P`, the
     * transform's translation. The instance-domain attributes are not copied (recorded as the gap
     * in this item's coverage). */
    SpanAttributeWriter<float3> positions =
        destination.lookup_or_add_for_write_span<float3>("position", bke::AttrDomain::Point);
    const Span<float4x4> transforms = source.instances->transforms();
    for (const int i : IndexRange(indices.size())) {
      if (indices[i] >= 0 && indices[i] < transforms.size()) {
        positions.span[i] = transforms[indices[i]].location();
      }
    }
    positions.finish();
    return;
  }
  bke::gather_attributes(
      source.attributes, source.domain, bke::AttrDomain::Point, filter, indices, destination);
}

static std::optional<SourcePoints> source_points_in(const GeometrySet &geometry,
                                                    const vm::Component component)
{
  switch (component) {
    case vm::Component::Mesh:
      if (const Mesh *mesh = geometry.get_mesh()) {
        return SourcePoints{mesh->verts_num, mesh->attributes()};
      }
      break;
    case vm::Component::Curve:
      if (const Curves *curves = geometry.get_curves()) {
        return SourcePoints{curves->geometry.wrap().points_num(),
                            curves->geometry.wrap().attributes()};
      }
      break;
    case vm::Component::PointCloud:
      if (const PointCloud *points = geometry.get_pointcloud()) {
        return SourcePoints{points->totpoint, points->attributes()};
      }
      break;
    case vm::Component::Instance:
      /* `WR-27` allows an instance set: its "points" are the instances (`WR-10`'s reading of `@P`). */
      if (const bke::Instances *instances = geometry.get_instances()) {
        return SourcePoints{instances->instances_num(),
                            instances->attributes(),
                            bke::AttrDomain::Instance,
                            instances};
      }
      break;
    default:
      break;
  }
  return std::nullopt;
}

static bool apply_curve_topology(GeometrySet &geometry,
                                 const vm::Program &program,
                                 const PassInputs &pass,
                                 const Span<TaggedTopology> topology,
                                 std::string &error)
{
  Vector<const TaggedTopology *> requests;
  for (const TaggedTopology &tagged : topology) {
    if (program.attrs[size_t(tagged.entry.slot)].polyline) {
      requests.append(&tagged);
    }
  }
  if (requests.is_empty()) {
    return true;
  }
  /* `WR-27`: the numbers are the *named* source component's points (`pointdomain`), and the source
   * data is copied into the new splines' own point segments. All the requests of one program have
   * to name the same source, because one gather writes one point range. */
  const Curves *existing = geometry.get_curves();
  vm::Component source = vm::Component::Pass;
  for (const TaggedTopology *tagged : requests) {
    const vm::Component here = program.attrs[size_t(tagged->entry.slot)].point_component;
    if (source == vm::Component::Pass) {
      source = here;
    }
    else if (here != source) {
      error =
          "addprim: the splines of one program must take their points from the same pointdomain "
          "(WR-27)";
      return false;
    }
  }
  std::optional<SourcePoints> source_data = source_points_in(geometry, source);
  if (!source_data.has_value()) {
    /* A component the stream does not have is reported, since `addprim` has no silent case for a
     * missing source (only the reads and the setters have one). */
    error = "addprim: the component `pointdomain` names is not in this stream (WR-27)";
    return false;
  }
  const int source_points = source_data->size;

  int added_points = 0;
  for (const TaggedTopology *tagged : requests) {
    const int count = int(tagged->entry.points.size());
    if (count < 1) {
      /* `WR-27`: a spline needs at least one control point (a one-point spline is legal). */
      error = "addprim(0, \"curve\", …) needs at least one point (WR-27)";
      return false;
    }
    for (const int point : tagged->entry.points) {
      if (point < 0 || point >= source_points) {
        error = "addprim(0, \"polyline\", …) references point " + std::to_string(point) +
                ", which does not exist (WR-36)";
        return false;
      }
    }
    added_points += count;
  }

  Curves *curves_id = bke::curves_new_nomain(added_points, int(requests.size()));
  bke::CurvesGeometry &curves = curves_id->geometry.wrap();
  curves.fill_curve_types(CURVE_TYPE_POLY);
  MutableSpan<int> offsets = curves.offsets_for_write();
  int at = 0;
  for (const int index : IndexRange(int(requests.size()))) {
    offsets[index] = at;
    at += int(requests[index]->entry.points.size());
  }
  offsets[int(requests.size())] = added_points;

  Array<int> indices(added_points);
  at = 0;
  for (const TaggedTopology *tagged : requests) {
    for (const int point : tagged->entry.points) {
      indices[at++] = point;
    }
  }
  /* The new splines become their own curve component, which is then joined back: join_geometries
   * handles every attribute domain, so nothing of an existing curve is lost - and when the stream
   * has no curve at all, the join is what adds the component. */
  copy_point_data(*source_data, indices, curves.attributes_for_write(), *pass.attribute_filter);
  GeometrySet addition = bke::GeometrySet::from_curves(curves_id);
  GeometrySet joined = geometry::join_geometries({geometry, addition}, {});
  geometry = std::move(joined);
  return true;
}

/**
 * `WR-36`: apply the `addprim` requests to a mesh, in element order. *
 * Validation comes first and reports a node error, which is what `WR-36` asks for on a too-short
 * "poly" or a point number that does not exist ("节点错误 + 原样输出" - so this runs inside the
 * counting pass, before the bind phase has created a single writer). Growing a mesh domain is the
 * recipe recorded next to the point growth, taken from `node_geo_extrude_mesh.cc:140-179`.
 */
/**
 * `WR-27`'s copy case for a mesh target: when `pointdomain` names something other than the mesh
 * (or the mesh is absent), the referenced points are copied into the mesh - their point-domain
 * attributes included - appended after the mesh's own points, and each request's numbers are
 * shifted by the block they landed in. `apply_mesh_topology` then sees the mesh's own numbering and
 * builds the faces as usual.
 */
static bool copy_mesh_target_points(GeometrySet &geometry,
                                    const vm::Program &program,
                                    const PassInputs &pass,
                                    std::vector<TaggedTopology> &topology,
                                    std::string &error)
{
  struct CopyRequest {
    size_t index = 0;
    vm::Component source = vm::Component::Pass;
    int base = 0;
  };
  Vector<CopyRequest> copies;
  int total = 0;
  for (const size_t i : IndexRange(topology.size())) {
    const TaggedTopology &tagged = topology[i];
    const vm::AttrBinding &attr = program.attrs[size_t(tagged.entry.slot)];
    if (attr.polyline || attr.point_component == vm::Component::Pass ||
        attr.point_component == vm::Component::Mesh)
    {
      continue; /* a curve target, or the reuse case - neither copies here */
    }
    const std::optional<SourcePoints> source = source_points_in(geometry, attr.point_component);
    if (!source.has_value()) {
      error = "addprim: the component `pointdomain` names is not in this stream (WR-27)";
      return false;
    }
    for (const int point : tagged.entry.points) {
      if (point < 0 || point >= source->size) {
        error = "addprim(0, \"mesh\", …) references point " + std::to_string(point) +
                ", which the source component does not have (WR-27)";
        return false;
      }
    }
    copies.append(CopyRequest{i, attr.point_component, total});
    total += int(tagged.entry.points.size());
  }
  if (copies.is_empty()) {
    return true;
  }
  /* One addition mesh carries every copied point, in request order. Joining appends them after the
   * mesh's own points (and merges the attributes), while the existing faces keep their indices -
   * the addition has none of its own. */
  const vm::Component source_component = copies[0].source;
  for (const CopyRequest &request : copies) {
    if (request.source != source_component) {
      error =
          "addprim: the faces of one program must copy their points from the same pointdomain "
          "(WR-27)";
      return false;
    }
  }
  const std::optional<SourcePoints> source = source_points_in(geometry, source_component);
  if (!source.has_value()) {
    error = "addprim: the component `pointdomain` names is not in this stream (WR-27)";
    return false;
  }
  Mesh *addition = BKE_mesh_new_nomain(total, 0, 0, 0);
  Array<int> indices(total);
  int at = 0;
  for (const CopyRequest &request : copies) {
    for (const int point : topology[request.index].entry.points) {
      indices[at++] = point;
    }
  }
  copy_point_data(*source, indices, addition->attributes_for_write(), *pass.attribute_filter);

  const Mesh *existing = geometry.get_mesh();
  const int base = (existing != nullptr) ? existing->verts_num : 0;
  GeometrySet joined = geometry::join_geometries({geometry, GeometrySet::from_mesh(addition)}, {});
  geometry = std::move(joined);
  for (const CopyRequest &request : copies) {
    TaggedTopology &tagged = topology[request.index];
    for (int &point : tagged.entry.points) {
      point += base + request.base;
    }
  }
  return true;
}

/**
 * Which half of the mesh-topology work to do.
 *
 * The faces have to be *sized* while the counting pass runs: the face table must already be its
 * final size when the bind phase creates its writers (the same reason the point table is grown
 * there). The corner indices, though, can only be written once every element knows its own
 * new-point base, and that is the real pass - `addpoint` returns `base + offset`, and the base only
 * exists after the counts are in. Recording the primitives in the counting pass therefore built
 * them on 0-based numbers: measured with nine elements each growing a box, the first `addpoint` of
 * every element returned 0 in that pass, so all nine boxes were built on the grid's own first
 * points (the real pass's `addpoint` returned 9, 17, 25, ..., which is what the attribute write
 * showed). `WR-27`'s "the numbers are the ones `addpoint` handed out" only holds in the real pass.
 */
enum class MeshTopologyStage {
  Size,
  Write,
};

static bool apply_mesh_topology(Mesh &mesh,
                                const vm::Program &program,
                                const Span<TaggedTopology> topology,
                                const MeshTopologyStage stage,
                                std::string &error)
{
  if (topology.is_empty()) {
    return true;
  }

  int added_faces = 0;
  int added_corners = 0;
  for (const TaggedTopology &tagged : topology) {
    const vm::AttrBinding &attr = program.attrs[size_t(tagged.entry.slot)];
    if (attr.polyline) {
      /* Not this builder's business: a curve target becomes a spline, which
       * `apply_curve_topology` builds and validates (including "at least two points" and "the
       * point exists"). Erroring here instead - the message this used to carry dates from before
       * the curve path existed - aborted the whole pass, so on a mesh stream the curve was built
       * *and* every `@` write of the same program was lost (measured; E-64/E-68 only ever
       * measured the curve). */
      continue;
    }
    if (int(tagged.entry.points.size()) < 3) {
      error = "addprim(0, \"poly\", …) needs at least three points, got " +
              std::to_string(tagged.entry.points.size()) + " (WR-36)";
      return false;
    }
    if (stage == MeshTopologyStage::Write) {
      /* The range check belongs to the write stage: only there do the numbers mean what the program
       * asked for (the counting pass sees 0-based ones, so it would both miss real errors and
       * invent false ones). */
      for (const int point : tagged.entry.points) {
        if (point < 0 || point >= mesh.verts_num) {
          error = "addprim(0, \"poly\", …) references point " + std::to_string(point) +
                  ", which does not exist (WR-36)";
          return false;
        }
      }
    }
    added_faces += 1;
    added_corners += int(tagged.entry.points.size());
  }

  if (stage == MeshTopologyStage::Size) {
    /* Only the tables are sized here; the corners still carry the counting pass's numbering, so the
     * edges and the topology tag wait for the write stage. */
    if (added_faces == 0 && added_corners == 0) {
      return true;
    }
  }

  if (stage == MeshTopologyStage::Size) {
    /* Grow the tables so the bind phase sees their final size, and leave the mesh *consistent*
     * while doing it: the real pass runs before the corners can be written, and it reads the edge
     * table (measured: skipping the edge rebuild here left it short and the depsgraph died with an
     * access violation). The corners are placeholders - the counting pass's `addpoint` numbers are
     * element-relative, not the real ones - so anything out of range becomes 0 and the edge builder
     * cannot walk off the point table. */
    if (added_faces > 0) {
      const int old_faces = mesh.faces_num;
      mesh.faces_num += added_faces;
      CustomData_realloc(&mesh.face_data, old_faces, mesh.faces_num);
      mesh.attribute_storage.wrap().resize(bke::AttrDomain::Face, mesh.faces_num);
      implicit_sharing::resize_trivial_array(&mesh.face_offset_indices,
                                            &mesh.runtime->face_offsets_sharing_info,
                                            old_faces == 0 ? 0 : (old_faces + 1),
                                            mesh.faces_num + 1);
      mesh.face_offset_indices[0] = 0;
      mesh.face_offset_indices[mesh.faces_num] = mesh.corners_num + added_corners;
    }
    if (added_corners > 0) {
      if (mesh.corners_num == 0) {
        mesh.attributes_for_write().add(
            ".corner_vert", bke::AttrDomain::Corner, bke::AttrType::Int32, bke::AttributeInitConstruct());
        mesh.attributes_for_write().add(
            ".corner_edge", bke::AttrDomain::Corner, bke::AttrType::Int32, bke::AttributeInitConstruct());
      }
      const int old_corners = mesh.corners_num;
      mesh.corners_num += added_corners;
      CustomData_realloc(&mesh.corner_data, old_corners, mesh.corners_num);
      mesh.attribute_storage.wrap().resize(bke::AttrDomain::Corner, mesh.corners_num);
    }
    MutableSpan<int> corner_verts = mesh.corner_verts_for_write();
    MutableSpan<int> face_offsets = mesh.face_offsets_for_write();
    int face = mesh.faces_num - added_faces;
    int corner = mesh.corners_num - added_corners;
    for (const TaggedTopology &tagged : topology) {
      if (program.attrs[size_t(tagged.entry.slot)].polyline) {
        /* `WR-36`: a polyline became a curve spline above and has no face and no corners. Skipping
         * it here is what keeps `face` / `corner` in step with `added_faces` / `added_corners`. */
        continue;
      }
      face_offsets[face] = corner;
      for (const int point : tagged.entry.points) {
        corner_verts[corner++] = (point >= 0 && point < mesh.verts_num) ? point : 0;
      }
      face++;
    }
    face_offsets[mesh.faces_num] = mesh.corners_num;
  }
  else {
    /* `WR-27`: overwrite the placeholders with the numbers the real pass handed out. The tables were
     * sized in the counting pass, so nothing grows here - growing twice is what put the corners past
     * the end of the table. */
    MutableSpan<int> corner_verts = mesh.corner_verts_for_write();
    MutableSpan<int> face_offsets = mesh.face_offsets_for_write();
    int face = mesh.faces_num - added_faces;
    int corner = mesh.corners_num - added_corners;
    for (const TaggedTopology &tagged : topology) {
      if (program.attrs[size_t(tagged.entry.slot)].polyline) {
        continue;
      }
      face_offsets[face] = corner;
      for (const int point : tagged.entry.points) {
        corner_verts[corner++] = point;
      }
      face++;
    }
    face_offsets[mesh.faces_num] = mesh.corners_num;
  }

  /* The new faces bring new edges, and a mesh cannot be evaluated with a stale edge table. */
  bke::mesh_calc_edges(mesh, false, true);
  mesh.tag_topology_changed();
  return true;
}

/**
 * `WR-25`: writes the positions the `addpoint` calls asked for. The counting pass has already grown
 * the point table to its final size, so each entry's number addresses a real element; the entries
 * carry the position because the VM's registers are gone once the run is over.
 */
static bool apply_added_points(GeometrySet &geometry,
                               const Span<vm::Frame::AddPointEntry> entries,
                               const Span<int> claimed_by_writes,
                               std::string &error)
{
  Mesh *touched_mesh = nullptr;
  SpanAttributeWriter<float3> mesh_positions;
  SpanAttributeWriter<float3> cloud_positions;
  bool mesh_ready = false;
  bool cloud_ready = false;
  for (const vm::Frame::AddPointEntry &entry : entries) {
    if (claimed_by_writes.contains(entry.number)) {
      /* `WR-29` promise 3: the program itself wrote this element's position, and that write has to
       * win over the position the element was created with. */
      continue;
    }
    const float3 position{entry.position.x, entry.position.y, entry.position.z};
    if (entry.cloud) {
      PointCloud *cloud = geometry.get_pointcloud_for_write();
      if (cloud == nullptr) {
        error = "addpoint(0, \"pointcloud\", …): the point cloud is gone (WR-25)";
        return false;
      }
      if (!cloud_ready) {
        cloud_positions = cloud->attributes_for_write().lookup_or_add_for_write_span<float3>(
            "position", bke::AttrDomain::Point);
        cloud_ready = true;
      }
      if (entry.number < 0 || entry.number >= cloud_positions.span.size()) {
        error = "addpoint(0, \"pointcloud\", …): the new point is outside the table (WR-25)";
        return false;
      }
      cloud_positions.span[entry.number] = position;
      continue;
    }
    Mesh *mesh = geometry.get_mesh_for_write();
    if (mesh == nullptr) {
      error = "addpoint(0, \"mesh\", …): the mesh is gone (WR-25)";
      return false;
    }
    if (!mesh_ready) {
      mesh_positions = mesh->attributes_for_write().lookup_or_add_for_write_span<float3>(
          "position", bke::AttrDomain::Point);
      mesh_ready = true;
    }
    if (entry.number < 0 || entry.number >= mesh_positions.span.size()) {
      error = "addpoint(0, \"mesh\", …): the new point is outside the table (WR-25)";
      return false;
    }
    mesh_positions.span[entry.number] = position;
    touched_mesh = mesh;
  }
  if (cloud_ready) {
    cloud_positions.finish();
  }
  if (mesh_ready) {
    mesh_positions.finish();
  }
  if (touched_mesh != nullptr) {
    touched_mesh->tag_positions_changed();
  }
  return true;
}

static bool run_domain_on_component(const vm::Program &program,
                                    bke::MutableAttributeAccessor attributes,
                                    const WrangleDomain domain,
                                    const IndexRange range,
                                    const Span<int> corner_verts,
                                    const SelectionFilter &filter,
                                    const fn::FieldContext &field_context,
                                    std::string &error,
                                    const PassInputs &pass,
                                    /* Instances only: where their `@P` lives (WR-23). */
                                    const MutableSpan<float4x4> transforms = {})
{
  IndexMaskMemory mask_memory;
  const IndexMask mask = selection_mask(filter, field_context, int(range.size()), mask_memory);
  if (mask.is_empty()) {
    /* WR-26: an empty selection runs nothing at all - not even the attribute binding, so
     * that a selection which selects nothing cannot create an attribute either. */
    return true;
  }

  /* `WR-41` / `WR-34` promise 3: the run may not see the elements it creates, and `npoints` /
   * `nprimitives` report the counts this evaluation *started* with. The counting pass below grows
   * the real table before the code ever runs, so its two numbers are recorded here - before
   * anything can change them - and the bind phase reads the record instead of the live table.
   * Only a topology-creating program can change a table, so otherwise nothing is recorded and the
   * tables are read live, exactly as before.
   *
   * `pass_at_start` exists only to carry that record into the binds (whose other inputs are
   * `own` / `other` / `component` - none of which a topology change invalidates: the
   * `GeometrySet` itself outlives the pass, only its components are replaced). */
  StartTables start_tables;
  PassInputs pass_at_start = pass;
  if (program.creates_topology && pass.own != nullptr) {
    const ReadTable point = resolve_read_table(*pass.own, pass.component, vm::ReadDomain::Point);
    const ReadTable prim = resolve_read_table(*pass.own, pass.component, vm::ReadDomain::Prim);
    start_tables.point = StartTable{point.size, point.valid};
    start_tables.prim = StartTable{prim.size, prim.valid};
    /* `WR-18`/`WR-19`: the same for every component any `domain` word can name, on every input. */
    for (int input = 0; input < 4; input++) {
      const GeometrySet *set = (input == 0) ?
                                   pass.own :
                                   ((pass.other != nullptr) ? (*pass.other)[input - 1] : nullptr);
      for (int index = 0; index < 4; index++) {
        StartTable point_here;
        StartTable prim_here;
        if (set != nullptr) {
          const GeometryComponent::Type type = component_type_of(index);
          const ReadTable p = resolve_read_table(*set, type, vm::ReadDomain::Point);
          const ReadTable q = resolve_read_table(*set, type, vm::ReadDomain::Prim);
          point_here = StartTable{p.size, p.valid};
          prim_here = StartTable{q.size, q.valid};
        }
        start_tables.point_by_input[input][index] = point_here;
        start_tables.prim_by_input[input][index] = prim_here;
      }
    }
    pass_at_start.start_tables = &start_tables;
  }

  /* WR-32: check the cross-stream and explicit-index reads *before* any writer exists. A type
   * mismatch there is only knowable at run time, and "原样输出" has to mean the geometry really
   * is untouched - if this ran after the writers were created, the output would carry a new
   * all-default attribute from the attempt. */
  for (const int index : IndexRange(program.attrs.size())) {
    const vm::AttrBinding &attr = program.attrs[index];
    if (attr.topology == vm::AttrBinding::Topology::RemovePoint ||
        attr.topology == vm::AttrBinding::Topology::RemovePrim)
    {
      /* `WR-28`: a removal names a mesh, a curve, a point cloud or an instance set, and all four
       * are implemented - `filter_removals` drops the requests whose component is not there, which
       * is the L0's silent case. Nothing to refuse. */
    }
    if (attr.topology == vm::AttrBinding::Topology::AddPrim) {
      if (attr.polyline) {
        /* `WR-27`: a curve target needs a writable stream to add the component to (or to append to
         * an existing curve). */
        if (pass.own_write == nullptr) {
          error = "addprim(0, \"curve\", …) needs a stream to build the curve in (WR-27)";
          return false;
        }
        continue;
      }
      if (pass.own_write == nullptr) {
        error = "addprim(0, \"mesh\", …) needs a stream to build the mesh in (WR-27)";
        return false;
      }
      /* The source has to exist: the reuse case takes its numbers from the mesh, the copy case
       * from the component `pointdomain` names - and neither can point at nothing. (`addprim` has
       * no silent case in the L0; only the reads and the setters do.) */
      if (!source_points_in(*pass.own, attr.point_component).has_value()) {
        error = "addprim: the component `pointdomain` names is not in this stream (WR-27)";
        return false;
      }
    }
    if (attr.indexed_write) {
      /* `WR-24`: a target that cannot be written is *not* an error - the bind below turns it into a
       * dropped write, so nothing has to be checked here. */
      continue;
    }
    /* Only the explicit-index bindings still read something other than the run-over domain, so
     * they are the only ones worth probing before the pass (a storage type that does not fit is a
     * node error, and the probe finds it before anything is created). `@opinput` is retired. */
    if (!(attr.count || attr.domain != vm::ReadDomain::RunOver)) {
      continue;
    }
    Slot probe;
    vm::ReadSource probe_source;
    if (!bind_explicit_read(probe, attr, pass_at_start, &probe_source, error)) {
      return false;
    }
  }

  /* WR-34 promise 1: a program that creates elements has to know their numbers before it runs,
   * because the number `addpoint` returns is the final one. So: a counting pass, then the point
   * table is grown to its final size, then the real pass - which also means the writers are
   * created after the growth, so their spans already cover the new elements. */
  std::vector<int> new_point_bases;
  std::vector<int> new_point_bases_cloud;
  /* `WR-25`: which point tables the program adds to - the growth below may have to create them. */
  bool wants_mesh = false;
  bool wants_cloud = false;
  /** `WR-27`: is there an `addprim` whose target is the mesh? Such a program needs the mesh to
   * exist even when it adds no points (the copy case creates or extends it). */
  bool has_mesh_prim = false;
  for (const vm::AttrBinding &attr : program.attrs) {
    if (attr.topology == vm::AttrBinding::Topology::AddPrim && !attr.polyline) {
      has_mesh_prim = true;
      continue;
    }
    if (attr.topology != vm::AttrBinding::Topology::AddPoint) {
      continue;
    }
    (attr.addpoint_cloud ? wants_cloud : wants_mesh) = true;
  }
  /* WR-37/WR-41: a mesh stream's removal requests are applied *after* the run (see below); a curve\n   * or a point cloud, whose real pass does not run, applies them right here. */
  Vector<TaggedRemoval> pending_removals;
  if (program.creates_topology && (pass.growth != nullptr || pass.own_write != nullptr)) {
    Mesh *growth = pass.growth;
    Array<Slot> count_slots(program.attrs.size());
    Array<vm::ReadSource> count_sources(program.attrs.size());
    for (const int index : IndexRange(program.attrs.size())) {
      if (!bind_slot(count_slots[index],
                     program.attrs[index],
                     index,
                     attributes,
                     domain,
                     corner_verts,
                     int(range.size()),
                     error,
                     transforms,
                     pass_at_start,
                     &count_sources[index],
                     /* count_only: */ true))
      {
        return false;
      }
    }
    RunResult counted = run_over_range(program,
                                            mask,
                                            int(range.size()),
                                            corner_verts,
                                            count_slots.as_span(),
                                            count_sources.as_span(),
                                            {},
                                            {},
                                            /* count_only: */ true);
    if (!counted.error.empty()) {
      error = counted.error;
      return false;
    }
    /* WR-37: the requests are checked here - before any writer exists - against the *input* tables.
     * On a mesh stream the application itself is deferred to the end of this function (`WR-41`'s
     * frozen view); a curve or a point cloud applies them right here, because for those streams the
     * real pass does not run (the `growth == nullptr` early return below). */
    if (pass.own_write != nullptr && pass.attribute_filter != nullptr) {
      /* `WR-28`/`WR-29`: the requests that name a component the stream does not have, or an index
       * its table does not have, are dropped here - both are the L0's silent cases. */
      pending_removals = filter_removals(*pass.own_write, counted.removals);
      if (pass.growth == nullptr) {
        if (!apply_removals(*pass.own_write, pending_removals, *pass.attribute_filter, error)) {
          return false;
        }
        pending_removals.clear();
      }
    }
    /* From here on the growth is a mesh thing: a curve has no point table to grow (WR-36's curve
     * side is not implemented). */
    /* WR-36: the polyline path builds or extends the curve component here - before the bind phase,
     * so a bad request still leaves the geometry untouched, and *before* the mesh-only early return
     * below (a curve stream has no mesh table to grow, which is why the order matters). */
    if (pass.own_write != nullptr && pass.attribute_filter != nullptr) {
      if (!apply_curve_topology(*pass.own_write, program, pass, counted.topology, error)) {
        return false;
      }
      /* The curve path replaces the geometry set (`join_geometries`), so the accessor the caller
       * handed in can point at a copy the stream no longer carries - take the live one. */
      attributes = live_attributes(*pass.own_write, pass.component, attributes);
      if (pass.growth != nullptr) {
        /* The curve path replaces the geometry set (join_geometries), so the mesh pointer taken
         * earlier is stale. */
        growth = pass.own_write->get_mesh_for_write();
        if (growth == nullptr) {
          error = "the stream has no mesh component any more after the curve was built (WR-36)";
          return false;
        }
      }
    }

    {
      /* WR-25: each point table the program names is grown to its final size - and a table whose
       * component the stream does not have is created here, in the counting pass, so the writers
       * the bind phase makes below already cover the new elements. A created component stays in the
       * output: nothing else in this evaluation processes it, because the set of passes was decided
       * from the input geometry. This block also builds `addprim`'s primitives (`WR-36`), so it runs
       * whenever the program creates topology, not only when it adds points. */
      if (wants_mesh && !counted.new_points.empty() &&
          std::any_of(counted.new_points.begin(), counted.new_points.end(), [](int n) { return n > 0; }) &&
          pass.own_write->get_mesh_for_write() == nullptr)
      {
        pass.own_write->replace_mesh(BKE_mesh_new_nomain(0, 0, 0, 0));
      }
      if (wants_cloud && !counted.new_points_cloud.empty() &&
          std::any_of(counted.new_points_cloud.begin(),
                      counted.new_points_cloud.end(),
                      [](int n) { return n > 0; }) &&
          pass.own_write->get_pointcloud_for_write() == nullptr)
      {
        pass.own_write->replace_pointcloud(BKE_pointcloud_new_nomain(0));
      }
      if (wants_mesh && growth == nullptr) {
        /* The mesh this program adds to is the one this pass runs over when there is one, and the
         * stream's own mesh (possibly just created) otherwise. */
        growth = pass.own_write->get_mesh_for_write();
      }
      /* `WR-27`: the copy case - a mesh target whose points come from another component (or a mesh
       * that the copy itself creates). It appends the copied points, so the face builder below works
       * on the mesh's own numbering - and the join replaces the geometry set, so the mesh pointer
       * has to be taken again. */
      if (!copy_mesh_target_points(*pass.own_write, program, pass, counted.topology, error)) {
        return false;
      }
      if (wants_mesh || has_mesh_prim) {
        growth = pass.own_write->get_mesh_for_write();
      }
      /* The numbering continues right after the input's own points, per table, in element order. */
      new_point_bases.assign(size_t(range.size()), 0);
      new_point_bases_cloud.assign(size_t(range.size()), 0);
      const int old_verts_before = (growth != nullptr) ? growth->verts_num : 0;
      PointCloud *cloud_table = wants_cloud ? pass.own_write->get_pointcloud_for_write() : nullptr;
      const int old_cloud_before = (cloud_table != nullptr) ? cloud_table->totpoint : 0;
      int next = old_verts_before;
      int next_cloud = old_cloud_before;
      mask.foreach_index([&](const int64_t index) {
        new_point_bases[size_t(index)] = next;
        next += counted.new_points[size_t(index)];
        new_point_bases_cloud[size_t(index)] = next_cloud;
        next_cloud += counted.new_points_cloud[size_t(index)];
      });
      if (growth != nullptr) {
        growth->verts_num = next;
      }
      /* Growing a mesh domain is three steps, not one: the count, the CustomData that holds the
       * legacy/`.vert_data` arrays (measured against node_geo_extrude_mesh.cc:140-179), and then
       * the attribute storage. Skipping CustomData_realloc leaves the old arrays short.
       *
       * The same recipe, for the face work that is still owed (WR-36/WR-37):
       *   faces:   faces_num += n; CustomData_realloc(&mesh.face_data, old, faces_num);
       *            attribute_storage.wrap().resize(AttrDomain::Face, faces_num);
       *            implicit_sharing::resize_trivial_array(&mesh.face_offset_indices,
       *                &mesh.runtime->face_offsets_sharing_info, old == 0 ? 0 : old + 1,
       *                faces_num + 1);  then face_offset_indices[0] = 0 and
       *            [faces_num] = corners_num; and fill the new offsets per face.
       *   corners: corners_num += n; CustomData_realloc(&mesh.corner_data, old, corners_num);
       *            attribute_storage.wrap().resize(AttrDomain::Corner, corners_num);
       *            (add ".corner_vert"/".corner_edge" first when corners_num was 0)
       *   edges:   edges_num += n; CustomData_realloc(&mesh.edge_data, old, edges_num);
       *            attribute_storage.wrap().resize(AttrDomain::Edge, edges_num);
       *            and rebuild them with bke::mesh_calc_edges after the faces are in. */
      if (cloud_table != nullptr) {
        /* WR-25: a point cloud's point table is its whole geometry, so BKE's own resize is the
         * recipe (it also keeps the attribute storage in step). It has to happen whether or not
         * this pass's component is the point cloud. */
        if (next_cloud > old_cloud_before) {
          pointcloud_resize(*cloud_table, next_cloud);
        }
      }
      if (growth != nullptr && next > old_verts_before) {
        /* Only touch the tables when the code really added a point. A no-op `CustomData_realloc` /
         * attribute-storage resize is not free: on a component that came out of `join_geometries`
         * (the `addprim(0, "polyline", …)` path) writing the storage of a *copied* mesh in place
         * leaves its sharing state inconsistent, and the depsgraph then crashes in `~MeshRuntime` /
         * `free_mesh_eval` when it releases the evaluated copy (access violation; the crash stack is
         * in docs/14 §M6 第三刀). */
        CustomData_realloc(&growth->vert_data, old_verts_before, growth->verts_num);
        growth->attribute_storage.wrap().resize(bke::AttrDomain::Point, growth->verts_num);
      }

      /* WR-36: build the primitives here, in the counting pass - which is where the geometry can
       * still be left untouched if a request is invalid, and where the face table is sized before
       * the bind phase creates any writer. */
      if (growth != nullptr) {
        if (!apply_mesh_topology(*growth, program, counted.topology,
                                 MeshTopologyStage::Size, error)) {
          return false;
        }
        growth->tag_positions_changed();
      }
    }
  }

  Array<Slot> slots(program.attrs.size());
  Array<vm::ReadSource> sources(program.attrs.size());
  for (const int index : IndexRange(program.attrs.size())) {
    if (!bind_slot(slots[index],
                   program.attrs[index],
                   index,
                   attributes,
                   domain,
                   corner_verts,
                   int(range.size()),
                   error,
                   transforms,
                   pass_at_start,
                   &sources[index]))
    {
      return false;
    }
  }
  const RunResult result = run_over_range(program,
                                          mask,
                                          int(range.size()),
                                          corner_verts,
                                          slots.as_span(),
                                          sources.as_span(),
                                          new_point_bases,
                                          new_point_bases_cloud);

  /* WR-34: apply the buffered writes in element order. This happens after the parallel loop, so
   * every element read the frozen input view (promise 2), and a write to an element that does
   * not exist - or into a table this component does not have - is dropped instead of walking
   * off the array. */
  for (const TaggedWrite &tagged : result.writes) {
    if (tagged.write.slot < 0 || tagged.write.slot >= int(slots.size())) {
      continue;
    }
    Slot &slot = slots[tagged.write.slot];
    const int index = tagged.write.index;
    if (index < 0) {
      continue;
    }
    switch (slot.kind) {
      case Slot::Kind::F:
        if (index < slot.f.size()) {
          slot.f[index] = tagged.write.scalar;
        }
        break;
      case Slot::Kind::I:
        if (index < slot.i.size()) {
          slot.i[index] = int(tagged.write.scalar);
        }
        break;
      case Slot::Kind::B:
        if (index < slot.b.size()) {
          slot.b[index] = tagged.write.scalar != 0.0f;
        }
        break;
      case Slot::Kind::V3:
        if (index < slot.v3.size()) {
          slot.v3[index] = float3(
              tagged.write.value.x, tagged.write.value.y, tagged.write.value.z);
        }
        break;
      case Slot::Kind::V2:
        if (index < slot.v2.size()) {
          slot.v2[index] = float2(tagged.write.value.x, tagged.write.value.y);
        }
        break;
      /* `WR-47` / `WR-53`: the same write, for the two transform attribute types. */
      case Slot::Kind::Q4:
        if (index < slot.q4.size()) {
          slot.q4[index] = math::Quaternion(tagged.write.quat[0],
                                            tagged.write.quat[1],
                                            tagged.write.quat[2],
                                            tagged.write.quat[3]);
        }
        break;
      case Slot::Kind::M4:
        if (index < slot.m4.size()) {
          float4x4 value;
          for (int column = 0; column < 4; column++) {
            for (int row = 0; row < 4; row++) {
              value[column][row] = tagged.write.matrix[column * 4 + row];
            }
          }
          slot.m4[index] = value;
        }
        break;
      default:
        break;
    }
  }
  if (!result.error.empty()) {
    error = result.error;
    return false;
  }
  /* Attributes were written in place; the writers tag themselves when they are done. */
  for (Slot &slot : slots) {
    if (!slot.attr->write) {
      continue;
    }
    slot.wf.finish();
    slot.wi.finish();
    slot.wb.finish();
    slot.wv3.finish();
    slot.wv2.finish();
    slot.wq4.finish();
    slot.wm4.finish();
  }
  /* `WR-27`: the corner indices are written here, after the real pass, because that is the pass in
   * which `addpoint` handed out the numbers this face was built from. The table was sized in the
   * counting pass, so nothing has to grow now - and the removals below still see the finished
   * faces, exactly as they saw the primitives before. */
  if (!result.topology.empty() && pass.own_write != nullptr) {
    Mesh *finished = pass.own_write->get_mesh_for_write();
    if (finished != nullptr) {
      if (!apply_mesh_topology(*finished, program, result.topology,
                               MeshTopologyStage::Write, error)) {
        return false;
      }
      finished->tag_positions_changed();
    }
  }

  /* WR-37/WR-41: the mesh stream's removals, applied once the run is over. This is what gives the\n   * run the frozen input view (WR-34 promise 3): every @ write lands on its own element instead\n   * of a slot the removal already shifted, a write to an element this run removes disappears with\n   * that element, and 
points/
primitives keep reporting the starting tables - all three are\n   * what H21 measures (docs/14 §M6 第一步 R1a/R1h). The writers are finished above, so nothing\n   * holds a view into the component pply_removals replaces. */
  if (!pending_removals.is_empty() && pass.own_write != nullptr &&
      pass.attribute_filter != nullptr)
  {
    if (!apply_removals(*pass.own_write, pending_removals, *pass.attribute_filter, error)) {
      return false;
    }
  }

  /* WR-25: `addpoint`'s position argument. The value has to come from the run (the VM's registers
   * are gone), the table has its final size by now, and an element the program wrote a position to
   * itself is left alone - that write wins (`WR-29` promise 3). */
  if (!result.added_points.empty() && pass.own_write != nullptr) {
    std::vector<int> claimed;
    for (const TaggedWrite &tagged : result.writes) {
      if (tagged.write.slot < 0 || tagged.write.slot >= int(slots.size())) {
        continue;
      }
      if (slots[tagged.write.slot].attr->name == "position" && tagged.write.index >= 0) {
        claimed.push_back(tagged.write.index);
      }
    }
    if (!apply_added_points(*pass.own_write, result.added_points, claimed, error)) {
      return false;
    }
  }


  return true;
}

/**
 * Runs the program over the component the run-over kind names (`WR-10`: `Vertex` is the mesh,
 * `Control Point` the curve, `Point` the point cloud, ...). Returns true when it ran; `error` is
 * set on a hard failure. A kind with nothing to run over is not an error - the geometry passes
 * through untouched.
 */
static bool run_over_geometry(const vm::Program &program,
                              GeometrySet &geometry,
                              const RunOver run_over,
                              const SelectionFilter &filter,
                              const std::array<const GeometrySet *, 3> &inputs,
                              const NodeAttributeFilter &attribute_filter,
                              /* `WR-36`/`WR-37`: the channel values, read once by the caller. */
                              const Span<const ChannelValue> channels,
                              std::string &error){
  bool ran = false;
  /* `WR-39`: a component this evaluation *creates* (the curve from `addprim(0, "polyline", …)`)
   * stays in the output but is not processed by this evaluation - `WR-22` runs over the components
   * the input has. Without this gate the same program runs a second time over the curve the mesh
   * pass just built (measured: 8 splines of two control points became 24). */
  /* `WR-39`: a component this evaluation *creates* (the curve from `addprim(0, "polyline", …)`)
   * stays in the output but is not processed by this evaluation - `WR-22` runs over the components
   * the input has. Without this gate the same program runs a second time over the curve the mesh
   * pass just built, and its `addprim` appends splines nobody asked for (measured: 8 splines of
   * two control points became 24 before this gate). */
  const bool had_mesh = geometry.get_mesh() != nullptr;
  const bool had_curves = geometry.get_curves() != nullptr;
  const bool had_pointcloud = geometry.get_pointcloud() != nullptr;
  const bool had_instances = geometry.get_instances() != nullptr;

  if (run_over == RunOver::Detail) {
    /* `WR-25`/`WR-27`: a Detail program that creates topology runs through the same two-pass
     * machinery as the other kinds. Detail is exactly one element, and the compiler already refused
     * every `@` (WR-8), so the only bindings left are the explicit reads - and those carry their own
     * tables, which is why this can reuse `run_domain_on_component` with a one-element mask.
     * Measured before the change: a Detail box came back empty while the same code on a Vertex pass
     * built it, because this branch ran the code once and dropped everything it produced. */
    bool detail_wants_mesh = false;
    bool detail_wants_cloud = false;
    bool detail_wants_curve = false;
    bool detail_removes = false;
    for (const vm::AttrBinding &attr : program.attrs) {
      if (attr.topology == vm::AttrBinding::Topology::AddPoint) {
        (attr.addpoint_cloud ? detail_wants_cloud : detail_wants_mesh) = true;
      }
      else if (attr.topology == vm::AttrBinding::Topology::AddPrim) {
        if (!attr.polyline) {
          detail_wants_mesh = true;
        }
        else {
          detail_wants_curve = true;
          /* The element to iterate over is the component the spline takes its points from: with no
           * `@` in the program that is the only component a Detail run can name. */
          if (attr.point_component == vm::Component::Mesh) {
            detail_wants_mesh = true;
          }
          else if (attr.point_component == vm::Component::PointCloud) {
            detail_wants_cloud = true;
          }
        }
      }
      else if (attr.topology == vm::AttrBinding::Topology::RemovePoint ||
               attr.topology == vm::AttrBinding::Topology::RemovePrim)
      {
        detail_removes = true;
      }
    }
    const bool touches_topology = detail_wants_mesh || detail_wants_cloud || detail_wants_curve ||
                                  detail_removes;
    if (touches_topology) {
      /* A curve target whose source *is* the curve is the one case the mesh and cloud branches
       * below cannot carry, so it goes first when a curve component is already there. */
      if (detail_wants_curve) {
        if (Curves *detail_curves = geometry.get_curves_for_write()) {
          bke::CurvesGeometry &detail_geometry = detail_curves->geometry.wrap();
          PassInputs detail_pass{&geometry, &inputs, GeometryComponent::Type::Curve};
          detail_pass.own_write = &geometry;
          detail_pass.attribute_filter = &attribute_filter;
          detail_pass.channels = channels;
          if (!run_domain_on_component(program,
                                       detail_geometry.attributes_for_write(),
                                       WrangleDomain::Point,
                                       IndexRange(1),
                                       {},
                                       filter,
                                       bke::CurvesFieldContext{detail_geometry,
                                                               bke::AttrDomain::Point},
                                       error,
                                       detail_pass))
          {
            return false;
          }
          if (Curves *live = geometry.get_curves_for_write()) {
            live->geometry.wrap().tag_positions_changed();
          }
          return true;
        }
      }
      if (detail_wants_mesh) {
        Mesh *detail_mesh = geometry.get_mesh_for_write();
        PassInputs detail_pass{&geometry, &inputs, GeometryComponent::Type::Mesh, detail_mesh};
        detail_pass.own_write = &geometry;
        detail_pass.attribute_filter = &attribute_filter;
        detail_pass.channels = channels;
        if (!run_domain_on_component(program,
                                     detail_mesh->attributes_for_write(),
                                     WrangleDomain::Point,
                                     IndexRange(1),
                                     {},
                                     filter,
                                     bke::MeshFieldContext{*detail_mesh, bke::AttrDomain::Point},
                                     error,
                                     detail_pass))
        {
          return false;
        }
        return true;
      }
      if (detail_wants_cloud) {
        PointCloud *detail_cloud = geometry.get_pointcloud_for_write();
        PassInputs detail_pass{&geometry, &inputs, GeometryComponent::Type::PointCloud};
        detail_pass.own_write = &geometry;
        detail_pass.attribute_filter = &attribute_filter;
        detail_pass.channels = channels;
        if (!run_domain_on_component(program,
                                     detail_cloud->attributes_for_write(),
                                     WrangleDomain::Point,
                                     IndexRange(1),
                                     {},
                                     filter,
                                     bke::PointCloudFieldContext{*detail_cloud},
                                     error,
                                     detail_pass))
        {
          return false;
        }
        return true;
      }
      /* Nothing left to delegate (for instance a removal on a component that is not one of the three
       * point tables): the single-run path below at least keeps the old behaviour. */
    }
    /* WR-8: Detail has no per-element context, so the *compiler* already refused any `@`
     * (that includes `@opinput<n>_`). This block is the backstop for the host-supplied
     * bindings that are left; the code runs exactly once and cannot leave a result behind. */
    Array<Slot> slots(program.attrs.size());
    Array<vm::ReadSource> sources(program.attrs.size());
    PassInputs pass{&geometry, &inputs, detail_component(geometry)};
    pass.channels = channels;
    for (const int index : IndexRange(program.attrs.size())) {
      const vm::AttrBinding &attr = program.attrs[index];
      /* WR-31: the explicit-index reads carry their own table, so Detail is exactly where they
       * are meant to be used - `npoints`/`nprimitives` included. */
      if (attr.count || attr.domain != vm::ReadDomain::RunOver) {
        if (!bind_explicit_read(slots[index], attr, pass, &sources[index], error)) {
          return false;
        }
        continue;
      }
      /* WR-36: Detail has no `@`, so a channel is one of the few outside values it can read. */
      if (attr.channel) {
        bind_channel(slots[index], attr, index, pass);
        continue;
      }
      if (!attr.host_supplied) {
        error =
            "Run Over = Detail cannot touch attributes; this code should not have compiled "
            "(WR-8)";
        return false;
      }
      slots[index].attr = &attr;
      slots[index].kind = Slot::Kind::Host;
    }
    vm::Frame frame;
    frame.ensure(program, sources.data(), int(sources.size()));
    load_element(program, slots.as_span(), 0, 1, frame);
    if (vm::run(program, frame) != vm::RunStatus::Ok) {
      error =
          "the code did not finish for some elements (step budget exceeded) - check for an "
          "endless loop";
      return false;
    }
    return true;
  }

  Mesh *mesh = had_mesh ? geometry.get_mesh_for_write() : nullptr;
  if (mesh != nullptr) {

    /* `WR-34`: the mesh is the one component `addpoint` can grow in this milestone. */
    PassInputs pass{&geometry, &inputs, GeometryComponent::Type::Mesh, mesh};
    pass.channels = channels;
    pass.own_write = &geometry;
    pass.attribute_filter = &attribute_filter;    if (run_over == RunOver::Vertex) {
      if (!run_domain_on_component(program,
                                   mesh->attributes_for_write(),
                                   WrangleDomain::Point,
                                   IndexRange(mesh->verts_num),
                                   {},
                                   filter,
                                   bke::MeshFieldContext{*mesh, bke::AttrDomain::Point},
                                   error,
                                   pass))
      {
        return false;
      }
      /* The pass can replace the component from the inside (ddprim(0, "polyline", …) joins a
       * curve in, and taking the accessor for write can localise a shared mesh), so the pointer
       * taken above can be the *shared original* by now. Tagging that one writes into data the
       * depsgraph owns; measured as a crash in ~MeshRuntime when it later releases its evaluated
       * copy (stack in docs/14 §M6 第三刀). Tag the live one. */
      if (Mesh *live_mesh = geometry.get_mesh_for_write()) {
        live_mesh->tag_positions_changed();
      }
      ran = true;
    }
    else if (run_over == RunOver::Edges) {
      if (!run_domain_on_component(program,
                                   mesh->attributes_for_write(),
                                   WrangleDomain::Edge,
                                   IndexRange(mesh->edges_num),
                                   {},
                                   filter,
                                   bke::MeshFieldContext{*mesh, bke::AttrDomain::Edge},
                                   error,
                                   pass))
      {
        return false;
      }
      ran = true;
    }
    else if (run_over == RunOver::Faces) {
      if (!run_domain_on_component(program,
                                   mesh->attributes_for_write(),
                                   WrangleDomain::Face,
                                   IndexRange(mesh->faces_num),
                                   {},
                                   filter,
                                   bke::MeshFieldContext{*mesh, bke::AttrDomain::Face},
                                   error,
                                   pass))
      {
        return false;
      }
      ran = true;
    }
    else if (run_over == RunOver::FaceCorners) {
      if (!run_domain_on_component(program,
                                   mesh->attributes_for_write(),
                                   WrangleDomain::Corner,
                                   IndexRange(mesh->corners_num),
                                   mesh->corner_verts(),
                                   filter,
                                   bke::MeshFieldContext{*mesh, bke::AttrDomain::Corner},
                                   error,
                                   pass))
      {
        return false;
      }
      ran = true;
    }
  }

  /* Curves: `Control Point` is the point domain, `Splines` is the curve domain. */
  Curves *curves_id = had_curves ? geometry.get_curves_for_write() : nullptr;
  if (curves_id != nullptr) {

    bke::CurvesGeometry &curves = curves_id->geometry.wrap();
    PassInputs pass{&geometry, &inputs, GeometryComponent::Type::Curve};
    pass.channels = channels;
    pass.own_write = &geometry;
    pass.attribute_filter = &attribute_filter;    if (run_over == RunOver::ControlPoint) {
      if (!run_domain_on_component(program,
                                   curves.attributes_for_write(),
                                   WrangleDomain::Point,
                                   IndexRange(curves.points_num()),
                                   {},
                                   filter,
                                   bke::CurvesFieldContext{curves, bke::AttrDomain::Point},
                                   error,
                                   pass))
      {
        return false;
      }
      /* Same as the mesh case above: the pass can have replaced the curve component already. */
      if (Curves *live_curves = geometry.get_curves_for_write()) {
        live_curves->geometry.wrap().tag_positions_changed();
      }
      ran = true;
    }
    else if (run_over == RunOver::Splines) {
      if (!run_domain_on_component(program,
                                   curves.attributes_for_write(),
                                   WrangleDomain::Curve,
                                   IndexRange(curves.curves_num()),
                                   {},
                                   filter,
                                   bke::CurvesFieldContext{curves, bke::AttrDomain::Curve},
                                   error,
                                   pass))
      {
        return false;
      }
      ran = true;
    }
  }

  PointCloud *pointcloud = had_pointcloud ? geometry.get_pointcloud_for_write() : nullptr;
  if (pointcloud != nullptr) {

    PassInputs pass{&geometry, &inputs, GeometryComponent::Type::PointCloud};
    pass.channels = channels;
    /* WR-36 rule 1: a point cloud's points are a valid source for addprim("polyline", ...), so this
     * pass has to be able to build the curve component. */
    pass.own_write = &geometry;
    pass.attribute_filter = &attribute_filter;
    if (run_over == RunOver::Point) {
      if (!run_domain_on_component(program,
                                   pointcloud->attributes_for_write(),
                                   WrangleDomain::Point,
                                   IndexRange(pointcloud->totpoint),
                                   {},
                                   filter,
                                   bke::PointCloudFieldContext{*pointcloud},
                                   error,
                                   pass))
      {
        return false;
      }
      ran = true;
    }
  }

  bke::Instances *instances = had_instances ? geometry.get_instances_for_write() : nullptr;
  if (instances != nullptr) {

    PassInputs pass{&geometry, &inputs, GeometryComponent::Type::Instance};
    pass.channels = channels;
    /* `WR-26`/`WR-27`: the same writable stream the other passes carry, so `addpoint` (and the
     * explicit writes) can create or grow a table from an Instances run. The attribute filter has to
     * come along too: without it the removal collection is skipped entirely (measured - the
     * instance removals were silently never collected). */
    pass.own_write = &geometry;
    pass.attribute_filter = &attribute_filter;
    if (run_over == RunOver::Instances) {
      if (!run_domain_on_component(program,
                                   instances->attributes_for_write(),
                                   WrangleDomain::Instance,
                                   IndexRange(instances->instances_num()),
                                   {},
                                   filter,
                                   bke::InstancesFieldContext{*instances},
                                   error,
                                   pass,
                                   instances->transforms_for_write()))
      {
        return false;
      }
      ran = true;
    }
  }

  return ran;
}

/**
 * `WR-37`: read every channel the program mentions **once**, before the parallel run.
 *
 * Nothing here can fail: `WR-38`/`WR-39` say a name that does not exist - or a channel whose type
 * does not fit the function - reads 0, silently. `extract_input` may only be called once per socket
 * and the same channel can be read by two functions (`chf("h")` and `chi("h")`), so each channel's
 * raw value is read once and folded per binding: an int binding truncates, `chv` on a scalar channel
 * is `(value, 0, 0)`, and a scalar read of a vector channel is 0.
 */
static void resolve_channels(const vm::Program &program,
                             GeoNodeExecParams &params,
                             const MutableSpan<ChannelValue> r_values)
{
  const bNode &node = params.node();
  if (node.storage == nullptr) {
    return;
  }
  const NodeGeometryPDTWrangle &storage = node_storage(node);
  Map<int, ChannelValue> ready;
  for (const int index : IndexRange(program.attrs.size())) {
    const vm::AttrBinding &attr = program.attrs[index];
    if (!attr.channel) {
      continue;
    }
    const NodeGeometryPDTWrangleItem *item = nullptr;
    for (const NodeGeometryPDTWrangleItem &candidate : Span(storage.items, storage.items_num)) {
      /* `WR-38`: an exact, case-sensitive match. Everything else is "no such channel". */
      if (candidate.name != nullptr && attr.name == candidate.name) {
        item = &candidate;
        break;
      }
    }
    if (item == nullptr) {
      continue;
    }
    const UString identifier(PDTWrangleItemsAccessor::socket_identifier_for_item(*item));
    const ChannelValue raw = ready.lookup_or_add_cb(int(item->identifier), [&]() {
      ChannelValue fresh;
      switch (item->socket_type) {
        case SOCK_FLOAT: {
          const float value = params.extract_input<float>(identifier);
          fresh.scalar = value;
          fresh.vector = vm::Vec3{value, 0.0f, 0.0f};
          break;
        }
        case SOCK_INT: {
          const int value = params.extract_input<int>(identifier);
          fresh.scalar = float(value);
          fresh.vector = vm::Vec3{float(value), 0.0f, 0.0f};
          break;
        }
        case SOCK_VECTOR: {
          const float3 value = params.extract_input<float3>(identifier);
          /* `WR-39`: a vector channel read as a scalar is 0, not its first component. */
          fresh.scalar = 0.0f;
          fresh.vector = vm::Vec3{value.x, value.y, value.z};
          break;
        }
        /* `WR-35`/`WR-36`: the two transform channels. Only the register file that matches is
         * filled, so a rotation read as a matrix (or the other way round) is zero - `WR-39`. */
        case SOCK_ROTATION: {
          const math::Quaternion value = params.extract_input<math::Quaternion>(identifier);
          fresh.quat[0] = value.w;
          fresh.quat[1] = value.x;
          fresh.quat[2] = value.y;
          fresh.quat[3] = value.z;
          break;
        }
        case SOCK_MATRIX: {
          const float4x4 value = params.extract_input<float4x4>(identifier);
          for (int column = 0; column < 4; column++) {
            for (int row = 0; row < 4; row++) {
              fresh.matrix[column * 4 + row] = value[column][row];
            }
          }
          break;
        }
        default:
          break;
      }
      return fresh;
    });
    ChannelValue value = raw;
    if (attr.type == vm::Type::Int) {
      /* `WR-39`: int and float channels convert (`WR-12`); float -> int truncates. */
      value.scalar = std::trunc(value.scalar);
    }
    r_values[index] = value;
  }
}

static void node_geo_exec(GeoNodeExecParams params)
{
  GeometrySet geometry = params.extract_input<GeometrySet>("Geometry"_ustr);
  /* WR-29: inputs 1..3 are read-only streams; an unconnected one is empty geometry, so
   * reading it yields 0 (WR-32). */
  GeometrySet input_1 = params.extract_input<GeometrySet>("INPUT_1"_ustr);
  GeometrySet input_2 = params.extract_input<GeometrySet>("INPUT_2"_ustr);
  GeometrySet input_3 = params.extract_input<GeometrySet>("INPUT_3"_ustr);
  const std::array<const GeometrySet *, 3> inputs{&input_1, &input_2, &input_3};
  const std::string code = params.extract_input<std::string>("Code"_ustr);
  const RunOver run_over = params.extract_input<RunOver>("Run Over"_ustr);
  const Field<bool> selection = params.extract_input<Field<bool>>("Selection"_ustr);
  /* WR-26: an unwired `Selection` means "every element" - and must not cost a field
   * evaluation, so the link state is checked here rather than evaluated into a mask. */
  const bNodeSocket *selection_socket = params.node().input_by_identifier("Selection"_ustr);
  const SelectionFilter filter{
      &selection, selection_socket != nullptr && selection_socket->is_logically_linked()};

  if (code.empty()) {
    params.set_output("Geometry"_ustr, std::move(geometry));
    return;
  }

  /* WR-10: `Run Over` decides how many times the code runs - the element count of that kind on
   * the *input*. Zero elements (or no component of that kind) means it simply does not run. */

  const std::shared_ptr<const vm::Program> program = get_program(code,
                                                                 run_over == RunOver::Detail);
  if (!program->ok()) {
    std::string report;
    for (const vm::Error &error : program->errors) {
      report += error.format();
      report += "\n";
    }
    /* `WR-32`: remember where it stopped, for the Code box to mark. */
    remember_highlight_error(code, program->errors);
    params.error_message_add(NodeWarningType::Error, report);
    /* `WR-2`: code that does not compile leaves **empty geometry**. The input must not leak
     * through, or a downstream node would silently keep working on data the code never produced. */
    params.set_output("Geometry"_ustr, GeometrySet{});
    return;
  }

  if (program->attrs.empty() && program->code.size() <= 1) {
    /* The code compiled to nothing (comments only). */
    params.set_output("Geometry"_ustr, std::move(geometry));
    return;
  }

  std::string error;
  const NodeAttributeFilter &attribute_filter = params.get_attribute_filter("Geometry"_ustr);
  /* `WR-37`: the channels are read here, once, so the per-element work is a register load. */
  Array<ChannelValue> channel_values(program->attrs.size());
  resolve_channels(*program, params, channel_values);
  const Span<const ChannelValue> channels{channel_values.data(), channel_values.size()};
  const bool ran =
      run_over_geometry(*program, geometry, run_over, filter, inputs, attribute_filter, channels,
                        error);
  if (!error.empty()) {
    /* `WR-2`: the same for an error only the geometry could reveal (a stored type that does not fit,
     * a primitive that cannot be built, ...) - and for `WR-3`'s step budget. */
    params.error_message_add(NodeWarningType::Error, error);    params.set_output("Geometry"_ustr, GeometrySet{});
    return;
  }
  /* WR-10: a kind with nothing to run over is *not* an error - the geometry simply passes
   * through (`ran` is kept for the caller's own diagnostics, not reported). */
  (void)ran;

  params.set_output("Geometry"_ustr, std::move(geometry));
}

/* ------------------------------------------------------------------ */
/** \name Channels (`WR-35`..`WR-40`)                                   */
/* ------------------------------------------------------------------ */

/** A fresh node gets the (empty) channel list; `WR-35` makes "no channels" the old behaviour. */
static void node_init(bNodeTree * /*tree*/, bNode *node)
{
  node->storage = MEM_new<NodeGeometryPDTWrangle>(__func__);
}

static void node_free_storage(bNode *node)
{
  socket_items::destruct_array<PDTWrangleItemsAccessor>(*node);
  MEM_delete(static_cast<NodeGeometryPDTWrangle *>(node->storage));
}

static void node_copy_storage(bNodeTree * /*tree*/, bNode *dst_node, const bNode *src_node)
{
  const NodeGeometryPDTWrangle &src_storage = node_storage(*src_node);
  auto *dst_storage = MEM_new<NodeGeometryPDTWrangle>(__func__, dna::shallow_copy(src_storage));
  dst_node->storage = dst_storage;
  socket_items::copy_array<PDTWrangleItemsAccessor>(*src_node, *dst_node);
}

/** Dragging a link onto the extend socket adds a channel of that type (the "+" button too). */
static bool node_insert_link(bke::NodeInsertLinkParams &params)
{
  return socket_items::try_add_item_via_any_extend_socket<PDTWrangleItemsAccessor>(
      params.ntree, params.node, params.node, params.link);
}

static void node_operators()
{
  socket_items::ops::make_common_operators<PDTWrangleItemsAccessor>();
}

/** The channel list: the panel's own list is where a channel is named, typed, added or removed. */
static void node_layout_ex(ui::Layout &layout, bContext *C, PointerRNA *ptr)
{
  bNodeTree &tree = *reinterpret_cast<bNodeTree *>(ptr->owner_id);
  bNode &node = *static_cast<bNode *>(ptr->data);

  if (ui::Layout *panel = layout.panel(C, "pdt_wrangle_channels", false, IFACE_("Channels"))) {
    socket_items::ui::draw_items_list_with_operators<PDTWrangleItemsAccessor>(
        C, panel, tree, node);
    socket_items::ui::draw_active_item_props<PDTWrangleItemsAccessor>(
        tree, node, [&](PointerRNA *item_ptr) {
          panel->use_property_split_set(true);
          panel->use_property_decorate_set(false);
          panel->prop(item_ptr, "socket_type", UI_ITEM_NONE, std::nullopt, ICON_NONE);
        });
  }
}

static void node_blend_write(const bNodeTree & /*tree*/, const bNode &node, BlendWriter &writer)
{
  socket_items::blend_write<PDTWrangleItemsAccessor>(&writer, node);
}

static void node_blend_read(bNodeTree & /*tree*/, bNode &node, BlendDataReader &reader)
{
  socket_items::blend_read_data<PDTWrangleItemsAccessor>(&reader, node);
}

static void node_register()
{
  static bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodePDTWrangle"_ustr);
  ntype.ui_name = "PDT Wrangle";
  ntype.ui_description = "Run user-written VEX-like code over geometry elements";
  ntype.nclass = NODE_CLASS_GEOMETRY;
  /* The code box is unusable at the default 140 px: the widest preset is the honest
   * default here (users can still drag it; `DefaultMax` is 700). */
  ntype.default_width = bke::NodeWidth::_320;
  ntype.declare = node_declare;
  ntype.geometry_node_execute = node_geo_exec;
  /* `WR-35`: the channel list is node storage, with the shared `socket_items::` helpers. */
  ntype.initfunc = node_init;
  ntype.insert_link = node_insert_link;
  ntype.draw_buttons_ex = node_layout_ex;
  ntype.register_operators = node_operators;
  ntype.blend_write_storage_content = node_blend_write;
  ntype.blend_data_read_storage_content = node_blend_read;
  bke::node_type_storage(ntype, "NodeGeometryPDTWrangle", node_free_storage, node_copy_storage);

  bke::node_register_type(ntype);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_pdt_wrangle_cc

namespace blender::nodes {

StructRNA **PDTWrangleItemsAccessor::item_srna = &RNA_NodeGeometryPDTWrangleItem;
int PDTWrangleItemsAccessor::node_type = 0;

void PDTWrangleItemsAccessor::blend_write_item(BlendWriter *writer, const ItemT &item)
{
  writer->write_string(item.name);
}

void PDTWrangleItemsAccessor::blend_read_data_item(BlendDataReader *reader, ItemT &item)
{
  BLO_read_string(reader, &item.name);
}

}  // namespace blender::nodes
