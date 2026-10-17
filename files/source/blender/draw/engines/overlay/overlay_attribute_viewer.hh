/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup overlay
 */

#pragma once

#include "BKE_attribute.hh"
#include "BKE_curves.hh"
#include "BKE_geometry_set.hh"
#include "DNA_curve_types.h"
#include "DNA_pointcloud_types.h"

#include "draw_cache.hh"
#include "draw_cache_impl.hh"
#include "draw_manager_text.hh"
#include "overlay_base.hh"

namespace blender::draw::overlay {

/**
 * EVO-6: maximum number of elements drawn as direction lines / axes. Above this the overlay draws
 * nothing and reports the count, instead of building a batch that would freeze the viewport.
 */
constexpr int64_t VIEWER_OVERLAY_MAX_ELEMENTS = 1000000;

/**
 * Displays geometry node viewer output.
 * Values are displayed as vertex or face colors on top of the active object. `Float3` (vector),
 * `Quaternion` (rotation) and `Float4x4` (matrix) attributes can instead be drawn as direction
 * lines and rotation axes. See `tools/pdt/spec/viewer_overlay.md` (EVO-1..EVO-6).
 */
class AttributeViewer : Overlay {
 private:
  PassMain ps_ = {"attribute_viewer_ps_"};

  PassMain::Sub *mesh_sub_ = nullptr;
  PassMain::Sub *pointcloud_sub_ = nullptr;
  PassMain::Sub *curve_sub_ = nullptr;
  PassMain::Sub *curves_sub_ = nullptr;
  PassMain::Sub *instance_sub_ = nullptr;
  /** EVO-1..EVO-5: direction lines and rotation axes. */
  PassMain::Sub *line_sub_ = nullptr;

  /** EVO-2: the viewer attribute is drawn as lines. */
  bool show_vector_ = false;

  /**
   * Batches built while syncing. The recorded draw commands only hold pointers to them, so they
   * have to stay alive until the view has been submitted (see #draw_line).
   */
  Vector<gpu::Batch *> line_batches_;

  /* Matches the vertex format expected by the `overlay_uniform_color` shader. */
  struct LineVertex {
    float3 pos;
    GPU_VERTEX_FORMAT_FUNC(LineVertex, pos);
  };

 public:
  void begin_sync(Resources &res, const State &state) final
  {
    this->release_line_batches();

    ps_.init();
    const bool is_view3d = state.is_space_v3d() && !res.is_selection();
    enabled_ = is_view3d && state.show_attribute_viewer();
    show_vector_ = is_view3d && state.show_attribute_viewer_vector();
    if (!enabled_ && !show_vector_) {
      return;
    }

    ps_.bind_ubo(OVERLAY_GLOBALS_SLOT, &res.globals_buf);
    ps_.bind_ubo(DRW_CLIPPING_UBO_SLOT, &res.clip_planes_buf);
    ps_.state_set(DRW_STATE_WRITE_COLOR | DRW_STATE_DEPTH_LESS_EQUAL | DRW_STATE_BLEND_ALPHA,
                  state.clipping_plane_count);

    auto create_sub = [&](const char *name, gpu::Shader *shader) {
      auto &sub = ps_.sub(name);
      sub.shader_set(shader);
      return &sub;
    };

    if (enabled_) {
      mesh_sub_ = create_sub("mesh", res.shaders->attribute_viewer_mesh.get());
      pointcloud_sub_ = create_sub("pointcloud", res.shaders->attribute_viewer_pointcloud.get());
      curve_sub_ = create_sub("curve", res.shaders->attribute_viewer_curve.get());
      curves_sub_ = create_sub("curves", res.shaders->attribute_viewer_curves.get());
      instance_sub_ = create_sub("instance", res.shaders->uniform_color.get());
    }
    if (show_vector_) {
      line_sub_ = create_sub("line", res.shaders->uniform_color.get());
      /* Guides are meant to be read, so they are not occluded by the geometry. */
      line_sub_->state_set(DRW_STATE_WRITE_COLOR | DRW_STATE_DEPTH_ALWAYS | DRW_STATE_BLEND_ALPHA,
                           state.clipping_plane_count);
    }
  }

