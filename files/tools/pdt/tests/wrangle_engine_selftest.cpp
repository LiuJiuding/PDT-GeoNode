/* wrangle_engine_selftest.cpp - standalone harness for the PDT Wrangle engine (M1).
 *
 * The engine header (`pdt_wrangle_vm.hh`) has no Blender dependencies on purpose, so the
 * language and the VM can be iterated in **seconds** instead of the ~21 s Blender build:
 *
 *   . tools\pdt\tests\build_engine_selftest.ps1      # compiles this file and runs it (~3 s)
 *   <build>\engine_selftest\wrangle_engine_selftest.exe   # the binary it leaves behind
 *
 * The `Runner` below is also the prototype of what `node_geo_pdt_wrangle.cc` has to do
 * (bind attributes to flat storage, load -> run -> store per element), so a bug found
 * here is a bug found before the node exists.
 *
 * The authoritative assertions live in tools/pdt/tests/test_pdt_wrangle.py (through the
 * real node); this file is the fast loop and the disassembler viewer.
 */

#include "pdt_wrangle_vm.hh"
#include "pdt_wrangle_highlight.hh"
#include "pdt_wrangle_edit.hh"

#include <array>
#include <cmath>
#include <cstdio>
#include <string>
#include <unordered_map>
#include <vector>

using namespace pdt_wrangle;

static int g_checks = 0;
static int g_failures = 0;

static void check(const std::string &label, const double got, const double want, const double eps = 1e-4)
{
  g_checks++;
  const bool ok = std::fabs(got - want) <= eps;
  if (ok) {
    std::printf("OK   %s = %g\n", label.c_str(), got);
  }
  else {
    std::printf("FAIL %s: got %.8g, want %.8g\n", label.c_str(), got, want);
    g_failures++;
  }
}

static void check_vec(const std::string &label,
                      const Vec3 &got,
                      const Vec3 &want,
                      const double eps = 1e-4)
{
  g_checks++;
  const bool ok = std::fabs(got.x - want.x) <= eps && std::fabs(got.y - want.y) <= eps &&
                  std::fabs(got.z - want.z) <= eps;
  if (ok) {
    std::printf("OK   %s = (%.4g, %.4g, %.4g)\n", label.c_str(), got.x, got.y, got.z);
  }
  else {
    std::printf("FAIL %s: got (%.8g, %.8g, %.8g), want (%.8g, %.8g, %.8g)\n",
                label.c_str(), got.x, got.y, got.z, want.x, want.y, want.z);
    g_failures++;
  }
}

static void check_true(const std::string &label, const bool value, const std::string &detail = "")
{
  g_checks++;
  if (value) {
    std::printf("OK   %s%s\n", label.c_str(), detail.empty() ? "" : (" [" + detail + "]").c_str());
  }
  else {
    std::printf("FAIL %s%s\n", label.c_str(), detail.empty() ? "" : (" [" + detail + "]").c_str());
    g_failures++;
  }
}

/* ------------------------------------------------------------------ */
/* the prototype host                                                  */
/* ------------------------------------------------------------------ */

/* point-read-domain: the accessors a prototype host hands to the VM. `ctx` is the name-keyed store, so the
 * read goes straight into it - no copy, exactly like the real host reads a `VArray`. */
static float runner_read_f(const void *ctx, const int index)
{
  return (*static_cast<const std::vector<float> *>(ctx))[size_t(index)];
}
static int runner_read_i(const void *ctx, const int index)
{
  return int((*static_cast<const std::vector<float> *>(ctx))[size_t(index)]);
}
static void runner_read_v(const void *ctx, const int index, float *out)
{
  const Vec3 &value = (*static_cast<const std::vector<Vec3> *>(ctx))[size_t(index)];
  out[0] = value.x;
  out[1] = value.y;
  out[2] = value.z;
}
/** point-read-domain: `qpoint` / `qprim` read four floats, w first. */
static void runner_read_q(const void *ctx, const int index, float *out)
{
  const std::vector<float> &table = *static_cast<const std::vector<float> *>(ctx);
  for (int k = 0; k < 4; k++) {
    out[k] = table[size_t(index) * 4 + size_t(k)];
  }
}
/** point-read-domain: `mpoint` / `mprim` read sixteen floats, four columns. */
static void runner_read_m(const void *ctx, const int index, float *out)
{
  const std::vector<float> &table = *static_cast<const std::vector<float> *>(ctx);
  for (int k = 0; k < 16; k++) {
    out[k] = table[size_t(index) * 16 + size_t(k)];
  }
}

class Runner {
 public:
  bool compile(const std::string &code)
  {
    program_ = pdt_wrangle::compile(code);
    if (!program_.ok()) {
      return false;
    }
    for (const AttrBinding &binding : program_.attrs) {
      if (binding.type == Type::Vector) {
        vectors_[binding.name].resize(1);
      }
      else {
        scalars_[binding.name].resize(1);
      }
    }
    return true;
  }

  /** Resizes every attribute store to `count` (call before run_all). */
  void prepare(const int count)
  {
    count_ = count;
    for (const AttrBinding &binding : program_.attrs) {
      if (binding.type == Type::Vector) {
        vectors_[binding.name].resize(count);
      }
      else {
        scalars_[binding.name].resize(count);
      }
    }
  }

  void set_scalar(const std::string &name, const std::vector<float> &values)
  {
    scalars_[name] = values;
  }
  void set_vector(const std::string &name, const std::vector<Vec3> &values)
  {
    vectors_[name] = values;
  }
  /** point-read-domain: a table of rotations, four floats each, w first. */
  void set_quat(const std::string &name, const std::vector<float> &values)
  {
    quats_[name] = values;
  }
  /** point-read-domain: a table of matrices, sixteen floats each, four columns. */
  void set_matrix(const std::string &name, const std::vector<float> &values)
  {
    matrices_[name] = values;
  }
  /** channel-read: the *scalar* a channel holds (the host would read this from the node's socket). */
  void set_channel(const std::string &name, const float value)
  {
    channel_scalars_[name] = value;
  }
  /** channel-read: the vector a channel holds. */
  void set_channel_vector(const std::string &name, const Vec3 value)
  {
    channel_vectors_[name] = value;
  }
  /** channel-read: the rotation a `SOCK_ROTATION` channel holds, w first. */
  void set_channel_quat(const std::string &name, const float w, const float x, const float y,
                        const float z)
  {
    channel_quats_[name] = {w, x, y, z};
  }
  /** channel-read: the matrix a `SOCK_MATRIX` channel holds, four columns. */
  void set_channel_matrix(const std::string &name, const std::vector<float> &value)
  {
    channel_matrices_[name] = value;
  }

  RunStatus run_all()
  {
    Frame frame;
    build_sources();
    frame.ensure(program_, sources_.data(), int(sources_.size()));
    element_writes_.clear();
    element_new_points_.clear();
    for (int i = 0; i < count_; i++) {
      /* The real host counts first and then sets each element's base; the harness stands in for
       * that pass with a fixed stride, which is enough to check the numbering itself. */
      /* add-point: one base per point table; this fake host only ever grows the mesh table. */
      frame.new_point_base[0] = new_point_base_ + i * new_point_stride_;
      frame.new_point_base[1] = 0;
      frame.new_points[0] = 0;
      frame.new_points[1] = 0;
      for (const AttrBinding &binding : program_.attrs) {
        if (binding.channel) {
          /* channel-read: a channel is a value of the node itself - the host read it before the run
           * and the pass only loads the register (the folding rules are the host's, so this harness
           * just hands over whatever the test set). */
          if (binding.type == Type::Vector) {
            const auto found = channel_vectors_.find(binding.name);
            frame.v[binding.reg] = found == channel_vectors_.end() ? Vec3{} : found->second;
          }
          else if (binding.type == Type::Quaternion) {
            /* channel-read: `chq` - the rotation a SOCK_ROTATION channel holds. */
            const auto found = channel_quats_.find(binding.name);
            for (int k = 0; k < 4; k++) {
              frame.q[binding.reg * 4 + k] = found == channel_quats_.end() ? 0.0f : found->second[k];
            }
          }
          else if (binding.type == Type::Matrix) {
            /* channel-read: `chm` - the matrix a SOCK_MATRIX channel holds. */
            const auto found = channel_matrices_.find(binding.name);
            for (int k = 0; k < 16; k++) {
              frame.m[binding.reg * 16 + k] =
                  found == channel_matrices_.end() ? 0.0f : found->second[k];
            }
          }
          else {
            const auto found = channel_scalars_.find(binding.name);
            frame.s[binding.reg] = found == channel_scalars_.end() ? 0.0f : found->second;
          }
          continue;
        }
        if (binding.host_supplied) {
          if (binding.count) {
            /* npoints / nprimitives report the table sizes (prim-read-domain). */
            frame.s[binding.reg] = float(binding.domain == ReadDomain::Prim ? prims_count_
                                                                            : points_count_);
          }
          else {
            frame.s[binding.reg] = (binding.name == "numelem") ? float(count_) : float(i);
          }
          continue;
        }
        if (binding.domain != ReadDomain::RunOver) {
          /* point-read-domain: an explicit-index read is fetched by the VM at the index the code computes,
           * so the host must not pre-load it. */
          continue;
        }
        /* The harness binds floats, ints and vectors; a rotation or matrix attribute (`WR-47`,
         * `WR-53`) has no register file here, so it is left at its zero value - the real host
         * binds those separately. */
        if (binding.type == Type::Quaternion || binding.type == Type::Matrix) {
          continue;
        }
        if (binding.type == Type::Vector) {
          const auto &store = vectors_[binding.name];
          frame.v[binding.reg] = i < int(store.size()) ? store[i] : Vec3{};
        }
        else {
          const auto &store = scalars_[binding.name];
          frame.s[binding.reg] = i < int(store.size()) ? store[i] : 0.0f;
        }
      }
      const RunStatus status = run(program_, frame);
      if (status != RunStatus::Ok) {
        return status;
      }
      /* ordered-parallelism: harvest what this element buffered - the host merges these in element order. */
      element_writes_.push_back(frame.writes);
      frame.writes.clear();
      element_new_points_.push_back(frame.new_points[0]);
      element_topology_.push_back(frame.topology);
      frame.topology.clear();
      for (const AttrBinding &binding : program_.attrs) {
        if (!binding.write || binding.indexed_write) {
          continue;
        }
        if (binding.type == Type::Quaternion || binding.type == Type::Matrix) {
          continue;
        }
        if (binding.type == Type::Vector) {
          vectors_[binding.name].resize(count_);
          vectors_[binding.name][i] = frame.v[binding.reg];
        }
        else {
          scalars_[binding.name].resize(count_);
          scalars_[binding.name][i] = frame.s[binding.reg];
        }
      }
    }
    return RunStatus::Ok;
  }

  /** `ordered-parallelism`: what element `i` buffered, in call order. */
  const std::vector<WriteEntry> &element_writes(const int i) const
  {
    static const std::vector<WriteEntry> none;
    return (i >= 0 && i < int(element_writes_.size())) ? element_writes_[size_t(i)] : none;
  }

  /** `add-prim`: what element `i` asked to build. */
  const std::vector<TopologyEntry> &element_topology(const int i) const
  {
    static const std::vector<TopologyEntry> none;
    return (i >= 0 && i < int(element_topology_.size())) ? element_topology_[size_t(i)] : none;
  }

  /** `ordered-parallelism` promise 1: the numbering base the host's counting pass would hand element `i`. */
  void set_new_point_base(const int base, const int stride = 100)
  {
    new_point_base_ = base;
    new_point_stride_ = stride;
  }
  int element_new_points(const int i) const
  {
    return (i >= 0 && i < int(element_new_points_.size())) ? element_new_points_[size_t(i)] : 0;
  }

