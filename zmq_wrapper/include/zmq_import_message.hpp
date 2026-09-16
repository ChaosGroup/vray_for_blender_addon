// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include "zmq_message.hpp"

/// Messages for the .vrscene import protocol, exchanged over a dedicated
/// ExporterType::SCENE_IMPORT connection.
///
/// Flow:
///   client -> MsgImportVrsceneRequest
///   server -> MsgImportOnProgress*                (parsing / enumeration progress)
///   server -> MsgImportOnPluginData*              (batched plugin parameter data)
///   client -> MsgImportVrsceneAck per batch       (flow-control window credit)
///   server -> MsgImportOnResult                   (always the final message)
///   client -> MsgImportVrsceneAck{GOODBYE_BATCH_INDEX}  (result received, worker may be reaped)
///
/// The client may send MsgImportVrsceneCancel at any time; the server replies
/// with MsgImportOnResult{Cancelled} as soon as it reaches a cancellation point.

#pragma warning (push)
#pragma warning (disable: 4505) // Unreferenced function has been removed

namespace VrayZmqWrapper {

/// Final status of an import session, carried by MsgImportOnResult.
enum class ImportStatus : int {
	Ok = 0,
	ParseError,     ///< VRayRenderer::load() failed; error fields hold getLastParserError() info.
	Cancelled,
	InternalError
};

/// Per-parameter flags in ImportedPluginParam.
enum ImportParamFlags : uint32_t {
	ImportParamAnimated = 1 << 0   ///< The property has keyframes; value is sampled at the plugin's `time`.
};

/// Per-plugin flags in ImportedPluginData.
enum ImportPluginFlags : uint32_t {
	ImportPluginPartialUpdate = 1 << 0   ///< Animation phase: `params` holds only animated values at `time`.
};

/// One (parameter, value) pair of an imported plugin.
struct ImportedPluginParam {
	std::string name;
	uint32_t flags = 0;      ///< ImportParamFlags
	vray::AttrValue value;
};

SERIALIZE_STRUCT(ImportedPluginParam,
	PARAM(name)
	PARAM(flags)
	PARAM(value)
);

/// All non-default parameters of a single plugin read from a .vrscene.
struct ImportedPluginData {
	std::string pluginName;
	std::string pluginType;
	double time = 0.0;       ///< Sample time of the values; 0.0 for static imports.
	uint32_t flags = 0;      ///< ImportPluginFlags
	std::vector<ImportedPluginParam> params;
};

static SerializerStream& operator&& (SerializerStream& s, const std::vector<ImportedPluginParam>& params) {
	s << static_cast<int>(params.size());
	for (const auto& param : params) {
		s && param;
	}
	return s;
}

static DeserializerStream& operator&& (DeserializerStream& s, std::vector<ImportedPluginParam>& params) {
	int count = 0;
	s >> count;
	params.resize(count);
	for (auto& param : params) {
		s && param;
	}
	return s;
}

SERIALIZE_STRUCT(ImportedPluginData,
	PARAM(pluginName)
	PARAM(pluginType)
	PARAM(time)
	PARAM(flags)
	PARAM(params)
);

static SerializerStream& operator&& (SerializerStream& s, const std::vector<ImportedPluginData>& plugins) {
	s << static_cast<int>(plugins.size());
	for (const auto& plugin : plugins) {
		s && plugin;
	}
	return s;
}

static DeserializerStream& operator&& (DeserializerStream& s, std::vector<ImportedPluginData>& plugins) {
	int count = 0;
	s >> count;
	plugins.resize(count);
	for (auto& plugin : plugins) {
		s && plugin;
	}
	return s;
}


/// Request flags in MsgImportVrsceneRequest.
enum ImportRequestFlags : uint32_t {
	ImportRequestSkipDefaults = 1 << 0   ///< Skip properties whose state is Default (recommended).
};

/// MsgImportVrsceneRequest - client asks the server to read a .vrscene and
/// stream its plugin data back. frameStart == frameEnd requests a single
/// static sample; a wider range is reserved for the animation phase.
PROTO_MESSAGE(ImportVrsceneRequest,
	std::string filePath;
	uint32_t flags = ImportRequestSkipDefaults;
	double frameStart = 0.0;
	double frameEnd = 0.0;
	double frameStep = 1.0;
	int batchSizeHint = 0;   ///< Max plugins per MsgImportOnPluginData batch; 0 = server default.
	std::string typeFilter;  ///< Comma-separated plugin-type prefixes (e.g. "Mtl"); empty = all plugins.
);

SERIALIZE_MESSAGE(ImportVrsceneRequest,
	PARAM(filePath)
	PARAM(flags)
	PARAM(frameStart)
	PARAM(frameEnd)
	PARAM(frameStep)
	PARAM(batchSizeHint)
	PARAM(typeFilter)
);


/// MsgImportVrsceneCancel - client aborts the running import.
EMPTY_PROTO_MESSAGE(ImportVrsceneCancel);

SERIALIZE_EMPTY_MESSAGE(ImportVrsceneCancel);


/// Sent as MsgImportVrsceneAck::batchIndex after the client receives MsgImportOnResult,
/// telling the server the session is over and the worker can be reaped.
static const int GOODBYE_BATCH_INDEX = -1;

/// MsgImportVrsceneAck - client confirms a MsgImportOnPluginData batch was
/// consumed, releasing one send-window credit on the server. A batchIndex of
/// GOODBYE_BATCH_INDEX instead confirms reception of the final result.
PROTO_MESSAGE(ImportVrsceneAck,
	int batchIndex = 0;
);

SERIALIZE_MESSAGE(ImportVrsceneAck,
	PARAM(batchIndex)
);


/// MsgImportOnPluginData - a batch of imported plugins.
PROTO_MESSAGE(ImportOnPluginData,
	int batchIndex = 0;
	std::vector<ImportedPluginData> plugins;
);

SERIALIZE_MESSAGE(ImportOnPluginData,
	PARAM(batchIndex)
	PARAM(plugins)
);


/// MsgImportOnProgress - import progress notification.
PROTO_MESSAGE(ImportOnProgress,
	int pluginsDone = 0;
	int pluginsTotal = 0;
	std::string stage;   ///< "parsing", "enumerating"
);

SERIALIZE_MESSAGE(ImportOnProgress,
	PARAM(pluginsDone)
	PARAM(pluginsTotal)
	PARAM(stage)
);


/// MsgImportOnResult - always the final message of an import session.
PROTO_MESSAGE(ImportOnResult,
	int status = 0;          ///< ImportStatus
	std::string errorText;   ///< ParserError::errorText or exception message.
	std::string errorFile;   ///< ParserError::fileName.
	int errorLine = 0;       ///< ParserError::fileLine.
	int pluginCount = 0;
	int paramCount = 0;
	std::string sceneBaseDir;   ///< Directory of the .vrscene, for client-side path remapping.
);

SERIALIZE_MESSAGE(ImportOnResult,
	PARAM(status)
	PARAM(errorText)
	PARAM(errorFile)
	PARAM(errorLine)
	PARAM(pluginCount)
	PARAM(paramCount)
	PARAM(sceneBaseDir)
);

};  // end VrayZmqWrapper namespace

#pragma warning (pop)
