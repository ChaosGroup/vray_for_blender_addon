// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include "zmq_message.hpp"
#include "zmq_import_message.hpp"   // ImportedPluginData - reused by MsgScatterPresetResult

/// Messages for the Chaos Scatter preview protocol, exchanged over a dedicated
/// ExporterType::SCATTER_PREVIEW connection.
///
/// Scene data travels over the STANDARD generic plugin messages (MsgPluginCreate /
/// MsgPluginUpdate / MsgPluginRemove with AttrValue payloads) - the scatter preview controller
/// handles that subset against its private headless renderer, so meshes (GeomStaticMesh),
/// density maps (RawBitmapBuffer/TexBitmap), target/model Nodes and the GeomScatter plugin
/// itself are built with the exact same wire format and value conversions as the render
/// export. Content caching = plugin names are client-side content hashes; unchanged plugins
/// are simply not re-sent.
///
/// Only the compute trigger and its reply are scatter-specific:
///   client -> MsgScatterPreviewRequest   (run readScatterData on a GeomScatter plugin)
///   server -> MsgScatterPreviewResult    (transforms + topo)
///
/// A pending (not yet started) request is replaced by a newer one - latest wins; results carry
/// the requestId so the client can drop stale replies. MsgScatterPreviewCancel drops a queued
/// request; a running readScatterData is not interruptible.
///
/// The same connection also reads Chaos Scatter presets (.mbc), which is the one other thing only
/// the AppSDK can do with a GeomScatter:
///   client -> MsgScatterPresetRequest    (run readScatterPreset on a .mbc)
///   server -> MsgScatterPresetResult     (the filled plugin's parameters)
/// Here the plugin is built server-side, so the result travels the opposite way to the preview's
/// scene data - as ImportedPluginData records, the same shape a .vrscene import produces.

#pragma warning (push)
#pragma warning (disable: 4505) // Unreferenced function has been removed

namespace VrayZmqWrapper {

/// Final status of a preview request, carried by MsgScatterPreviewResult.
enum class ScatterPreviewStatus : int {
	Ok = 0,
	Error,
	Cancelled
};


/// MsgScatterPreviewRequest - compute the preview transforms of one GeomScatter plugin
/// previously built on this connection via the generic plugin messages.
PROTO_MESSAGE(ScatterPreviewRequest,
	int requestId = 0;              ///< Monotonically increasing per client.
	std::string scatterPluginName;  ///< The GeomScatter plugin to sample.
	double time = 0.0;              ///< Scene frame (renderer current time for the sampling).
);

SERIALIZE_MESSAGE(ScatterPreviewRequest,
	PARAM(requestId)
	PARAM(scatterPluginName)
	PARAM(time)
);


/// MsgScatterPreviewCancel - drop the queued request with this id (a running computation
/// finishes but its result is discarded client-side by requestId comparison).
PROTO_MESSAGE(ScatterPreviewCancel,
	int requestId = 0;
);

SERIALIZE_MESSAGE(ScatterPreviewCancel,
	PARAM(requestId)
);


/// MsgScatterPreviewResult - the reply to a MsgScatterPreviewRequest.
/// Extension point (protocol bump): per-instance uint64 ids for the instance-editing milestone.
PROTO_MESSAGE(ScatterPreviewResult,
	int requestId = 0;
	int status = 0;                 ///< ScatterPreviewStatus
	std::string errorText;
	vray::AttrValue transforms;     ///< AttrListTransform, one per instance
	vray::AttrValue topo;           ///< AttrListInt, model index per instance
	int instanceCount = 0;
	int elapsedMs = 0;
);

SERIALIZE_MESSAGE(ScatterPreviewResult,
	PARAM(requestId)
	PARAM(status)
	PARAM(errorText)
	PARAM(transforms)
	PARAM(topo)
	PARAM(instanceCount)
	PARAM(elapsedMs)
);


/// MsgScatterPresetRequest - read a Chaos Scatter preset config (.mbc) into a fresh GeomScatter
/// plugin and reply with its parameters. Nothing of the preset is kept server-side afterwards.
PROTO_MESSAGE(ScatterPresetRequest,
	int requestId = 0;              ///< Monotonically increasing per client.
	std::string filePath;           ///< The preset config (.mbc), as downloaded by Cosmos.
	double unitRescale = 1.0;       ///< Preset units -> scene units, i.e. 1 / SettingsUnitsInfo::meters_scale.
);

SERIALIZE_MESSAGE(ScatterPresetRequest,
	PARAM(requestId)
	PARAM(filePath)
	PARAM(unitRescale)
);


/// MsgScatterPresetResult - the reply to a MsgScatterPresetRequest.
///
/// 'plugins' holds the filled GeomScatter plus one Node per model the preset references, in
/// ImportedPluginData form. The Nodes are placeholders that only carry the import position the
/// preset asks for; they exist because the scatter core writes models / model_frequencies /
/// model_parents only for links whose import callback returned a plugin.
///
/// 'modelAssetIds' is parallel to those Nodes in creation order. The asset ids cannot be part of
/// the plugin names: names allow only A-Z a-z 0-9 @ | _ and a Cosmos asset id is a UUID.
PROTO_MESSAGE(ScatterPresetResult,
	int requestId = 0;
	int status = 0;                 ///< ScatterPreviewStatus
	std::string errorText;
	std::vector<ImportedPluginData> plugins;
	vray::AttrListString modelAssetIds;
);

SERIALIZE_MESSAGE(ScatterPresetResult,
	PARAM(requestId)
	PARAM(status)
	PARAM(errorText)
	PARAM(plugins)
	PARAM(modelAssetIds)
);

};  // end VrayZmqWrapper namespace

#pragma warning (pop)