  void object_sync(Manager &manager,
                   const ObjectRef &ob_ref,
                   Resources & /*res*/,
                   const State &state) final
  {
    if (!enabled_ && !show_vector_) {
      return;
    }

    const bool is_preview = ob_ref.preview_base_geometry() != nullptr;
    if (!is_preview) {
      return;
    }

    if (ob_ref.preview_instance_index() >= 0) {
      const auto &instances =
          *ob_ref.preview_base_geometry()->get_component<bke::InstancesComponent>();
      const bke::AttributeAccessor instance_attributes = *instances.attributes();
      const int index = ob_ref.preview_instance_index();
      /* EVO-1: the attribute of the previewed instance is a single value, drawn at the object
       * origin - the same element the text overlay reports. */
      const Span<float4x4> instance_transforms = instances.get() ? instances.get()->transforms() :
                                                                   Span<float4x4>();
      if (this->try_draw_lines(instance_attributes, ob_ref, state, manager, index,
                               instance_transforms))
      {
        return;
      }
      if (const std::optional<bke::AttributeMetaData> meta_data =
              instance_attributes.lookup_meta_data(".viewer"))
      {
        if (attribute_type_supports_viewer_overlay(meta_data->data_type)) {
          populate_for_instance(ob_ref, state, manager);
          return;
        }
      }
    }
    populate_for_geometry(ob_ref, state, manager);
  }

  void pre_draw(Manager &manager, View &view) final
  {
    if (!enabled_ && !show_vector_) {
      return;
    }

    manager.generate_commands(ps_, view);
  }

  void draw_line(Framebuffer &framebuffer, Manager &manager, View &view) final
  {
    if (!enabled_ && !show_vector_) {
      return;
    }

    GPU_framebuffer_bind(framebuffer);
    manager.submit_only(ps_, view);
    /* The draw commands have been issued, the line geometry is no longer needed. */
    this->release_line_batches();
  }

 private:
  void release_line_batches()
  {
    for (gpu::Batch *batch : this->line_batches_) {
      GPU_BATCH_DISCARD_SAFE(batch);
    }
    this->line_batches_.clear();
  }

  /**
   * EVO-6: report why nothing is drawn instead of leaving the viewport silently empty.
   */
  static void add_budget_note(const State &state, const ObjectRef &ob_ref, const int64_t count)
  {
    if (state.dt == nullptr) {
      return;
    }
    const std::string text = std::string("Vector overlay: ") + std::to_string(count) +
                             " elements (limit " +
                             std::to_string(VIEWER_OVERLAY_MAX_ELEMENTS) + "), not drawn";
    const float3 position = math::transform_point(ob_ref.object->object_to_world(), float3(0.0f));
    uchar color[4];
    ui::theme::get_color_4ubv(TH_TEXT_HI, color);
    DRW_text_cache_add(state.dt,
                       position,
                       text.data(),
                       text.size(),
                       0,
                       0,
                       DRW_TEXT_CACHE_GLOBALSPACE,
                       color,
                       true,
                       true);
  }

  void draw_lines(const Span<float3> verts,
                  const float4 &color,
                  Manager &manager,
                  const ObjectRef &ob_ref)
  {
    if (verts.is_empty() || line_sub_ == nullptr) {
      return;
    }

    gpu::VertBuf *vbo = GPU_vertbuf_create_with_format(LineVertex::format());
    GPU_vertbuf_data_alloc(*vbo, verts.size());
    vbo->data<float3>().copy_from(verts);
    gpu::Batch *batch = GPU_batch_create_ex(GPU_PRIM_LINES, vbo, nullptr, GPU_BATCH_OWNS_VBO);
    this->line_batches_.append(batch);

    auto &sub = *line_sub_;
    sub.push_constant("ucolor", color);
    sub.draw(batch, manager.unique_handle(ob_ref));
  }

