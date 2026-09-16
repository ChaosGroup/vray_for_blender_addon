// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <nanobind/nanobind.h>

#include "zmq_scatter_message.hpp"


namespace VRayForBlender {
namespace ScatterPreview {

namespace nb = nanobind;
namespace proto = VrayZmqWrapper;

/// Store a preview result (called from the exporter's message thread) and notify Python via the
/// "scatterPreviewResult" callback with (requestId, status, errorText, instanceCount). The heavy
/// transform/topo arrays are fetched lazily by getResult on the main thread.
void onResult(const proto::MsgScatterPreviewResult& result);

/// Fetch a completed result as (transforms (N,12) float32, topo (N,) int32) zero-copy ndarrays.
/// Returns (None, None) if the requestId is unknown.
nb::tuple getResult(int requestId);

/// Drop a stored result. Arrays already handed to Python via getResult stay valid (capsule owned).
void releaseResult(int requestId);

/// Store a preset-read result and notify Python via the "scatterPresetResult" callback with
/// (requestId, status, errorText). The parameters are fetched by getPresetResult.
void onPresetResult(const proto::MsgScatterPresetResult& result);

/// Fetch a completed preset read as ([(pluginName, pluginType, attrs), ...], [assetId, ...]).
/// The records are the filled GeomScatter followed by one placeholder Node per referenced model in
/// link order; the asset ids are parallel to those Nodes. (None, None) if the id is unknown.
nb::tuple getPresetResult(int requestId);

/// Drop a stored preset result.
void releasePresetResult(int requestId);

/// Drop all stored results (server restart / shutdown).
void clear();

} // namespace ScatterPreview
} // namespace VRayForBlender