  /** npoints / nprimitives report these (prim-read-domain's tables). */
  void set_counts(const int points, const int prims)
  {
    points_count_ = points;
    prims_count_ = prims;
  }

  float scalar(const std::string &name, const int i = 0) const
  {
    const auto found = scalars_.find(name);
    return found == scalars_.end() || i >= int(found->second.size()) ? 0.0f : found->second[i];
  }
  Vec3 vector(const std::string &name, const int i = 0) const
  {
    const auto found = vectors_.find(name);
    return found == vectors_.end() || i >= int(found->second.size()) ? Vec3{} : found->second[i];
  }
  bool writes(const std::string &name) const
  {
    for (const AttrBinding &binding : program_.attrs) {
      if (binding.name == name) {
        return binding.write;
      }
    }
    return false;
  }
  bool reads(const std::string &name) const
  {
    for (const AttrBinding &binding : program_.attrs) {
      if (binding.name == name) {
        return binding.read;
      }
    }
    return false;
  }
  std::string errors() const
  {
    return program_.error_report();
  }
  const Program &program() const
  {
    return program_;
  }

 private:
  /** point-read-domain: one `ReadSource` per binding. An empty store means "this reads 0" (indexed-read-silent) - which
   * is also how the miss cases below are set up. */
  void build_sources()
  {
    sources_.assign(program_.attrs.size(), ReadSource{});
    for (size_t slot = 0; slot < program_.attrs.size(); slot++) {
      const AttrBinding &binding = program_.attrs[slot];
      if (binding.count || binding.domain == ReadDomain::RunOver) {
        continue;
      }
      ReadSource &source = sources_[slot];
      if (binding.type == Type::Vector) {
        const auto found = vectors_.find(binding.name);
        if (found == vectors_.end()) {
          continue;
        }
        source.ctx = &found->second;
        source.read_v = runner_read_v;
        source.size = int(found->second.size());
      }
      else if (binding.type == Type::Quaternion) {
        /* point-read-domain: `qpoint` / `qprim` - four floats per element, w first. */
        const auto found = quats_.find(binding.name);
        if (found == quats_.end()) {
          continue;
        }
        source.ctx = &found->second;
        source.read_q = runner_read_q;
        source.size = int(found->second.size() / 4);
      }
      else if (binding.type == Type::Matrix) {
        /* point-read-domain: `mpoint` / `mprim` - sixteen floats per element, four columns. */
        const auto found = matrices_.find(binding.name);
        if (found == matrices_.end()) {
          continue;
        }
        source.ctx = &found->second;
        source.read_m = runner_read_m;
        source.size = int(found->second.size() / 16);
      }
      else {
        const auto found = scalars_.find(binding.name);
        if (found == scalars_.end()) {
          continue;
        }
        source.ctx = &found->second;
        source.read_f = binding.type == Type::Int ? nullptr : runner_read_f;
        source.read_i = binding.type == Type::Int ? runner_read_i : nullptr;
        source.size = int(found->second.size());
      }
    }
  }

  Program program_;
  int count_ = 1;
  int points_count_ = 0;
  int prims_count_ = 0;
  std::vector<ReadSource> sources_;
  std::vector<std::vector<WriteEntry>> element_writes_;
  std::vector<int> element_new_points_;
  std::vector<std::vector<TopologyEntry>> element_topology_;
  int new_point_base_ = 0;
  int new_point_stride_ = 100;
  std::unordered_map<std::string, std::vector<float>> scalars_;
  std::unordered_map<std::string, std::vector<Vec3>> vectors_;
  /* point-read-domain: the tables `qpoint` / `mpoint` / `qprim` / `mprim` read - flat, four (or
   * sixteen) floats per element. */
  std::unordered_map<std::string, std::vector<float>> quats_;
  std::unordered_map<std::string, std::vector<float>> matrices_;
  /* channel-read: what the node's channel sockets hold - one value each, not per element. */
  std::unordered_map<std::string, float> channel_scalars_;
  std::unordered_map<std::string, Vec3> channel_vectors_;
  std::unordered_map<std::string, std::array<float, 4>> channel_quats_;
  std::unordered_map<std::string, std::vector<float>> channel_matrices_;
};

/** Compiles `code`, loads P, and (unless `autorun` is false) runs every element once. */
static Runner *setup(const std::string &code,
                     const Vec3 &p = Vec3{0, 0, 0},
                     const int count = 1,
                     const bool autorun = true)
{
  Runner *runner = new Runner();
  if (!runner->compile(code)) {
    std::printf("FAIL compile: %s\n--- errors ---\n%s\n", code.c_str(), runner->errors().c_str());
    g_failures++;
    g_checks++;
    delete runner;
    return nullptr;
  }
  runner->prepare(count);
  runner->set_vector("position", std::vector<Vec3>(size_t(count), p));
  if (autorun) {
    if (runner->run_all() != RunStatus::Ok) {
      std::printf("FAIL run: %s (budget)\n", code.c_str());
      g_failures++;
      g_checks++;
    }
  }
  return runner;
}

static std::string first_error(const std::string &code, const bool detail = false)
{
  const pdt_wrangle::Program program = pdt_wrangle::compile(code, detail);
  const std::string report = program.error_report();
  const size_t newline = report.find('\n');
  return newline == std::string::npos ? report : report.substr(0, newline);
}

/* ------------------------------------------------------------------ */