  static bool is_line_attribute_type(const CPPType &type)
  {
    return type.is<float3>() || type.is<math::Quaternion>() || type.is<float4x4>();
  }

  /**
   * Matrix that brings the *directions* of an Instance-domain value into the space the lines are
   * drawn in (the preview object's local space).
   *
   * A preview instance's object matrix is `base_object * instance_transform` while the attribute
   * lives in the base object's space, so drawing the value with that matrix applies the instance
   * transform a second time. Reported with `test_matrix.blend` (2026-09-16): viewing
   * `instance_transform` while changing `Instance on Points`' Rotation Z, the tripod turned
   * exactly twice as fast as the instances. With this compensation the drawn axes are the value's
   * orientation in the base object's space - which is what the instances show.
   *
   * The inverse of `R * S` is `S^-1 * R^T`; an axis with a zero scale cannot be undone, so it is
   * mapped to zero (the other two axes stay right for a flat instance, and EVO-7 already drops a
   * zero scale axis of the value itself).
   */
  static float3x3 to_local_directions(const float4x4 &instance_transform)
  {
    float3 location;
    float3 scale;
    math::EulerXYZ rotation;
    math::to_loc_rot_scale_safe<true>(instance_transform, location, rotation, scale);
    const float3x3 rotation_inverse = math::transpose(math::from_rotation<float3x3>(rotation));
    float3x3 scale_inverse = float3x3::identity();
    for (const int axis : IndexRange(3)) {
      scale_inverse[axis][axis] = (math::abs(scale[axis]) > 1.0e-8f) ? 1.0f / scale[axis] : 0.0f;
    }
    return scale_inverse * rotation_inverse;
  }

  void append_vector_line(Vector<float3> &r_verts,
                          const float3 &position,
                          const float3 &value,
                          const float3x3 &to_local,
                          const State &state) const
  {
    const float scale = state.overlay.viewer_vector_scale;
    float3 direction = value;
    if (state.overlay.viewer_vector_mode == V3D_VIEWER_VECTOR_MODE_DIRECTION) {
      const float length = math::length(direction);
      if (length < 1.0e-8f) {
        /* EVO-7: a degenerate value has no direction to show. */
        return;
      }
      direction /= length;
    }
    /* EVO-7: draw nothing rather than a zero length segment. Rasterizing a segment whose two
     * endpoints coincide is driver dependent, so it is filtered out here. */
    if (math::length(direction) * scale < 1.0e-8f) {
      return;
    }
    direction = to_local * direction;
    r_verts.append(position);
    r_verts.append(position + direction * scale);
  }

  void append_axes(std::array<Vector<float3>, 3> &r_verts,
                   const float3 &position,
                   const float3x3 &rotation,
                   const float3 &axis_scale,
                   const float3x3 &to_local,
                   const State &state) const
  {
    const float scale = state.overlay.viewer_axes_scale;
    const std::array<float3, 3> directions = {to_local * rotation.x_axis(),
                                              to_local * rotation.y_axis(),
                                              to_local * rotation.z_axis()};
    const std::array<float, 3> lengths = {math::abs(axis_scale.x) * scale,
                                         math::abs(axis_scale.y) * scale,
                                         math::abs(axis_scale.z) * scale};
    for (const int axis : IndexRange(3)) {
      if (lengths[axis] < 1.0e-8f) {
        continue;
      }
      r_verts[axis].append(position);
      r_verts[axis].append(position + directions[axis] * lengths[axis]);
    }
  }

  void append_quaternion_axes(std::array<Vector<float3>, 3> &r_verts,
                              const float3 &position,
                              const math::Quaternion &value,
                              const float3x3 &to_local,
                              const State &state) const
  {
    const float3x3 rotation = math::from_rotation<float3x3>(math::normalize(value));
    this->append_axes(r_verts, position, rotation, float3(1.0f), to_local, state);
  }

