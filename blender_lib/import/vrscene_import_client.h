// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <atomic>
#include <memory>
#include <mutex>
#include <vector>

#include <nanobind/nanobind.h>

#include "zmq_agent.h"
#include "zmq_import_message.hpp"


namespace VRayForBlender {
namespace SceneImport {

namespace nb = nanobind;
namespace proto = VrayZmqWrapper;

/// Client side of one .vrscene import session: opens a SCENE_IMPORT connection, sends
/// the request and collects the streamed plugin data (protocol: zmq_import_message.hpp).
/// Progress/completion arrive through the registered Python callbacks; the data is only
/// read after the finished callback.
class VrsceneImportClient {
public:
	VrsceneImportClient(int importId, proto::MsgImportVrsceneRequest request);
	~VrsceneImportClient();

	VrsceneImportClient(const VrsceneImportClient&) = delete;
	VrsceneImportClient& operator=(const VrsceneImportClient&) = delete;

	/// Connect to the server and send the import request.
	void start();

	/// Ask the server to abort the import.
	void cancel();

	/// Build the Python representation of the imported scene:
	/// a list of (pluginName, pluginType, attributes dict, animated param names) tuples.
	/// @param largeAttrs - {pluginType: [attrName, ...]} attrs to expose as numpy arrays.
	nb::list getImportedScene(const nb::dict& largeAttrs);

private:
	void handleMsg(const zmq::message_t& msg);
	void finish(const proto::MsgImportOnResult& result);

private:
	const int m_importId;
	proto::MsgImportVrsceneRequest m_request;
	std::unique_ptr<proto::ZmqAgent> m_conn;

	std::mutex m_lock;   ///< Guards m_plugins (written on the poller thread, read from Python)
	std::vector<proto::ImportedPluginData> m_plugins;
	std::atomic_bool m_completed{false};
};


// Module-level session registry, driving the Python API surface.

/// Start a new import session. Returns its importId.
int startImport(const std::string& filePath, bool skipDefaults, double frameStart, double frameEnd, const std::string& typeFilter);

/// Ask the server to abort the given import session.
void cancelImport(int importId);

/// Get the imported scene data. Valid after the finished callback has reported success.
nb::list getImportedScene(int importId, const nb::dict& largeAttrs);

/// Free the session and its native buffers. Numpy arrays already handed out to Python
/// stay valid (their capsules share ownership of the underlying buffers).
void releaseImport(int importId);

/// Free all sessions (module shutdown).
void releaseAllImports();

} // namespace SceneImport
} // namespace VRayForBlender
