/* SPDX-FileCopyrightText: 2023 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */
#pragma once

#include <stdbool.h>
#include <stddef.h>

namespace blender {

/** \file
 * \ingroup bke
 */

/**
 * The lines below use regex from scripts to extract their values,
 * Keep this in mind when modifying this file and keep this comment above the defines.
 *
 * \note Use #STRINGIFY() rather than defining with quotes.
 */

/** Blender major and minor version. */
#define BLENDER_VERSION 502
/** Blender patch version for bug-fix releases. */
#define BLENDER_VERSION_PATCH 3
/** Blender release cycle stage: alpha/beta/rc/release. */
#define BLENDER_VERSION_CYCLE rc
/** Blender release type suffix. LTS or blank. */
#define BLENDER_VERSION_SUFFIX LTS

/* Blender file format version. */
#define BLENDER_FILE_VERSION BLENDER_VERSION
/* PDT fork: 45 -> 46 for the `PDT Wrangle` channel storage (`versioning_520.cc`, 502/46). */
#define BLENDER_FILE_SUBVERSION 46

/**
 * PDT fork: the brand of this build = the family name plus the release version.
 *
 * - #BLENDER_BRAND_FAMILY identifies "a build of this fork". Files are compared against
 *   it by *prefix*, so a file written by `v0.1.0` opens in a `v0.2.0` build without the
 *   foreign-build warning - while an official or third-party build still warns
 *   (`wm_files.cc`, `bpy.app.file_build_hash`).
 * - #BLENDER_BRAND_ID is the full marker: appended to the window title / splash /
 *   status-bar version string (#BKE_blender_version_string_branded, also
 *   `bpy.app.version_string_branded`) and written into `FileGlobal.build_hash` of every
 *   saved .blend (`writefile.cc`).
 *
 * The version is a compile-time constant on purpose: the published "changes only"
 * repository has no git history or tags, so it cannot come from `git describe` at build
 * time. `tools\pdt\version.ps1 -Action stamp -Tag v0.1.0` rewrites
 * #BLENDER_BRAND_VERSION; `-Action verify` checks it still equals the release tag.
 *
 * Keep it ASCII and at most 15 bytes: `FileGlobal.build_hash` is `char[16]`, so the
 * family (6) + space (1) leaves at most 8 characters for the version.
 */
#define BLENDER_BRAND_FAMILY "PDT-GN"
#define BLENDER_BRAND_VERSION "v0.1.0"
#define BLENDER_BRAND_ID BLENDER_BRAND_FAMILY " " BLENDER_BRAND_VERSION

/* Minimum Blender version that supports reading file written with the current
 * version. Older Blender versions will test this and cancel loading the file, showing a warning to
 * the user.
 *
 * See
 * https://developer.blender.org/docs/handbook/guidelines/compatibility_handling_for_blend_files/
 * for details. */
#define BLENDER_FILE_MIN_VERSION 405
#define BLENDER_FILE_MIN_SUBVERSION 85

/** User readable version string. */
const char *BKE_blender_version_string();

/**
 * As above, with the build brand (#BLENDER_BRAND_ID) appended in parentheses.
 * Only user interface should use this: the plain #BKE_blender_version_string is what gets
 * written into exported file headers (OBJ/PLY/USD/...), so it must stay upstream-compatible.
 */
const char *BKE_blender_version_string_branded();

/** As above but does not show patch version. */
const char *BKE_blender_version_string_compact();

/** Returns true when version cycle is alpha, otherwise (beta, rc) returns false. */
bool BKE_blender_version_is_alpha();

/** Returns true when version suffix is LTS, otherwise returns false. */
bool BKE_blender_version_is_lts();

/**
 * Fill in given string buffer with user-readable formatted file version and subversion (if
 * provided).
 *
 * \param str_buff: a char buffer where the formatted string is written,
 * minimal recommended size is 8, or 16 if subversion is provided.
 *
 * \param file_subversion: the file subversion, if given value < 0, it is ignored, and only the
 * `file_version` is used.
 */
void BKE_blender_version_blendfile_string_from_values(char *str_buff,
                                                      const size_t str_buff_maxncpy,
                                                      const short file_version,
                                                      const short file_subversion);

}  // namespace blender