  void append_matrix_axes(std::array<Vector<float3>, 3> &r_verts,
                          const float3 &position,
                          const float4x4 &value,
                          const float3x3 &to_local,
                          const State &state) const
  {
    /* EVO-5: the axes come from the loc/rot/scale decomposition, so they stay orthogonal even for
     * skewed matrices - the same decomposition the text overlay shows. */
    float3 location;
    float3 scale;
    math::EulerXYZ rotation;
    math::to_loc_rot_scale_safe<true>(value, location, rotation, scale);
    this->append_axes(
        r_verts, position, math::from_rotation<float3x3>(rotation), scale, to_local, state);
  }

  void draw_axes(const std::array<Vector<float3>, 3> &axis_verts,
                 const State &state,
                 Manager &manager,
                 const ObjectRef &ob_ref)
  {
    const float opacity = state.overlay.viewer_attribute_opacity;
    for (const int axis : IndexRange(3)) {
      if (axis_verts[axis].is_empty()) {
        continue;
      }
      float color[4];
      ui::theme::get_color_3fv(TH_AXIS_X + axis, color);
      color[3] = opacity;
      this->draw_lines(axis_verts[axis], float4(color), manager, ob_ref);
    }
  }

  /**
   * Draws the `.viewer` attribute as direction lines (`EVO-3`), rotation axes (`EVO-4`) or matrix
   * axes (`EVO-5`). `instance_index` is set when the viewer displays the `Instance` domain: then
   * only that single element is drawn, at the object origin, and its directions are converted into
   * the preview object's local space (see #to_local_directions).
   */
  void populate_lines(const bke::AttributeAccessor &attributes,
                      const ObjectRef &ob_ref,
                      const State &state,
                      Manager &manager,
                      const std::optional<int> instance_index,
                      const Span<float4x4> instance_transforms)
  {
    const bke::GAttributeReader attribute = attributes.lookup(".viewer");
    if (!attribute || attribute.varray.size() == 0) {
      return;
    }

    VArraySpan<float3> positions;
    const IndexRange range = instance_index.has_value() ? IndexRange(*instance_index, 1) :
                                                          IndexRange(attribute.varray.size());
    if (!instance_index.has_value()) {
      positions = *attributes.lookup<float3>("position", attribute.domain);
      /* EVO-6: never build a line batch above the budget - the viewport would freeze. */
      if (positions.size() > VIEWER_OVERLAY_MAX_ELEMENTS) {
        this->add_budget_note(state, ob_ref, positions.size());
        return;
      }
    }

    /* An Instance-domain value is expressed in the base object's space while the lines are drawn
     * in the preview object's local space - only worked out here, so that a viewer showing a type
     * without lines (or the lines switched off) never pays for it. */
    const float3x3 to_local = (instance_index.has_value() &&
                               instance_transforms.index_range().contains(*instance_index)) ?
                                  to_local_directions(instance_transforms[*instance_index]) :
                                  float3x3::identity();

    const CPPType &type = attribute.varray.type();
    const float opacity = state.overlay.viewer_attribute_opacity;

    if (type.is<float3>()) {
      const VArray<float3> values = attribute.varray.typed<float3>();
      Vector<float3> verts;
      verts.reserve(range.size() * 2);
      for (const int index : range) {
        const float3 position = instance_index.has_value() ? float3(0.0f) : positions[index];
        this->append_vector_line(verts, position, values[index], to_local, state);
      }
      const float3 color(state.overlay.viewer_vector_color[0],
                         state.overlay.viewer_vector_color[1],
                         state.overlay.viewer_vector_color[2]);
      this->draw_lines(verts, float4(color, opacity), manager, ob_ref);
      return;
    }

    std::array<Vector<float3>, 3> axis_verts;
    if (type.is<math::Quaternion>()) {
      const VArray<math::Quaternion> values = attribute.varray.typed<math::Quaternion>();
      for (const int index : range) {
        const float3 position = instance_index.has_value() ? float3(0.0f) : positions[index];
        this->append_quaternion_axes(axis_verts, position, values[index], to_local, state);
      }
    }
    else if (type.is<float4x4>()) {
      const VArray<float4x4> values = attribute.varray.typed<float4x4>();
      for (const int index : range) {
        const float3 position = instance_index.has_value() ? float3(0.0f) : positions[index];
        this->append_matrix_axes(axis_verts, position, values[index], to_local, state);
      }
    }
    else {
      return;
    }
    this->draw_axes(axis_verts, state, manager, ob_ref);
  }