int main()
{
  std::printf("=== PDT Wrangle engine self-test (M1) ===\n");

  /* ---------------------------------------------------------- attributes */
  if (Runner *r = setup("@P.y += 1;", Vec3{0, 0, 0})) {
    check_vec("@P.y += 1 from (0,0,0)", r->vector("position"), Vec3{0, 1, 0});
    check_true("position is written and read", r->writes("position") && r->reads("position"));
    delete r;
  }
  if (Runner *r = setup("@P = @P * 2 + {0.5, 0, 0};", Vec3{1, 2, 3})) {
    check_vec("@P = @P * 2 + {0.5,0,0}", r->vector("position"), Vec3{2.5, 4, 6});
    delete r;
  }
  if (Runner *r = setup("v@Cd = {1, 0, 0};")) {
    check_vec("v@Cd = {1,0,0} creates a vector attribute", r->vector("Cd"), Vec3{1, 0, 0});
    delete r;
  }
  if (Runner *r = setup("f@mask = 0.5; i@id = 7;")) {
    check("f@mask", r->scalar("mask"), 0.5);
    check("i@id", r->scalar("id"), 7);
    delete r;
  }
  if (Runner *r = setup("@P[1] = 5;")) {
    check_vec("@P[1] = 5", r->vector("position"), Vec3{0, 5, 0});
    delete r;
  }
  if (Runner *r = setup("@P.z = @P.x + @P.y;", Vec3{2, 3, 0})) {
    check_vec("@P.z = @P.x + @P.y", r->vector("position"), Vec3{2, 3, 5});
    delete r;
  }
  if (Runner *r = setup("v@a = {1,2,3}; @P.r += v@a.g;")) {
    check_vec("attribute-to-attribute and .r/.g swizzles", r->vector("position"), Vec3{2, 0, 0});
    delete r;
  }

  /* ---------------------------------------------------------- arithmetic */
  if (Runner *r = setup("f@r = 1 + 2 * 3;")) {
    check("precedence 1 + 2 * 3", r->scalar("r"), 7);
    delete r;
  }
  if (Runner *r = setup("f@r = (1 + 2) * 3;")) {
    check("parentheses (1+2)*3", r->scalar("r"), 9);
    delete r;
  }
  if (Runner *r = setup("f@r = 2 + 3 * 4 - 6 / 3;")) {
    check("2 + 3*4 - 6/3", r->scalar("r"), 12);
    delete r;
  }
  if (Runner *r = setup("f@r = 7 % 3;")) {
    check("7 % 3", r->scalar("r"), 1);
    delete r;
  }
  if (Runner *r = setup("f@r = -(2 + 3) + !0;")) {
    check("unary minus and !", r->scalar("r"), -4);
    delete r;
  }
  if (Runner *r = setup("float a = 3; float b = 4; f@r = sqrt(a * a + b * b);")) {
    check("sqrt(a^2+b^2)", r->scalar("r"), 5);
    delete r;
  }
  if (Runner *r = setup("f@r = 2; f@r *= 3; f@r -= 1;")) {
    check("compound assignment", r->scalar("r"), 5);
    delete r;
  }
  if (Runner *r = setup("int i = 2; i += 1; f@r = i;")) {
    check("int local arithmetic", r->scalar("r"), 3);
    delete r;
  }
  if (Runner *r = setup("i@n = 1.7; f@r = i@n;")) {
    check("float to int truncates (1.7 -> 1)", r->scalar("r"), 1);
    delete r;
  }
  if (Runner *r = setup("f@r = float(3) / 2;")) {
    check("float() cast keeps 3/2 = 1.5", r->scalar("r"), 1.5);
    delete r;
  }

  /* -------------------------------------------------------------- vectors */
  if (Runner *r = setup("v@a = {1, 2, 3}; f@r = length(v@a);")) {
    check("length({1,2,3})", r->scalar("r"), std::sqrt(14.0));
    delete r;
  }
  if (Runner *r = setup("f@r = dot({1, 0, 0}, {0, 1, 0});")) {
    check("dot of orthogonal vectors", r->scalar("r"), 0);
    delete r;
  }
  if (Runner *r = setup("v@a = cross({1, 0, 0}, {0, 1, 0});")) {
    check_vec("cross(x, y) = z", r->vector("a"), Vec3{0, 0, 1});
    delete r;
  }
  if (Runner *r = setup("v@a = normalize({0, 3, 4});")) {
    check_vec("normalize({0,3,4})", r->vector("a"), Vec3{0, 0.6f, 0.8f});
    delete r;
  }
  if (Runner *r = setup("f@r = distance({0,0,0}, {1,2,2});")) {
    check("distance", r->scalar("r"), 3);
    delete r;
  }
  if (Runner *r = setup("v@a = {1, 2};")) {
    check_vec("two-component vector literal", r->vector("a"), Vec3{1, 2, 0});
    delete r;
  }
  if (Runner *r = setup("v@a = sin({0, 0, 0});")) {
    check_vec("componentwise sin on a vector", r->vector("a"), Vec3{0, 0, 0});
    delete r;
  }
  if (Runner *r = setup("v@a = max({1, 5, 0}, 2);")) {
    check_vec("max(vector, scalar) broadcasts", r->vector("a"), Vec3{2, 5, 2});
    delete r;
  }
  if (Runner *r = setup("v@a = {1, 2, 3} * 2 + 1;")) {
    check_vec("vector * scalar + scalar", r->vector("a"), Vec3{3, 5, 7});
    delete r;
  }
  if (Runner *r = setup("v@a = 1 - {1, 2, 3};")) {
    check_vec("scalar - vector", r->vector("a"), Vec3{0, -1, -2});
    delete r;
  }

  /* -------------------------------------------------- the math library */
  if (Runner *r = setup("f@r = clamp(5, 0, 1);")) {
    check("clamp(5, 0, 1)", r->scalar("r"), 1);
    delete r;
  }
  if (Runner *r = setup("f@r = lerp(0, 10, 0.25);")) {
    check("lerp(0, 10, 0.25)", r->scalar("r"), 2.5);
    delete r;
  }
  if (Runner *r = setup("f@r = fit(5, 0, 10, 0, 1);")) {
    check("fit(5, 0, 10, 0, 1)", r->scalar("r"), 0.5);
    delete r;
  }
  if (Runner *r = setup("f@r = smoothstep(0, 1, 0.5);")) {
    check("smoothstep(0, 1, 0.5)", r->scalar("r"), 0.5);
    delete r;
  }
  if (Runner *r = setup("f@r = min(max(0.3, 0.4), 0.35);")) {
    check("nested min/max", r->scalar("r"), 0.35);
    delete r;
  }
  if (Runner *r = setup("f@r = pow(2, 10);")) {
    check("pow(2, 10)", r->scalar("r"), 1024);
    delete r;
  }
  if (Runner *r = setup("f@r = atan2(1, 1);")) {
    check("atan2(1, 1)", r->scalar("r"), 3.14159265358979 / 4.0);
    delete r;
  }
  if (Runner *r = setup("f@r = fract(-0.25);")) {
    check("fract(-0.25) is positive", r->scalar("r"), 0.75);
    delete r;
  }
  if (Runner *r = setup("f@r = step(0.5, 0.4) + step(0.5, 0.6);")) {
    check("step() below and above the edge", r->scalar("r"), 1);
    delete r;
  }
  if (Runner *r = setup("f@r = rand(3); f@r2 = rand(3);")) {
    const float a = r->scalar("r");
    check_true("rand is deterministic and in [0,1)", a == r->scalar("r2") && a >= 0.0f && a < 1.0f,
               std::to_string(a));
    delete r;
  }
  if (Runner *r = setup("f@r = noise({0.5, 0.5, 0.5});")) {
    const float a = r->scalar("r");
    check_true("noise is deterministic and in [-1,1]", a >= -1.0f && a <= 1.0f, std::to_string(a));
    delete r;
  }

  /* --------------------------------------------------------- control flow */
  if (Runner *r = setup("if (@P.x > 0) { f@r = 1; } else { f@r = 2; }", Vec3{-1, 0, 0})) {
    check("if/else takes the else branch", r->scalar("r"), 2);
    delete r;
  }
  if (Runner *r = setup("if (@P.x > 0) { f@r = 1; } else { f@r = 2; }", Vec3{1, 0, 0})) {
    check("if/else takes the then branch", r->scalar("r"), 1);
    delete r;
  }
  if (Runner *r = setup("float acc = 0; for (int i = 0; i < 5; i++) { acc += i; } f@r = acc;")) {
    check("for loop accumulates 0..4", r->scalar("r"), 10);
    delete r;
  }
  if (Runner *r = setup("float n = 1; for (int i = 1; i <= 4; i++) { n *= i; } f@r = n;")) {
    check("for loop multiplies to 4!", r->scalar("r"), 24);
    delete r;
  }
  if (Runner *r = setup("float acc = 0; for (int i = 0; i < 3; i++) { if (i == 1) { acc += 10; } acc += 1; } f@r = acc;")) {
    /* i = 0, 1, 2: three +1 and one +10 in the middle -> 13 */
    check("if inside for", r->scalar("r"), 13);
    delete r;
  }
  if (Runner *r = setup("float acc = 0; for (int i = 0; i < 2; i++) { float inner = 5; acc += inner; } f@r = acc;")) {
    check("inner declaration runs each iteration", r->scalar("r"), 10);
    delete r;
  }
  if (Runner *r = setup("float x = 1; { float x = 2; f@r = x; } f@r2 = x;")) {
    check("block shadowing inside", r->scalar("r"), 2);
    check("block shadowing outside", r->scalar("r2"), 1);
    delete r;
  }

  /* -------------------------------------------------------- element info */
  /* element-numbering: the Houdini names are gone - `@elemnum` / `@numelem` are the only element numbers. */
  if (Runner *r = setup("f@r = @elemnum; f@n = @numelem;", Vec3{0, 0, 0}, 4, false)) {
    r->run_all();
    check("@elemnum at index 0", r->scalar("r", 0), 0);
    check("@numelem", r->scalar("n", 0), 4);
    delete r;
  }
  check_true("@ptnum is refused (element-numbering)", !pdt_wrangle::compile("f@r = @ptnum;").ok());
  check_true("and the message names the replacement",
             first_error("f@r = @ptnum;").find("@elemnum") != std::string::npos);
  check_true("@primnum is refused too (element-numbering)", !pdt_wrangle::compile("f@r = @primnum;").ok());
  if (Runner *r = setup("f@r = @elemnum * 2;", Vec3{0, 0, 0}, 4, false)) {
    r->run_all();
    check("@elemnum * 2 at index 3", r->scalar("r", 3), 6);
    delete r;
  }
  if (Runner *r = setup("@P.x += @elemnum;", Vec3{0, 0, 0}, 3, false)) {
    r->run_all();
    check_vec("per-element attribute write at 0", r->vector("position", 0), Vec3{0, 0, 0});
    check_vec("per-element attribute write at 2", r->vector("position", 2), Vec3{2, 0, 0});
    delete r;
  }

  /* ------------------------------------------------------- error reports */
  check_true("empty code compiles", pdt_wrangle::compile("").ok());
  check_true("comment-only code compiles", pdt_wrangle::compile("// nothing\n/* here */").ok());
  check_true("missing semicolon is reported",
             !pdt_wrangle::compile("f@r = 1").ok());
  check_true("unknown function is reported",
             !pdt_wrangle::compile("f@r = nosuchfn(1);").ok());
  check_true("unknown name is reported", !pdt_wrangle::compile("f@r = mystery;").ok());
  check_true("wrong arity is reported", !pdt_wrangle::compile("f@r = sin(1, 2);").ok());
  check_true("vector into float is reported", !pdt_wrangle::compile("f@r = @P;").ok());
  check_true("comparing vectors is reported", !pdt_wrangle::compile("f@r = @P > @P;").ok());
  check_true("assignment to @ptnum is reported", !pdt_wrangle::compile("@ptnum = 1;").ok());
  check_true("mixed attribute types are reported",
             !pdt_wrangle::compile("f@x = 1; v@x = {1,2,3};").ok());
  check_true("a bare attribute name is reported (bounded-execution)",
             !pdt_wrangle::compile("f@r = @weight;").ok());
  check_true("and the message says which prefixes exist",
             first_error("f@r = @weight;").find("i@weight") != std::string::npos,
             first_error("f@r = @weight;"));
  check_true("a bare read of a known attribute is reported too (bounded-execution)",
             !pdt_wrangle::compile("v@Cd = {1,2,3};\nf@r = @Cd.y;").ok());
  check_true("unclosed block is reported", !pdt_wrangle::compile("if (1) { f@r = 1;").ok());
  check_true("unterminated comment is reported", !pdt_wrangle::compile("/* oops").ok());
  check_true("unknown component is reported", !pdt_wrangle::compile("f@r = @P.w;").ok());
  check_true("undeclared variable is reported", !pdt_wrangle::compile("nope = 1;").ok());
  check_true("the error carries a line and a column",
             first_error("f@r = 1\nf@r = nope;").rfind("2:", 0) == 0,
             first_error("f@r = 1\nf@r = nope;"));

  /* ------------------------------------- cross-stream `@opinput` is retired (明确不做) */
  /* The four geometry inputs stay (selection-scope) and the explicit-index family still reads them with
   * geo 1..3 (indexed-read-silent, exercised by the node tests) - but this syntax is gone. */
  check_true("`v@opinput1_P` is refused",
             !pdt_wrangle::compile("v@r = v@opinput1_P;").ok(),
             first_error("v@r = v@opinput1_P;"));
  check_true("and the message names the replacement",
             first_error("v@r = v@opinput1_P;").find("fpoint") != std::string::npos);
  {
    bool carries_input = false;
    for (const pdt_wrangle::AttrBinding &binding :
         pdt_wrangle::compile("v@r = v@opinput1_P;").attrs) {
      if (binding.input != 0) {
        carries_input = true;
      }
    }
    check_true("no surviving binding carries a non-zero input", !carries_input);
  }
  check_true("`@opinput2_position` is refused too",
             !pdt_wrangle::compile("v@r = v@opinput2_position;").ok());
  check_true("`@opinput3_mask` is refused as well",
             !pdt_wrangle::compile("f@mask = f@opinput3_mask;").ok());
  check_true("a bare `@opinput1_x` is refused",
             !pdt_wrangle::compile("f@r = @opinput1_weight;").ok(),
             first_error("f@r = @opinput1_weight;"));
  check_true("input 0 is not an `opinput`", !pdt_wrangle::compile("f@r = f@opinput0_weight;").ok(),
             first_error("f@r = f@opinput0_weight;"));
  check_true("input 4 does not exist", !pdt_wrangle::compile("f@r = f@opinput4_weight;").ok(),
             first_error("f@r = f@opinput4_weight;"));
  check_true("writing through the old syntax is refused",
             !pdt_wrangle::compile("f@opinput1_weight = 1;").ok(),
             first_error("f@opinput1_weight = 1;"));
  check_true("a name that merely starts with `opinput` is an ordinary attribute",
             pdt_wrangle::compile("f@opinputweight = 1;").ok());

  /* ------------------------------------------------- Detail has no `@` (at-read/30) */
  check_true("Detail refuses `@P`",
             !pdt_wrangle::compile("@P.y += 1;", true).ok(),
             first_error("@P.y += 1;", true));
  check_true("Detail refuses `f@x`",
             !pdt_wrangle::compile("f@x = 1;", true).ok());
  check_true("Detail refuses `@opinput1_P`",
             !pdt_wrangle::compile("v@r = v@opinput1_P;", true).ok());
  check_true("Detail still accepts locals only",
             pdt_wrangle::compile("float a = 1.0;\nvector v = {1, 2, 3};", true).ok(),
             pdt_wrangle::compile("float a = 1.0;\nvector v = {1, 2, 3};", true).error_report());
  check_true("the same code compiles outside Detail",
             pdt_wrangle::compile("@P.y += 1;", false).ok());

  /* --------------------------------------------------------- step budget */
  {
    Runner runner;
    const bool ok = runner.compile("for (float i = 0; i < 100000000; i += 1) { f@r = i; }");
    check_true("the runaway loop compiles", ok);
    if (ok) {
      runner.prepare(1);
      check_true("the runaway loop is stopped by the budget",
                 runner.run_all() == RunStatus::BudgetExceeded);
    }
  }

  /* ------------------------------------------------- attribute bookkeeping */
  {
    Runner runner;
    check_true("read-only attribute is only read",
               runner.compile("f@r = @P.x;") && runner.reads("position") &&
                   !runner.writes("position"));
    check_true("write-only attribute is only written",
               runner.compile("f@r = 1; v@Cd = {0,0,0};") && runner.writes("Cd") &&
                   !runner.reads("Cd"));
  }

  /* -------------------------------- explicit-index reads (point-read-domain / explicit-index reads / point-read-domain) */
  {
    Runner runner;
    if (runner.compile("f@r = fpoint(0, \"mesh\", \"weight\", 0);")) {
      const Program &program = runner.program();
      const AttrBinding *weight = nullptr;
      for (const AttrBinding &binding : program.attrs) {
        if (binding.name == "weight") {
          weight = &binding;
        }
      }
      check_true("fpoint binds a read-only point attribute (point-read-domain)",
                 program.attrs.size() == 2 && weight != nullptr &&
                     weight->type == Type::Float && weight->read && !weight->write &&
                     weight->input == 0 && weight->domain == ReadDomain::Point &&
                     !weight->count);
    }
    else {
      check_true("fpoint binds a read-only point attribute (point-read-domain)", false, runner.errors());
    }
  }

  if (Runner *r = setup("f@r = fpoint(0, \"mesh\", \"weight\", 0);", Vec3{}, 1, false)) {
    r->set_scalar("weight", {2.5f});
    r->run_all();
    check("fpoint(0, \"mesh\", \"weight\", 0) reads the stored value", r->scalar("r"), 2.5f);
    delete r;
  }
  if (Runner *r = setup("f@r = fpoint(0, \"mesh\", \"weight\", 1);", Vec3{}, 1, false)) {
    r->set_scalar("weight", {10.0f, 20.0f});
    r->run_all();
    check("the index is a run-time value, not the current element", r->scalar("r"), 20.0f);
    delete r;
  }
  if (Runner *r = setup("int k = 2;\nf@r = fpoint(0, \"mesh\", \"weight\", k + 1);", Vec3{}, 1, false)) {
    r->set_scalar("weight", {10.0f, 20.0f, 30.0f, 40.0f});
    r->run_all();
    check("the index may be any expression", r->scalar("r"), 40.0f);
    delete r;
  }
  if (Runner *r = setup("f@r = fpoint(0, \"mesh\", \"weight\", 5);\nf@n = fpoint(0, \"mesh\", \"weight\", -3);",
                        Vec3{},
                        1,
                        false))
  {
    r->set_scalar("weight", {10.0f, 20.0f});
    r->run_all();
    check("an index past the end reads 0 (indexed-read-silent)", r->scalar("r"), 0.0f);
    check("a negative index reads 0 (indexed-read-silent)", r->scalar("n"), 0.0f);
    delete r;
  }
  if (Runner *r = setup("f@r = fpoint(0, \"mesh\", \"weight\", 0);", Vec3{}, 1, false)) {
    r->set_scalar("weight", {});
    r->run_all();
    check_true("a missing attribute reads 0 and creates nothing (indexed-read-silent)",
               r->scalar("r") == 0.0f && !r->writes("weight"));
    delete r;
  }
  if (Runner *r = setup("v@c = vpoint(0, \"mesh\", \"Cd\", 0);\ni@t = ipoint(0, \"mesh\", \"tag\", 1);",
                        Vec3{},
                        1,
                        false))
  {
    r->set_vector("Cd", {Vec3{1, 2, 3}});
    r->set_scalar("tag", {7.0f, 9.0f});
    r->run_all();
    check_vec("vpoint reads a vector attribute", r->vector("c"), Vec3{1, 2, 3});
    check("ipoint reads an int attribute", r->scalar("t"), 9.0f);
    delete r;
  }
  /* `WR-18` / `WR-19`: the same family for the two transform types - `qpoint` / `mpoint` and
   * `qprim` / `mprim`. */
  {
    Runner runner;
    if (runner.compile("q@r = qpoint(0, \"mesh\", \"turn\", 1);\n"
                       "m@x = mpoint(0, \"mesh\", \"place\", 0);\n"
                       "q@rp = qprim(0, \"mesh\", \"turn\", 0);\n"
                       "m@xp = mprim(0, \"mesh\", \"place\", 1);"))
    {
      bool qpoint_ok = false, mpoint_ok = false, qprim_ok = false, mprim_ok = false;
      for (const AttrBinding &binding : runner.program().attrs) {
        if (binding.name == "turn" && binding.type == Type::Quaternion &&
            binding.domain == ReadDomain::Point)
        {
          qpoint_ok = binding.read;
        }
        if (binding.name == "place" && binding.type == Type::Matrix &&
            binding.domain == ReadDomain::Point)
        {
          mpoint_ok = binding.read;
        }
        if (binding.name == "turn" && binding.domain == ReadDomain::Prim) {
          qprim_ok = binding.type == Type::Quaternion && binding.read;
        }
        if (binding.name == "place" && binding.domain == ReadDomain::Prim) {
          mprim_ok = binding.type == Type::Matrix && binding.read;
        }
      }
      check_true("WR-18 qpoint binds a rotation point attribute", qpoint_ok);
      check_true("WR-18 mpoint binds a matrix point attribute", mpoint_ok);
      check_true("WR-19 qprim binds the prim-domain rotation", qprim_ok);
      check_true("WR-19 mprim binds the prim-domain matrix", mprim_ok);
    }
    else {
      check_true("WR-18 qpoint binds a rotation point attribute", false, runner.errors());
    }
  }
  if (Runner *r = setup("q@r = qpoint(0, \"mesh\", \"turn\", 1);\n"
                        "m@x = mpoint(0, \"mesh\", \"place\", 2);\n"
                        "f@w = q@r.w;\nf@t = m@x[0][3];",
                        Vec3{},
                        1,
                        false))
  {
    /* element 0 is the identity, element 1 is 180 degrees about X (w = 0, x = 1), and the matrices
     * translate by their index. Reading element 1/2 has to give exactly those. */
    r->set_quat("turn", {1, 0, 0, 0, 0, 1, 0, 0, 1, 0, 0, 0});
    r->set_matrix("place",
                  {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1,
                   1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 1,
                   1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 2, 0, 0, 1});
    r->run_all();
    check("WR-18 qpoint reads the w of the element it asked for", r->scalar("w"), 0.0);
    check("WR-18 mpoint reads the translation of the element it asked for", r->scalar("t"), 2.0);
    delete r;
  }
  /* `WR-20`: an index the table does not have reads 0 - for both transform types. */
  if (Runner *r = setup("q@r = qpoint(0, \"mesh\", \"turn\", 9);\n"
                        "m@x = mpoint(0, \"mesh\", \"place\", -1);\n"
                        "f@w = q@r.w;\nf@t = m@x[0][3];",
                        Vec3{},
                        1,
                        false))
  {
    r->set_quat("turn", {1, 0, 0, 0});
    r->set_matrix("place", {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 5, 0, 0, 1});
    r->run_all();
    check("WR-20 an out-of-range qpoint reads 0", r->scalar("w"), 0.0);
    check("WR-20 a negative mpoint index reads 0", r->scalar("t"), 0.0);
    delete r;
  }
  check_true("WR-19 qprim takes the prim domain",
             first_error("q@r = qprim(0, \"mesh\", \"turn\", 0);").empty());
  check_true("WR-18 qpoint still refuses a bad domain",
             !first_error("q@r = qpoint(0, \"blob\", \"turn\", 0);").empty());
  check_true("WR-18 qpoint takes a run-time index",
             first_error("int i = 1;\nq@r = qpoint(0, \"mesh\", \"turn\", i + 1);").empty());
  {
    Runner runner;
    if (runner.compile("f@r = fprim(0, \"mesh\", \"w\", 0);\nf@np = npoints(1, \"mesh\");\nf@nf = nprimitives(2, \"mesh\");"))
    {
      bool prim = false, points = false, prims = false;
      for (const AttrBinding &binding : runner.program().attrs) {
        if (binding.name == "w") {
          prim = binding.domain == ReadDomain::Prim && binding.type == Type::Float &&
                 binding.read;
        }
        if (binding.name == "#npoints") {
          points = binding.count && binding.host_supplied && binding.input == 1 &&
                   binding.domain == ReadDomain::Point;
        }
        if (binding.name == "#nprimitives") {
          prims = binding.count && binding.host_supplied && binding.input == 2 &&
                  binding.domain == ReadDomain::Prim;
        }
      }
      check_true("fprim binds the prim table (point-read-domain)", prim);
      check_true("npoints(geo, \"mesh\") is a host-supplied count", points);
      check_true("nprimitives(geo, \"mesh\") is a host-supplied count", prims);
    }
    else {
      check_true("fprim binds the prim table (point-read-domain)", false, runner.errors());
    }
  }
  if (Runner *r = setup("f@np = npoints(0, \"mesh\");\nf@nf = nprimitives(0, \"mesh\");", Vec3{}, 1, false)) {
    r->set_counts(8, 6);
    r->run_all();
    check("npoints reports the point table", r->scalar("np"), 8.0f);
    check("nprimitives reports the prim table", r->scalar("nf"), 6.0f);
    delete r;
  }
  if (Runner *r = setup("f@a = f@weight;\nf@b = fpoint(0, \"mesh\", \"weight\", 0);", Vec3{}, 1, false)) {
    int run_over = 0, point = 0;
    for (const AttrBinding &binding : r->program().attrs) {
      if (binding.name == "weight" && binding.domain == ReadDomain::RunOver) {
        run_over++;
      }
      if (binding.name == "weight" && binding.domain == ReadDomain::Point) {
        point++;
      }
    }
    check_true("the @ form and fpoint are separate bindings", run_over == 1 && point == 1);
    r->set_scalar("weight", {1.0f});
    r->run_all();
    check_true("both see the same data", r->scalar("a") == 1.0f && r->scalar("b") == 1.0f);
    delete r;
  }
  {
    Runner runner;
    bool input_ok = false;
    const bool compiled = runner.compile("f@r = fpoint(2, \"mesh\", \"weight\", 0);");
    for (const AttrBinding &binding : runner.program().attrs) {
      if (binding.name == "weight") {
        input_ok = binding.input == 2 && binding.read && !binding.write;
      }
    }
    check_true("fpoint(2, ...) addresses Input 2 read-only (geometry-inputs/point-read-domain)",
               compiled && input_ok,
               runner.errors());
  }
  check_true("fpoint refuses a non-literal stream",
             first_error("f@r = fpoint(geo, \"mesh\", \"P\", 0);").find("literal stream") !=
                 std::string::npos);
  check_true("fpoint refuses a stream outside 0..3",
             first_error("f@r = fpoint(4, \"mesh\", \"P\", 0);").find("0..3") != std::string::npos);
  check_true("fpoint refuses a name that is not a string (point-read-domain)",
             first_error("f@r = fpoint(0, P, 0);").find("string literal") != std::string::npos);
  check_true("fpoint refuses a missing index (point-read-domain)",
             first_error("f@r = fpoint(0, \"mesh\", \"P\");")
                     .find("stream, domain, \"name\", index") != std::string::npos);
  check_true("fpoint refuses a vector index",
             first_error("v@r = fpoint(0, \"mesh\", \"P\", {1, 2, 3});").find("must be a number") !=
                 std::string::npos);
  check_true("npoints wants the domain as a string literal (point-read-domain)",
             first_error("f@r = npoints(0, 1);").find("must be a string literal") !=
                 std::string::npos);
  check_true("npoints refuses a third argument (point-read-domain)",
             first_error("f@r = npoints(0, \"mesh\", 1);").find("two arguments") !=
                 std::string::npos);
  /* point-read-domain/prim-read-domain: the domain word list is per table class - a point table takes four words, a prim
   * table only two. */
  check_true("the point tables accept all four words (point-read-domain)",
             pdt_wrangle::compile("f@a = fpoint(0, \"mesh\", \"P\", 0);\n"
                                  "f@b = fpoint(0, \"curve\", \"P\", 0);\n"
                                  "f@c = fpoint(0, \"pointcloud\", \"P\", 0);\n"
                                  "f@d = fpoint(0, \"instance\", \"P\", 0);\n"
                                  "i@e = npoints(1, \"pointcloud\");")
                 .ok());
  check_true("a word outside the point list is refused (point-read-domain)",
             !pdt_wrangle::compile("f@r = fpoint(0, \"edge\", \"P\", 0);").ok());
  check_true("and the message lists the words",
             first_error("f@r = fpoint(0, \"edge\", \"P\", 0);").find("instance") !=
                 std::string::npos);
  check_true("the prim tables take only mesh and curve (prim-read-domain)",
             !pdt_wrangle::compile("f@r = fprim(0, \"pointcloud\", \"P\", 0);").ok());
  check_true("and an instance is refused there too (prim-read-domain)",
             !pdt_wrangle::compile("f@r = fprim(0, \"instance\", \"P\", 0);").ok());
  check_true("but mesh and curve are fine (prim-read-domain)",
             pdt_wrangle::compile("f@a = fprim(0, \"mesh\", \"P\", 0);\n"
                                  "f@b = fprim(0, \"curve\", \"P\", 0);\n"
                                  "i@c = nprimitives(2, \"curve\");")
                 .ok());
  check_true("the explicit reads are not assignable",
             !first_error("fpoint(0, \"mesh\", \"P\", 0) = 1;").empty());
  check_true("one name in two domains is a type clash",
             first_error("f@a = fpoint(0, \"mesh\", \"w\", 0);\nv@b = vpoint(0, \"mesh\", \"w\", 0);")
                     .find("already read as") != std::string::npos);
  check_true("an unknown f-name is still unknown",
             first_error("f@r = fpointx();").find("unknown") != std::string::npos);
  check_true("Detail allows the explicit reads (at-read / point-read-domain)",
             first_error("float a = fpoint(0, \"mesh\", \"P\", 0);", true).empty());
  check_true("the string names go through the alias table (at-p-scope)",
             first_error("f@r = fpoint(0, \"mesh\", \"P\", 0);").empty() &&
                 first_error("setpointattrib(0, \"mesh\", \"P\", 0, {1, 2, 3});").empty());

  /* ------------------------------------------- channel-read / channel-missing-zero (chf chi chv) */
  {
    Runner runner;
    if (runner.compile(
            "f@a = chf(\"height\");\ni@b = chi(\"height\");\nv@c = chv(\"axis\");"))
    {
      bool chf_ok = false, chi_ok = false, chv_ok = false;
      for (const AttrBinding &binding : runner.program().attrs) {
        if (!binding.channel) {
          continue;
        }
        if (binding.name == "height" && binding.type == Type::Float) {
          chf_ok = binding.host_supplied && binding.read && !binding.write;
        }
        if (binding.name == "height" && binding.type == Type::Int) {
          chi_ok = binding.read && !binding.write;
        }
        if (binding.name == "axis" && binding.type == Type::Vector) {
          chv_ok = binding.read && !binding.write;
        }
      }
      check_true("chf binds a host-supplied channel (channel-read)", chf_ok);
      check_true("chi on the same name is a *second* binding (channel-type-mismatch)", chi_ok);
      check_true("chv binds the vector register file (channel-read)", chv_ok);
    }
    else {
      check_true("chf binds a host-supplied channel (channel-read)", false, runner.errors());
    }
  }
  if (Runner *r = setup("f@h = chf(\"height\");\nv@a = chv(\"axis\");", Vec3{}, 2, false)) {
    r->set_channel("height", 2.5f);
    r->set_channel_vector("axis", Vec3{1.0f, 2.0f, 3.0f});
    r->run_all();
    check("chf reads the channel's value", r->scalar("h"), 2.5f);
    check_vec("chv reads the channel's vector", r->vector("a"), Vec3{1, 2, 3});
    delete r;
  }
  check_true("a channel name must be a string literal (channel-read)",
             first_error("f@a = chf(1);").find("string literal") != std::string::npos);
  check_true("a channel takes exactly one argument (channel-read)",
             first_error("f@a = chf(\"high\", 1);").find("exactly one argument") !=
                 std::string::npos);
  check_true("an unknown channel name still compiles (channel-missing-zero)",
             first_error("f@a = chf(\"nope\");").empty());
  check_true("chv is not a writer (channel-read)",
             !first_error("chv(\"axis\") = 1;").empty());

  /* --------------------------------------------------------------- channel-read: chq / chm */
  {
    Runner runner;
    if (runner.compile("quat q = chq(\"turn\");\n"
                       "matrix x = chm(\"place\");"))
    {
      bool chq_ok = false, chm_ok = false;
      for (const AttrBinding &binding : runner.program().attrs) {
        if (!binding.channel) {
          continue;
        }
        if (binding.name == "turn" && binding.type == Type::Quaternion) {
          chq_ok = binding.host_supplied && binding.read && !binding.write;
        }
        if (binding.name == "place" && binding.type == Type::Matrix) {
          chm_ok = binding.host_supplied && binding.read && !binding.write;
        }
      }
      check_true("WR-36 chq binds a host-supplied rotation channel", chq_ok);
      check_true("WR-36 chm binds a host-supplied matrix channel", chm_ok);
    }
    else {
      check_true("WR-36 chq binds a host-supplied rotation channel", false, runner.errors());
    }
  }
  if (Runner *r = setup("quat q = chq(\"turn\");\n"
                        "matrix x = chm(\"place\");\n"
                        "f@w = q.w;\nv@axis = rotate_vector({1,0,0}, q);\n"
                        "f@t = x[0][3];\nv@moved = x * {0,0,0};",
                        Vec3{},
                        1,
                        false))
  {
    /* The host would read these from the node's sockets; the harness hands over the same shape.
     * `turn` is a 90 degree rotation about Z (w = x = sin(45)), `place` translates to (7, 0, 0). */
    r->set_channel_quat("turn", 0.7071068f, 0.0f, 0.0f, 0.7071068f);
    r->set_channel_matrix("place", {1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 7, 0, 0, 1});
    r->run_all();
    check("WR-36 chq reads the rotation's w", r->scalar("w"), 0.7071068);
    check_vec("WR-36 chq's rotation really turns a vector", r->vector("axis"), Vec3{0, 1, 0}, 1e-4);
    check("WR-36 chm reads a matrix entry", r->scalar("t"), 7.0);
    check_vec("WR-36 chm's matrix really translates", r->vector("moved"), Vec3{7, 0, 0}, 1e-4);
    delete r;
  }
  if (Runner *r = setup("quat q = chq(\"scalar_channel\");\n"
                        "matrix x = chm(\"vector_channel\");\n"
                        "f@w = q.w;\nf@m = x[0][0];",
                        Vec3{},
                        1,
                        false))
  {
    /* `WR-39`: a channel of another type reads as zero. The harness is deliberately given values
     * for both names, so a non-zero answer would mean the type was ignored. */
    r->set_channel("scalar_channel", 3.0f);
    r->set_channel_vector("vector_channel", Vec3{1, 2, 3});
    r->run_all();
    check("WR-39 chq on a scalar channel reads 0", r->scalar("w"), 0.0);
    check("WR-39 chm on a vector channel reads 0", r->scalar("m"), 0.0);
    delete r;
  }
  check_true("WR-38 an unknown rotation channel still compiles",
             first_error("quat q = chq(\"nope\");").empty());
  /* The right-hand side is a real rotation/matrix, so only "a channel is not a writer" can be the
   * reason these fail. */
  check_true("WR-39 chq is not a writer",
             !first_error("quat r = axis_angle_rotation({0,0,1}, 1.0);\nchq(\"turn\") = r;").empty());
  check_true("WR-39 chm is not a writer",
             !first_error("matrix x = combine_transform({0,0,0}, quaternion(1,0,0,0), {1,1,1});\n"
                          "chm(\"place\") = x;")
                  .empty());

  /* ------------------------------- buffered cross-element writes (set-point-attrib / ordered-parallelism) */
  {
    Runner runner;
    const AttrBinding *binding = nullptr;
    const bool compiled = runner.compile("setpointattrib(0, \"mesh\", \"w\", 3, 1.5);");
    for (const AttrBinding &candidate : runner.program().attrs) {
      if (candidate.name == "w") {
        binding = &candidate;
      }
    }
    check_true("setpointattrib binds a buffered point write (set-point-attrib)",
               compiled && binding != nullptr && binding->domain == ReadDomain::Point &&
                   binding->type == Type::Float && binding->indexed_write && binding->write,
               runner.errors());
  }
  {
    Runner runner;
    bool prim = false;
    const bool compiled = runner.compile("setprimattrib(0, \"mesh\", \"w\", 1, {1, 2, 3});");
    for (const AttrBinding &binding : runner.program().attrs) {
      if (binding.name == "w") {
        prim = binding.domain == ReadDomain::Prim && binding.type == Type::Vector &&
               binding.indexed_write;
      }
    }
    check_true("setprimattrib targets the prim table with a vector value", compiled && prim,
               runner.errors());
  }
  if (Runner *r = setup("setpointattrib(0, \"mesh\", \"w\", @elemnum, @elemnum);", Vec3{}, 3, false)) {
    r->run_all();
    bool ok = true;
    for (int i = 0; i < 3; i++) {
      const std::vector<WriteEntry> &writes = r->element_writes(i);
      ok = ok && writes.size() == 1 && writes[0].index == i && writes[0].scalar == float(i);
    }
    check_true("each element buffers its own write instead of applying it (ordered-parallelism)", ok);
    delete r;
  }
  if (Runner *r = setup("setpointattrib(0, \"mesh\", \"w\", 0, 1);\nsetpointattrib(0, \"mesh\", \"w\", 1, 2);",
                        Vec3{},
                        1,
                        false))
  {
    r->run_all();
    const std::vector<WriteEntry> &writes = r->element_writes(0);
    check_true("writes keep their call order inside an element (ordered-parallelism)",
               writes.size() == 2 && writes[0].index == 0 && writes[0].scalar == 1.0f &&
                   writes[1].index == 1 && writes[1].scalar == 2.0f);
    delete r;
  }
  if (Runner *r = setup("setpointattrib(0, \"mesh\", \"w\", 0, 5);\nf@back = f@w;", Vec3{}, 1, false)) {
    r->set_scalar("w", {7.0f});
    r->run_all();
    check_true("a buffered write does not change what this element reads (ordered-parallelism promise 2)",
               r->scalar("back") == 7.0f && r->element_writes(0).size() == 1 &&
                   r->element_writes(0)[0].scalar == 5.0f);
    delete r;
  }
  check_true("the target stream must be the literal 0 (indexed-write-error)",
             first_error("setpointattrib(1, \"mesh\", \"w\", 0, 1);").find("literal 0") !=
                 std::string::npos);
  check_true("setpointattrib refuses a name that is not a string",
             first_error("setpointattrib(0, w, 0, 1);").find("string literal") !=
                 std::string::npos);
  check_true("setpointattrib refuses a missing value",
             first_error("setpointattrib(0, \"mesh\", \"w\", 0);")
                     .find("takes (0, domain, \"name\", index, value)") != std::string::npos);
  check_true("setpointattrib refuses a vector index",
             first_error("setpointattrib(0, \"mesh\", \"w\", {1, 2}, 1);").find("must be a number") !=
                 std::string::npos);
  check_true("one attribute cannot be written as two types",
             first_error("setpointattrib(0, \"mesh\", \"w\", 0, 1);\nsetpointattrib(0, \"mesh\", \"w\", 1, {1,2,3});")
                     .find("already written as") != std::string::npos);
  check_true("Detail allows the buffered writes (at-read)",
             first_error("setpointattrib(0, \"mesh\", \"w\", 3, 1.0);", true).empty());

  /* ------------------------------------------- addpoint and its numbering (ordered-parallelism promise 1) */
  if (Runner *r = setup("i@a = addpoint(0, \"mesh\", {1, 2, 3});\ni@b = addpoint(0, \"mesh\", {4, 5, 6});",
                    Vec3{}, 3, false)) {
    r->set_new_point_base(8); /* the input had 8 points, so numbering starts right after them */
    r->run_all();
    bool adjacent = true, counted = true;
    for (int i = 0; i < 3; i++) {
      adjacent = adjacent && r->scalar("a", i) == float(8 + i * 100) &&
                 r->scalar("b", i) == float(8 + i * 100 + 1);
      counted = counted && r->element_new_points(i) == 2;
    }
    check_true("addpoint hands out adjacent numbers from the element's base (ordered-parallelism)",
               adjacent && counted);
    delete r;
  }
  if (Runner *r = setup("int p = addpoint(0, \"mesh\", {1, 2, 3});\nsetpointattrib(0, \"mesh\", \"w\", p, 1.5);",
                    Vec3{}, 2, false))
  {
    r->set_new_point_base(8);
    r->run_all();
    check_true("the number addpoint returns addresses the new element in a write (ordered-parallelism)",
               r->element_writes(0).size() == 1 && r->element_writes(0)[0].index == 8 &&
                   r->element_writes(0)[0].scalar == 1.5f &&
                   r->element_writes(1)[0].index == 108);
    delete r;
  }
  check_true("addpoint obeys the literal-0 rule (add-point)",
             !first_error("addpoint(1, \"mesh\", {0, 0, 0});").empty());
  check_true("the domain must be a string literal (add-point)",
             !first_error("addpoint(0, 1, {0, 0, 0});").empty());
  check_true("an unknown point table is refused (add-point)",
             !first_error("addpoint(0, \"curve\", {0, 0, 0});").empty());
  check_true("and the message names the two tables",
             first_error("addpoint(0, \"curve\", {0, 0, 0});").find("pointcloud") !=
                 std::string::npos);
  check_true("pos is required (add-point)", !first_error("addpoint(0, \"mesh\");").empty());
  check_true("pos must be a vector (add-point)",
             !first_error("addpoint(0, \"mesh\", 1);").empty());

  /* ------------------------------ addprim and the removals (add-prim / remove-point) */
  {
    Runner runner;
    const bool compiled = runner.compile("i@p = addprim(0, \"mesh\", \"mesh\", 0, 1, 2);");
    const AttrBinding *binding = nullptr;
    for (const AttrBinding &candidate : runner.program().attrs) {
      if (candidate.topology == AttrBinding::Topology::AddPrim) {
        binding = &candidate;
      }
    }
    check_true("addprim(\"poly\", …) records three points and a mesh face (add-prim)",
               compiled && runner.program().creates_topology && binding != nullptr &&
                   !binding->polyline && binding->argc == 3 &&
                   binding->domain == ReadDomain::Prim,
               runner.errors());
  }
  {
    Runner runner;
    bool polyline = false;
    int argc = 0;
    const bool compiled = runner.compile("addprim(0, \"curve\", \"mesh\", 0, 1);");
    for (const AttrBinding &binding : runner.program().attrs) {
      if (binding.topology == AttrBinding::Topology::AddPrim) {
        polyline = binding.polyline;
        argc = binding.argc;
      }
    }
    check_true("addprim(\"polyline\", …) is the curve spline type (add-prim)",
               compiled && polyline && argc == 2,
               runner.errors());
  }
  check_true("addprim rejects a domain outside the prim list (add-prim)",
             first_error("i@p = addprim(0, \"tri\", \"mesh\", 0, 1, 2);").find("unknown domain") !=
                 std::string::npos);
  check_true("and a pointdomain outside the point list (add-prim)",
             !first_error("i@p = addprim(0, \"mesh\", \"tri\", 0, 1, 2);").empty());
  check_true("a mesh face needs three points (add-prim)",
             first_error("i@p = addprim(0, \"mesh\", \"mesh\", 0, 1);").find("at least 3") !=
                 std::string::npos);
  check_true("a curve spline takes one (add-prim)",
             pdt_wrangle::compile(
                 "i@p = addprim(0, \"curve\", \"mesh\", 0);\n"
                 "i@q = addprim(0, \"curve\", \"pointcloud\", 1);\n"
                 "i@r = addprim(0, \"mesh\", \"mesh\", 0, 1, 2);")
                 .ok());
  check_true("addprim needs a string type (add-prim)",
             first_error("i@p = addprim(0, poly, 0, 1, 2);").find("string literal") !=
                 std::string::npos);
  {
    Runner runner;
    bool remove_point = false;
    const bool compiled = runner.compile("removepoint(0, \"mesh\", 3);");
    for (const AttrBinding &binding : runner.program().attrs) {
      if (binding.topology == AttrBinding::Topology::RemovePoint) {
        remove_point = binding.domain == ReadDomain::Point;
      }
    }
    check_true("removepoint records a point removal and says it creates topology (remove-point)",
               compiled && remove_point && runner.program().creates_topology,
               runner.errors());
  }
  check_true("removeprim takes exactly two arguments (remove-prim)",
             !first_error("removeprim(0, 1, 2);").empty());
  check_true("removepoint obeys the literal-0 rule (set-point-attrib)",
             !first_error("removepoint(1, \"mesh\", 3);").empty());

  /* The point numbers have to travel with the request: after the run the registers are gone. */
  if (Runner *r = setup("i@p = addprim(0, \"mesh\", \"mesh\", 0, 1, 2);", Vec3{}, 1, false)) {
    r->run_all();
    const std::vector<TopologyEntry> &topology = r->element_topology(0);
    check_true("addprim carries its point numbers out of the VM (add-prim)",
               topology.size() == 1 && topology[0].points.size() == 3 &&
                   topology[0].points[0] == 0 && topology[0].points[1] == 1 &&
                   topology[0].points[2] == 2);
    delete r;
  }
  if (Runner *r = setup("int a = 7;\ni@p = addprim(0, \"mesh\", \"mesh\", a, a + 1, 3 - 1);",
                        Vec3{},
                        1,
                        false))
  {
    r->run_all();
    const std::vector<TopologyEntry> &topology = r->element_topology(0);
    check_true("the point numbers may be any expression (add-prim)",
               topology.size() == 1 && topology[0].points.size() == 3 &&
                   topology[0].points[0] == 7 && topology[0].points[1] == 8 &&
                   topology[0].points[2] == 2);
    delete r;
  }

  /* ------------------------------------------------------------ listing */
  {
    Runner runner;
    if (runner.compile("@P.y += sin(@P.x * 1.7) * 0.5;")) {
      std::printf("--- disassembly of '@P.y += sin(@P.x * 1.7) * 0.5;' ---\n%s",
                  runner.program().disassemble().c_str());
    }
  }
  std::printf("--- math library: %zu functions ---\n", kFunctions().size());

  /* ------------------------------------------- WR-31/WR-32: the Code box's lexical highlight */
  {
    auto class_at = [](const FormatArray &format, const std::string &code, const std::string &needle) {
      const size_t at = code.find(needle);
      return (at == std::string::npos) ? TokenClass::Plain : format[at];
    };
    auto all_same = [](const FormatArray &format, const std::string &code, const std::string &needle,
                       const TokenClass want) {
      const size_t at = code.find(needle);
      if (at == std::string::npos) {
        return false;
      }
      for (size_t k = at; k < at + needle.size(); k++) {
        if (format[k] != want) {
          return false;
        }
      }
      return true;
    };
    auto has_class = [](const FormatArray &format, const TokenClass want) {
      return std::any_of(format.begin(), format.end(), [&](const TokenClass c) { return c == want; });
    };

    const std::string code =
        "// a line comment\n"
        "float h = noise(@P * 0.1) * 2.0;\n"
        "vector shade = {0.2, 0.6, 0.9};\n"
        "f@height = h;\n"
        "v@Cd = shade;\n"
        "if (h > 1.0) { h = 1.0; } else { h = 0.0; }\n"
        "for (int k = 0; k < 3; k++) { h += 1e-3; }\n"
        "string tag = \"ridge\";\n"
        "/* a block comment */\n"
        "int n = setpointattrib(0, \"mesh\", \"w\", 0, 1.0);\n";

    const FormatArray format = format_code(code);

    check_true("WR-31 the format array has exactly one class per character",
               format.size() == code.size());
    check_true("WR-31 @P is one reference", all_same(format, code, "@P", TokenClass::Reference));
    check_true("WR-31 f@height is one reference, prefix included",
               all_same(format, code, "f@height", TokenClass::Reference));
    check_true("WR-31 v@Cd is one reference", all_same(format, code, "v@Cd", TokenClass::Reference));
    check_true("WR-31 float is a type", all_same(format, code, "float", TokenClass::Type));
    check_true("WR-31 vector is a type", all_same(format, code, "vector", TokenClass::Type));
    check_true("WR-31 int is a type", all_same(format, code, "int", TokenClass::Type));
    check_true("WR-31 noise is a function", all_same(format, code, "noise", TokenClass::Function));
    check_true("WR-31 setpointattrib is a function (the compiler's own table)",
               all_same(format, code, "setpointattrib", TokenClass::Function));
    check_true("WR-31 if and else are keywords", all_same(format, code, "if", TokenClass::Keyword) &&
                                                      all_same(format, code, "else", TokenClass::Keyword));
    check_true("WR-31 for is a keyword", all_same(format, code, "for", TokenClass::Keyword));
    check_true("WR-31 plain identifiers stay plain", all_same(format, code, "h = noise", TokenClass::Plain) == false &&
                                                          class_at(format, code, "h = noise") == TokenClass::Plain);
    check_true("WR-31 a number is a number", all_same(format, code, "2.0", TokenClass::Number) &&
                                                 all_same(format, code, "1e-3", TokenClass::Number));
    check_true("WR-31 the quotes and the contents are one class",
               all_same(format, code, "\"ridge\"", TokenClass::String));
    check_true("WR-31 a line comment is a comment",
               all_same(format, code, "// a line comment", TokenClass::Comment));
    check_true("WR-31 a block comment is a comment",
               all_same(format, code, "/* a block comment */", TokenClass::Comment));

    /* The lexer must not be fooled by markers inside a string, and it must not judge legality. */
    const std::string tricky = "string s = \"a//b\";\nfloat q = undeclared_thing;\n";
    const FormatArray tricky_format = format_code(tricky);
    check_true("WR-31 a // inside a string is not a comment",
               all_same(tricky_format, tricky, "\"a//b\"", TokenClass::String) &&
                   !has_class(tricky_format, TokenClass::Comment));
    check_true("WR-32 an undefined name is not an error - the lexer does not judge (WR-2 does)",
               !has_class(tricky_format, TokenClass::Error));

    /* `WR-47`: the rotation type is spelled `quat` exactly so that the type name and the *function*
     * name `quaternion(...)` can be told apart. They used to be the same word, and the type won the
     * colour (types are classified before functions), so the function was painted as a type. */
    {
      const std::string rotation = "quat q = quaternion(1, 0, 0, 0);\n";
      const FormatArray rotation_format = format_code(rotation);
      check_true("WR-47 the rotation type highlights as a type",
                 all_same(rotation_format, rotation, "quat", TokenClass::Type));
      check_true("WR-47 and the function highlights as a function",
                 all_same(rotation_format, rotation, "quaternion", TokenClass::Function));
      check_true("WR-47 the matrix type highlights as a type too",
                 all_same(format_code("matrix m;"), "matrix m;", "matrix", TokenClass::Type));
    }

    /* The anti-drift guarantee: every name the highlight paints as a function is a name the
     * compiler accepts as a call (and that includes the explicit-index family, which lives in the
     * VM's own tables rather than in `kFunctions()`). */
    check_true("WR-31 fpoint compiles as a call",
               first_error("float a = fpoint(0, \"mesh\", \"P\", 0);").empty());
    check_true("WR-31 setpointattrib compiles as a call",
               first_error("setpointattrib(0, \"mesh\", \"w\", 0, 1.0);").empty());
    check_true("WR-31 addpoint compiles as a call",
               first_error("int a = addpoint(0, \"mesh\", {0, 0, 0});").empty());
    check_true("WR-31 removeprim compiles as a call",
               first_error("removeprim(0, \"mesh\", 0);").empty());
    check_true("WR-31 nprimitives compiles as a call",
               first_error("int a = nprimitives(0, \"mesh\");").empty());
    check_true("WR-36 chf compiles as a call",
               first_error("float a = chf(\"height\");").empty());
    check_true("WR-36 chi compiles as a call",
               first_error("int a = chi(\"count\");").empty());
    check_true("WR-36 chv compiles as a call",
               first_error("vector a = chv(\"axis\");").empty());

    /* WR-32: the only thing taken from a compile is the error's position. */
    const std::string broken = "float h = ;\n";
    FormatArray marked = format_code(broken);
    mark_error(marked, broken, 1, 11);
    check_true("WR-32 mark_error marks the token the compiler pointed at",
               marked[10] == TokenClass::Error);
    FormatArray marked_name = format_code(broken);
    mark_error(marked_name, broken, 1, 7);
    check_true("WR-32 and it can mark a whole identifier",
               marked_name[6] == TokenClass::Error && marked_name[7] != TokenClass::Error);
  }

  /* ------------------------------------------- WR-33/WR-34: the Code box s two shortcuts */
  {
    auto state_of = [](const std::string &code, const int a, const int b) {
      CodeEditState s;
      s.code = code;
      s.sel_start = a;
      s.sel_end = b;
      s.cursor = a;
      return s;
    };
    /** The same, with the caret deliberately somewhere else than the selection (`Tab` and ordinary
     * typing advance `pos` without moving `selsta`/`selend`, so the framework really has this
     * state: "no selection" is `sel_start == sel_end`, not "caret at the selection"). */
    auto caret_state_of = [](const std::string &code, const int caret) {
      CodeEditState s;
      s.code = code;
      s.sel_start = 0;
      s.sel_end = 0;
      s.cursor = caret;
      return s;
    };

    /* The regression the user found in the GUI: the selection has to stay on the lines it covered,
     * however long the transform makes them. Re-searching the new code with the *old* offsets grew
     * the range by one line per press ("each Shift+Tab selects one more line"), because an outdent
     * shortens the text in front of it. */
    {
      CodeEditState sel = state_of("    a\n    b\n    c\n    d\n", 6, 12);
      sel = indent_lines(sel, false);
      check_true("WR-34 Shift+Tab on a selection of line 2 outdents just that line",
                 sel.code == "    a\nb\n    c\n    d\n");
      check_true("WR-34 and the selection stays on line 2 (no drift)",
                 sel.sel_start == 6 && sel.sel_end == 8);
      /* Pressed again there is nothing left to remove, and the state must not move at all. */
      const CodeEditState again = indent_lines(sel, false);
      check_true("WR-34 a second Shift+Tab changes nothing and keeps the selection",
                 again.code == sel.code && again.sel_start == 6 && again.sel_end == 8);

      CodeEditState lines4 = state_of("        a\n        b\n", 0, 18);
      lines4 = indent_lines(lines4, false);
      check_true("WR-34 Shift+Tab on both lines keeps both lines selected",
                 lines4.code == "    a\n    b\n" && lines4.sel_start == 0 && lines4.sel_end == 12);
      lines4 = indent_lines(lines4, false);
      check_true("WR-34 and again (fourteen characters shorter, same two lines)",
                 lines4.code == "a\nb\n" && lines4.sel_start == 0 && lines4.sel_end == 4);

      CodeEditState comment = state_of("    a\n    b\n    c\n", 6, 12);
      comment = toggle_comment(comment);
      check_true("WR-33 Ctrl+/ on a selection of line 2 comments just that line",
                 comment.code == "    a\n    // b\n    c\n");
      check_true("WR-33 and the selection stays on line 2",
                 comment.sel_start == 6 && comment.sel_end == 15);
      const CodeEditState un = toggle_comment(comment);
      check_true("WR-33 the second Ctrl+/ takes it away again, same line",
                 un.code == "    a\n    b\n    c\n" && un.sel_start == 6 && un.sel_end == 12);
    }

    /* No selection: the caret's line is what the L0 names, and the caret is the *cursor*, not a
     * stale `sel_start` the framework left behind. */
    {
      CodeEditState repeat = state_of("    a\n    b\n        c\n", 0, 0);
      repeat = indent_lines(repeat, false);
      check_true("WR-34 Shift+Tab with the caret on line 1 outdents line 1, no selection",
                 repeat.sel_start == repeat.sel_end && repeat.code == "a\n    b\n        c\n");
      /* The line has no leading space left, so the transform has nothing to do - and it must not
       * move on to another line (that is what the GUI report described). */
      const CodeEditState stuck = indent_lines(repeat, false);
      check_true("WR-34 and a second press is a no-op on the same line",
                 stuck.sel_start == stuck.sel_end && stuck.code == repeat.code);

      CodeEditState second = state_of("    a\n    b\n        c\n", 10, 10);
      second = indent_lines(second, false);
      check_true("WR-34 Shift+Tab with the caret on line 2 outdents line 2",
                 second.code == "    a\nb\n        c\n" && second.sel_start == 6 &&
                     second.sel_end == 6);

      CodeEditState caret = indent_lines(caret_state_of("    a\n    b\n", 8), false);
      check_true("WR-34 with no selection the caret's own line is the one that is outdented",
                 caret.code == "    a\nb\n" && caret.sel_start == 6 && caret.sel_end == 6);
      caret = toggle_comment(caret_state_of("    a\n    b\n", 8));
      check_true("WR-33 with no selection the caret's own line is the one that is commented",
                 caret.code == "    a\n    // b\n" && caret.sel_start == 8 && caret.sel_end == 8);
    }

    CodeEditState s = toggle_comment(state_of("float h = 1.0;\n", 0, 0));
    check_true("WR-33 Ctrl+/ comments the cursor line", s.code == "// float h = 1.0;\n");
    s = toggle_comment(state_of("// float h = 1.0;\n", 0, 0));
    check_true("WR-33 and takes it away again", s.code == "float h = 1.0;\n");
    s = toggle_comment(state_of("    float h = 1.0;\n", 6, 6));
    check_true("WR-33 keeps the indentation", s.code == "    // float h = 1.0;\n");
    s = toggle_comment(state_of("    // a\n", 4, 4));
    check_true("WR-33 uncomments an indented line and keeps the indent", s.code == "    a\n");
    s = toggle_comment(state_of("// a\n// b\n", 0, 8));
    check_true("WR-33 all-commented selection is uncommented as a whole", s.code == "a\nb\n");
    s = toggle_comment(state_of("// a\nb\n", 0, 7));
    check_true("WR-33 partly commented selection is commented as a whole",
               s.code == "// // a\n// b\n");
    s = toggle_comment(state_of("string t = \"// nope\";\n", 0, 0));
    check_true("WR-33 a // inside a string is not a comment prefix",
               s.code == "// string t = \"// nope\";\n");

    s = indent_lines(state_of("a = 1;\n", 0, 0), true);
    check_true("WR-34 Tab at the cursor inserts four spaces",
               s.code == "    a = 1;\n" && s.cursor == 4);
    s = indent_lines(state_of("a\nb\n", 0, 4), true);
    check_true("WR-34 Tab indents every covered line", s.code == "    a\n    b\n");
    s = indent_lines(state_of("    a\n  b\n", 0, 11), false);
    check_true("WR-34 Shift+Tab removes up to four spaces per line", s.code == "a\nb\n");
    s = indent_lines(state_of("  a\n", 0, 0), false);
    check_true("WR-34 Shift+Tab removes what is there when it is less than four", s.code == "a\n");
    s = indent_lines(state_of("x = 1;\n", 0, 0), false);
    check_true("WR-34 Shift+Tab leaves a line with no leading space alone", s.code == "x = 1;\n");
    s = indent_lines(state_of("\t x\n", 0, 0), false);
    check_true("WR-34 Shift+Tab does nothing when the line starts with a tab", s.code == "\t x\n");
  }

  /* ------------------------- WR-43…WR-58: the rotation and matrix functions */
  {
    /* Every number below is Blender's own: a 90 degree turn about Z is `radians(90)`, and the
     * functions are the `FunctionNode*` nodes' maths, so these assertions are the node results. */
    if (Runner *r = setup("quat q = axis_angle_rotation({0,0,1}, radians(90)); "
                          "v@a = rotate_vector({1,0,0}, q);")) {
      check_vec("WR-44 rotate_vector 90 deg about Z turns X into Y", r->vector("a"), Vec3{0, 1, 0});
      delete r;
    }
    if (Runner *r = setup("quat q = axis_angle_rotation({0,0,1}, radians(90)); "
                          "quat by = axis_angle_rotation({1,0,0}, radians(90)); "
                          "quat g = rotate_rotation(\"global\", q, by); "
                          "quat l = rotate_rotation(\"local\", q, by); "
                          "v@g = rotate_vector({1,0,0}, g); v@l = rotate_vector({1,0,0}, l);")) {
      /* base = 90 deg about Z, by = 90 deg about X. Global applies `by` first (`by * base`): X
       * goes to Y and then the base Z turn leaves it on Y... measured: the pair lands on Z for
       * global and on Y for local, which is the difference the two buttons make. */
      check_vec("WR-43 global composes as by * base", r->vector("g"), Vec3{0, 0, 1});
      check_vec("WR-43 local composes as base * by", r->vector("l"), Vec3{0, 1, 0});
      delete r;
    }
    if (Runner *r = setup("quat q = axis_angle_rotation({0,0,0}, 1.5); "
                          "v@a = rotate_vector({1,2,3}, q);")) {
      check_vec("WR-45 a zero axis leaves the vector alone", r->vector("a"), Vec3{1, 2, 3});
      delete r;
    }
    if (Runner *r = setup("quat q = euler_rotation({0, 0, radians(90)}); "
                          "v@a = rotate_vector({1,0,0}, q); "
                          "v@b = to_euler(q);")) {
      check_vec("WR-46 euler_rotation turns X into Y", r->vector("a"), Vec3{0, 1, 0});
      check_vec("WR-48 to_euler is the inverse", r->vector("b"), Vec3{0, 0, 1.5707963}, 1e-5);
      delete r;
    }
    if (Runner *r = setup("quat q = quaternion(0.7071068, 0, 0, 0.7071068); "
                          "f@w = q.w; v@xyz = {q.x, q.y, q.z}; v@v = rotate_vector({1,0,0}, q);")) {
      check("WR-47 a quaternion's first component is w", r->scalar("w"), 0.7071068);
      check_vec("WR-47 and the .x/.y/.z components follow", r->vector("xyz"), Vec3{0, 0, 0.7071068});
      check_vec("WR-47 the w-first quat rotates X into Y", r->vector("v"), Vec3{0, 1, 0});
      delete r;
    }
    if (Runner *r = setup("quat q = axis_angle_rotation({0,0,1}, radians(90)); "
                          "vector axis; float angle; to_axis_angle(q, &axis, &angle); "
                          "v@a = axis; f@b = degrees(angle);")) {
      check_vec("WR-49 an out parameter takes the axis", r->vector("a"), Vec3{0, 0, 1});
      check("WR-49 and the other takes the angle", r->scalar("b"), 90);
      delete r;
    }
    if (Runner *r = setup("quat q = axes_rotation(\"z\", \"x\", {1,0,0}, {0,1,0}); "
                          "v@a = rotate_vector({0,0,1}, q); v@b = rotate_vector({1,0,0}, q); "
                          "v@c = rotate_vector({0,1,0}, q);")) {
      /* `WR-50` with primary = Z and secondary = X: the local Z must land exactly on the primary
       * `{1,0,0}` and the local X as close as possible to the secondary `{0,1,0}`. Reading all three
       * basis rows pins the whole rotation - which is what catches a transposed answer. The first
       * `quat_from_mat3` read its off-diagonal terms column-major in three of its four branches, so
       * every matrix that landed in one of those came back mirrored (this pair used to be asserted
       * only through the degenerate `primary = {0,0,1}` case, which is the identity either way). */
      check_vec("WR-50 the primary axis is exact", r->vector("a"), Vec3{1, 0, 0}, 1e-5);
      check_vec("WR-50 and the secondary follows", r->vector("b"), Vec3{0, 1, 0}, 1e-5);
      check_vec("WR-50 so the third axis is the cross product", r->vector("c"), Vec3{0, 0, 1}, 1e-5);
      delete r;
    }
    if (Runner *r = setup("quat q = axes_rotation(\"y\", \"x\", {0,0,1}, {1,0,0}); "
                          "v@a = rotate_vector({0,1,0}, q); v@b = rotate_vector({1,0,0}, q); "
                          "v@c = rotate_vector({0,0,1}, q);")) {
      /* A second pair (this one has orthogonal inputs, so both axes are exact) that lands in the
       * `r[4] > r[8]` branch of the matrix -> quat conversion. Local Y is the primary axis
       * here, and it has to come out as the primary `{0,0,1}`; local X is the secondary. */
      check_vec("WR-50 orthogonally paired axes send the primary exactly",
                r->vector("a"),
                Vec3{0, 0, -1},
                1e-5);
      check_vec("WR-50 and the secondary exactly", r->vector("b"), Vec3{1, 0, 0}, 1e-5);
      check_vec("WR-50 with the third axis filled in", r->vector("c"), Vec3{0, 1, 0}, 1e-5);
      delete r;
    }
    if (Runner *r = setup("quat q = axes_rotation(\"x\", \"x\", {1,0,0}, {0,1,0}); "
                          "quat i = quaternion(1, 0, 0, 0); "
                          "v@a = to_euler(q); v@b = to_euler(i);")) {
      /* The node ref that the primary and secondary axis have to differ; the function falls back
       * to the identity, so the whole rotation is the identity here. */
      check_vec("WR-50 equal axes fall back to the identity", r->vector("a"), r->vector("b"));
      delete r;
    }
    if (Runner *r = setup("quat q = axis_angle_rotation({0,0,1}, radians(30)); "
                          "v@a = rotate_vector({1,0,0}, invert_rotation(q));")) {
      check_vec("WR-51 invert_rotation undoes it", r->vector("a"), Vec3{0.8660254, -0.5, 0}, 1e-5);
      delete r;
    }
    if (Runner *r = setup("quat q = align_rotation_to_vector(\"z\", \"auto\", "
                          "quaternion(1,0,0,0), 1.0, {1,0,0}); "
                          "v@a = rotate_vector({0,0,1}, q);")) {
      check_vec("WR-52 the aligned rotation points local Z along the vector",
                r->vector("a"),
                Vec3{1, 0, 0},
                1e-5);
      delete r;
    }
    if (Runner *r = setup("matrix m = combine_transform({1,2,3}, quaternion(1,0,0,0), {3,3,3}); "
                          "v@a = m * {1,0,0}; v@b = m * {0,0,0};")) {
      check_vec("WR-53 combine_transform applies the scale", r->vector("a"), Vec3{3, 0, 0}, 3);
      check_vec("WR-53 and the translation", r->vector("b"), Vec3{1, 2, 3});
      delete r;
    }
    if (Runner *r = setup("matrix s = combine_transform({0,0,0}, quaternion(1,0,0,0), {3,3,3}); "
                          "matrix t = combine_transform({1,2,3}, quaternion(1,0,0,0), {1,1,1}); "
                          "matrix a = matrix_multiply(t, s); "
                          "matrix b = matrix_multiply(s, t); "
                          "v@a = a * {1,0,0}; v@b = b * {1,0,0};")) {
      /* `WR-54`: the node's product is `second * first` in Blender's row-vector sense, so the
       * *later* argument is applied first - measured: `matrix_multiply(t, s)` lands the point on
       * the translated spot unscaled, while the mirrored call scales the translated point. */
      check_vec("WR-54 matrix_multiply(t, s) applies s first", r->vector("a"), Vec3{4, 2, 3});
      check_vec("WR-54 and the mirrored call applies t first", r->vector("b"), Vec3{6, 6, 9});
      delete r;
    }
    if (Runner *r = setup("matrix m = combine_transform({1,2,3}, "
                          "axis_angle_rotation({1,0,0}, radians(45)), {2,2,2}); "
                          "matrix i = matrix_multiply(m, invert_matrix(m)); "
                          "v@a = i * {7,8,9}; f@d = matrix_determinant(m);")) {
      check_vec("WR-55 invert_matrix undoes the transform", r->vector("a"), Vec3{7, 8, 9}, 1e-3);
      check("WR-57 matrix_determinant of a 45 degree rotated 2x scale", r->scalar("d"), 8, 1e-3);
      delete r;
    }
    if (Runner *r = setup("matrix z; v@a = z * {1,2,3};")) {
      check_vec("WR-53 a declared matrix starts as the identity", r->vector("a"), Vec3{1, 2, 3});
      delete r;
    }
    if (Runner *r = setup("matrix m = combine_transform({1,2,3}, quaternion(1,0,0,0), {4,5,6}); "
                          "matrix t = transpose_matrix(m); "
                          "v@col = t[3]; v@row = {t[0][3], t[1][3], t[2][3]}; "
                          "f@cell = t[3][0]; f@scale = m[1][1];")) {
      /* `m` scales by (4,5,6) and translates by (1,2,3), so `m[0][3]`/`m[1][3]`/`m[2][3]` are that
       * translation (the flat element 12/13/14, the last column).
       *
       * A transpose swaps the entries at (row, column) and (column, row): the translation moves
       * from the last *column* (flat 12) to the last *row*, which is flat element 3 - so `t[3][0]`
       * is the translation's x and `t[0][3]` is 0. Verified by mapping every index
       * (`qa/wrangle/_scratch_transpose_map.cpp`: input element k holds k, so the output shows each
       * source; 3 and 12 swap).
       *
       * `mat_transpose` itself used to be a no-op - it read `mat_index(row, col)` and wrote
       * `mat_index(col, row)`, which over column-major storage ARE the same index, so it only
       * permuted equal indices. That part is fixed and this assertion now pins the real answer. */
      check_vec("WR-56 [column] and the last column of t is the old last row, so (0,0,0)",
                r->vector("col"),
                Vec3{0, 0, 0});
      check_vec("WR-56 [last row] t[r][3] is the old column 3, which was (0,0,0)",
                r->vector("row"),
                Vec3{0, 0, 0});
      check("WR-56 t[3][0] is the translation's x, moved to the last row", r->scalar("cell"), 1);
      check("WR-56 while m[1][1] is its scale", r->scalar("scale"), 5);
      delete r;
    }
    if (Runner *r = setup("matrix m = combine_transform({4,5,6}, quaternion(1,0,0,0), {7,8,9}); "
                          "vector t; quat q; vector s; "
                          "separate_transform(m, &t, &q, &s); v@t = t; v@s = s;")) {
      check_vec("WR-58 separate_transform gives the translation", r->vector("t"), Vec3{4, 5, 6});
      check_vec("WR-58 and the scale", r->vector("s"), Vec3{7, 8, 9});
      delete r;
    }
    if (Runner *r = setup("matrix m = combine_transform({0,0,0}, "
                          "axis_angle_rotation({0,0,1}, radians(90)), {1,1,1}); "
                          "vector t; quat q; vector s; "
                          "separate_transform(m, &t, &q, &s); v@a = rotate_vector({1,0,0}, q);")) {
      check_vec("WR-58 the rotation comes back as a rotation", r->vector("a"), Vec3{0, 1, 0}, 1e-5);
      delete r;
    }

    /* The refusals. A wrong shape is a compile error here, never a silent 0 later. */
    check_true("WR-47 arithmetic on a rotation is a compile error",
               first_error("quat q = quaternion(1,0,0,0); f@a = q + 1;")
                   .find("arithmetic") != std::string::npos);
    check_true("WR-47 a rotation cannot be converted to a float",
               !first_error("f@a = float(quaternion(1,0,0,0));").empty());
    check_true("WR-43 an unknown space word is rejected",
               first_error("quat a = quaternion(1,0,0,0); quat b = a; "
                           "quat c = rotate_rotation(\"world\", a, b);")
                   .find("space") != std::string::npos);
    check_true("WR-43 the space word must be a literal",
               !first_error("quat a = quaternion(1,0,0,0); quat b = a; "
                            "quat c = rotate_rotation(0, a, b);")
                    .empty());
    check_true("WR-52 an unknown axis word is rejected",
               first_error("quat q = align_rotation_to_vector(\"up\", \"auto\", "
                           "quaternion(1,0,0,0), 1.0, {0,0,1});")
                   .find("axis") != std::string::npos);
    check_true("WR-49 a missing & is rejected",
               !first_error("quat q = quaternion(1,0,0,0); vector a; float b; "
                            "to_axis_angle(q, a, b);")
                    .empty());
    check_true("WR-49 an out parameter of the wrong type is rejected",
               !first_error("quat q = quaternion(1,0,0,0); vector a; vector b; "
                            "to_axis_angle(q, &a, &b);")
                    .empty());
    check_true("WR-53 a matrix cannot be given where a rotation is wanted",
               !first_error("matrix m = combine_transform({0,0,0}, quaternion(1,0,0,0), {1,1,1}); "
                            "v@a = rotate_vector({1,0,0}, m);")
                    .empty());
    check_true("WR-53 a matrix has no named components",
               !first_error("matrix m; f@a = m.x;").empty());
    check_true("WR-53 a matrix index must be a constant",
               !first_error("matrix m; float i = 1; f@a = m[i][0];").empty());
    check_true("WR-44 the functions reject a missing argument",
               !first_error("v@a = rotate_vector({1,0,0});").empty());
    check_true("WR-53 a matrix index past the last cell reads 0",
               first_error("matrix m; f@a = m[3][2];").empty());
  }

  /* ------------------------------------------------- WR-41: the line-number gutter's rule */
  {
    /* `line_number_rows` gets the byte offsets the text wrap started each row at (a wrapped row
     * starts right where the previous one stopped, a logical break is a row whose preceding byte is
     * the newline). Row offsets here are hand-made, which is the point: the rule is checkable
     * without a screen. */
    const std::string three = "a\nbb\nc\n";
    /* The wrap drops the newline from a row and appends an empty row for the trailing one. */
    const std::vector<int> rows_of_three = {0, 2, 5, 7};
    const std::vector<int> numbers_of_three = line_number_rows(three, rows_of_three);
    check_true("WR-41 every logical row of a three-line program gets its own number",
               numbers_of_three == std::vector<int>{1, 2, 3, 4});
    check_true("WR-41 and the digits follow the line count",
               line_number_digits(three) == 1 && line_number_at(three, 0) == 1 &&
                   line_number_at(three, 2) == 2 && line_number_at(three, 5) == 3 &&
                   line_number_at(three, 7) == 4);

    const std::string wrapped = "one long line that wraps\nsecond\n";
    /* Rows: "one long line " (0), "that wraps" (14, a wrapped continuation), "second" (25), "" (32). */
    const std::vector<int> rows_of_wrapped = {0, 14, 25, 32};
    const std::vector<int> numbers_of_wrapped = line_number_rows(wrapped, rows_of_wrapped);
    check_true("WR-41 a wrapped continuation carries no number of its own",
               numbers_of_wrapped == std::vector<int>{1, 0, 2, 3});

    /* Digits grow with the count (9 -> 10 must widen the column), and an empty program still has
     * one row: `integer_digits_i(0)` is not defined, so this must not be computed from 0. */
    std::string ten_lines;
    for (int i = 0; i < 9; i++) {
      ten_lines += "x\n";
    }
    check_true("WR-41 ten logical lines need two digits",
               line_number_digits(ten_lines) == 2 && line_number_at(ten_lines, 18) == 10);
    check_true("WR-41 an empty program needs one digit",
               line_number_digits("") == 1 && line_number_at("", 0) == 1);
    const std::vector<int> one_row = line_number_rows("", {0});
    check_true("WR-41 and its single row is line 1", one_row == std::vector<int>{1});

    /* Out-of-range offsets are clamped rather than read past the end (the wrap can hand the offset
     * just past the last byte). */
    const std::vector<int> clamped = line_number_rows(three, {0, 99});
    check_true("WR-41 a row offset past the end is clamped",
               clamped == std::vector<int>{1, 4});
  }

  std::printf("== wrangle_engine_selftest: %d checks, %d failed ==\n", g_checks, g_failures);
  return g_failures == 0 ? 0 : 1;
}