  /**
   * Draws the lines for the `.viewer` attribute if it is one of the line types and the overlay is
   * enabled. Returns true when it took over, in which case the color overlay must not draw the
   * same attribute (`EVO-3`).
   */
  bool try_draw_lines(const bke::AttributeAccessor &attributes,
                      const ObjectRef &ob_ref,
                      const State &state,
                      Manager &manager,
                      const std::optional<int> instance_index = std::nullopt,
                      const Span<float4x4> instance_transforms = {})
  {
    if (!show_vector_ || line_sub_ == nullptr) {
      return false;
    }
    const bke::GAttributeReader attribute = attributes.lookup(".viewer");
    if (!attribute || attribute.varray.size() == 0) {
      return false;
    }
    if (!is_line_attribute_type(attribute.varray.type())) {
      return false;
    }
    this->populate_lines(
        attributes, ob_ref, state, manager, instance_index, instance_transforms);
    return true;
  }

  void populate_for_instance(const ObjectRef &ob_ref, const State &state, Manager &manager)
  {
    Object &object = *ob_ref.object;
    const bke::GeometrySet &base_geometry = *ob_ref.preview_base_geometry();
    const bke::InstancesComponent &instances =
        *base_geometry.get_component<bke::InstancesComponent>();
    const bke::AttributeAccessor instance_attributes = *instances.attributes();
    const VArray attribute = *instance_attributes.lookup<ColorGeometry4f>(".viewer");
    if (!attribute) {
      return;
    }
    ColorGeometry4f color = attribute.get(ob_ref.preview_instance_index());
    color.a *= state.overlay.viewer_attribute_opacity;
    switch (object.type) {
      case OB_MESH: {
        ResourceHandleRange res_handle = manager.unique_handle(ob_ref);

        {
          gpu::Batch *batch = DRW_cache_mesh_surface_get(&object);
          auto &sub = *instance_sub_;
          sub.push_constant("ucolor", float4(color));
          sub.draw(batch, res_handle);
        }
        if (gpu::Batch *batch = DRW_cache_mesh_loose_edges_get(&object)) {
          auto &sub = *instance_sub_;
          sub.push_constant("ucolor", float4(color));
          sub.draw(batch, res_handle);
        }

        break;
      }
      case OB_POINTCLOUD: {
        auto &sub = *instance_sub_;
        gpu::Batch *batch = pointcloud_sub_pass_setup(sub, &object, nullptr);
        sub.push_constant("ucolor", float4(color));
        sub.draw(batch, manager.unique_handle(ob_ref));
        break;
      }
      case OB_CURVES_LEGACY: {
        gpu::Batch *batch = DRW_cache_curve_edge_wire_get(&object);
        auto &sub = *instance_sub_;
        sub.push_constant("ucolor", float4(color));
        ResourceHandleRange res_handle = manager.unique_handle(ob_ref);
        sub.draw(batch, res_handle);
        break;
      }
      case OB_CURVES: {
        /* Not supported yet because instances of this type are currently drawn as legacy curves.
         */
        break;
      }
      default:
        break;
    }
  }

  static bool attribute_type_supports_viewer_overlay(const bke::AttrType data_type)
  {
    return !ELEM(data_type, bke::AttrType::Quaternion, bke::AttrType::Float4x4);
  }

  void populate_for_geometry(const ObjectRef &ob_ref, const State &state, Manager &manager)
  {
    const float opacity = state.overlay.viewer_attribute_opacity;
    Object &object = *ob_ref.object;
    switch (object.type) {
      case OB_MESH: {
        Mesh &mesh = DRW_object_get_data_for_drawing<Mesh>(object);
        const bke::AttributeAccessor attributes = mesh.attributes();
        if (this->try_draw_lines(attributes, ob_ref, state, manager)) {
          break;
        }
        if (const std::optional<bke::AttributeMetaData> meta_data =
                attributes.lookup_meta_data(".viewer"))
        {
          if (attribute_type_supports_viewer_overlay(meta_data->data_type)) {
            gpu::Batch *batch = DRW_cache_mesh_surface_viewer_attribute_get(&object);
            auto &sub = *mesh_sub_;
            sub.push_constant("opacity", opacity);
            sub.draw(batch, manager.unique_handle(ob_ref));
          }
        }
        break;
      }
      case OB_POINTCLOUD: {
        PointCloud &pointcloud = DRW_object_get_data_for_drawing<PointCloud>(object);
        const bke::AttributeAccessor attributes = pointcloud.attributes();
        if (this->try_draw_lines(attributes, ob_ref, state, manager)) {
          break;
        }
        if (const std::optional<bke::AttributeMetaData> meta_data =
                attributes.lookup_meta_data(".viewer"))
        {
          if (attribute_type_supports_viewer_overlay(meta_data->data_type)) {
            gpu::VertBuf **vertbuf = DRW_pointcloud_evaluated_attribute(&pointcloud, ".viewer");
            /* Avoid trying to bind an empty `vertbuf` which causes assert / undefined behavior. */
            if (pointcloud.totpoint > 0 && vertbuf != nullptr) {
              auto &sub = *pointcloud_sub_;
              gpu::Batch *batch = pointcloud_sub_pass_setup(sub, &object, nullptr);
              sub.push_constant("opacity", opacity);
              sub.bind_texture("attribute_tx", vertbuf);
              sub.draw(batch, manager.unique_handle(ob_ref));
            }
          }
        }
        break;
      }
      case OB_CURVES_LEGACY: {
        Curve &curve = DRW_object_get_data_for_drawing<Curve>(object);
        if (curve.curve_eval) {
          const bke::CurvesGeometry &curves = curve.curve_eval->geometry.wrap();
          const bke::AttributeAccessor attributes = curves.attributes();
          if (this->try_draw_lines(attributes, ob_ref, state, manager)) {
            break;
          }
          if (const std::optional<bke::AttributeMetaData> meta_data =
                  attributes.lookup_meta_data(".viewer"))
          {
            if (attribute_type_supports_viewer_overlay(meta_data->data_type)) {
              gpu::Batch *batch = DRW_cache_curve_edge_wire_viewer_attribute_get(&object);
              auto &sub = *curve_sub_;
              sub.push_constant("opacity", opacity);
              ResourceHandleRange res_handle = manager.unique_handle(ob_ref);
              sub.draw(batch, res_handle);
            }
          }
        }
        break;
      }
      case OB_CURVES: {
        blender::Curves &curves_id = DRW_object_get_data_for_drawing<blender::Curves>(object);
        const bke::CurvesGeometry &curves = curves_id.geometry.wrap();
        const bke::AttributeAccessor attributes = curves.attributes();
        if (this->try_draw_lines(attributes, ob_ref, state, manager)) {
          break;
        }
        if (const std::optional<bke::AttributeMetaData> meta_data =
                attributes.lookup_meta_data(".viewer"))
        {
          if (attribute_type_supports_viewer_overlay(meta_data->data_type)) {
            bool is_point_domain;
            bool is_valid;
            gpu::VertBufPtr &texture = DRW_curves_texture_for_evaluated_attribute(
                &curves_id, ".viewer", is_point_domain, is_valid);
            if (is_valid) {
              auto &sub = *curves_sub_;
              const char *error = nullptr;
              /* The error string will always have been printed by the engine already.
               * No need to display it twice. */
              gpu::Batch *batch = curves_sub_pass_setup(sub, state.scene, ob_ref.object, error);
              sub.push_constant("opacity", opacity);
              sub.push_constant("is_point_domain", is_point_domain);
              sub.bind_texture("color_tx", texture);
              sub.draw(batch, manager.unique_handle(ob_ref));
            }
          }
        }
        break;
      }
      default:
        break;
    }
  }
};
}  // namespace blender::draw::overlay
