// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "vrscene_import_client.h"

#include <limits>
#include <random>
#include <set>
#include <string>

#include <nanobind/stl/string.h>
#include <tsl/robin_map.h>

#include "api/interop/import_conversion.h"
#include "api/interop/types.h"
#include "api/interop/utils.hpp"
#include "export/zmq_server.h"
#include "utils/logger.hpp"


namespace VRayForBlender {
namespace SceneImport {

using namespace VrayZmqWrapper;

namespace {

	/// Names of the Python callbacks in the ZmqServer callback registry.
	const char* CB_PROGRESS = "vrsceneImportProgress";
	const char* CB_FINISHED = "vrsceneImportFinished";

	RoutingId generateImportRoutingId() {
		std::random_device rd;
		std::mt19937_64 gen(rd());
		// Routing id 1 is reserved for the control connection.
		std::uniform_int_distribution<RoutingId> dist(2, std::numeric_limits<RoutingId>::max());
		return dist(gen);
	}

	// Session registry
	std::mutex g_sessionsLock;
	tsl::robin_map<int, std::unique_ptr<VrsceneImportClient>> g_sessions;
	int g_nextImportId = 1;

} // anonymous namespace


VrsceneImportClient::VrsceneImportClient(int importId, proto::MsgImportVrsceneRequest request)
	: m_importId(importId)
	, m_request(std::move(request))
{
}


VrsceneImportClient::~VrsceneImportClient() {
	if (m_conn) {
		m_conn->stop(true);
	}
}


void VrsceneImportClient::start() {

	m_conn = std::make_unique<ZmqAgent>(ZmqServer::get().context(), generateImportRoutingId(),
	                                    ExporterType::SCENE_IMPORT, ZmqAgent::Client);

	m_conn->setMsgCallback([this](zmq::message_t&& payload) {
		try {
			handleMsg(payload);
		}
		catch (const std::exception& exc) {
			Logger::error("Scene import message handling: %1%", exc.what());
		}
	});

	m_conn->setErrorCallback([this](const std::string& err) {
		Logger::error("Scene import connection error: %1%", err);

		// Report the failure to Python, unless the import has already completed.
		MsgImportOnResult result;
		result.status = static_cast<int>(ImportStatus::InternalError);
		result.errorText = err;
		finish(result);
	});

	m_conn->setTraceCallback([id = shorten(m_conn->routingId())](const std::string& msg) {
		Logger::debug("Import agent %1%: %2%", id, msg);
	});

	ZmqTimeouts timeouts;
	timeouts.handshake = ZmqServer::HANDSHAKE_TIMEOUT;
	timeouts.ping = NO_PING;
	timeouts.inactivity = HEARTBEAT_NO_TIMEOUT;

	m_conn->run(ZmqServer::get().getEndpoint(), timeouts);
	m_conn->send(serializeMessage(m_request));

	Logger::info("Scene import %1% started for \"%2%\"", m_importId, m_request.filePath);
}


void VrsceneImportClient::cancel() {
	if (m_conn) {
		m_conn->send(serializeMessage(MsgImportVrsceneCancel{}));
	}
}


void VrsceneImportClient::handleMsg(const zmq::message_t& msg) {
	DeserializerStream stream(reinterpret_cast<const char*>(msg.data()), msg.size());

	MsgType type = MsgType::None;
	stream >> type;

	switch (type) {
	case MsgType::ImportOnPluginData: {
		auto batch = deserializeMessage<MsgImportOnPluginData>(stream);
		{
			std::scoped_lock lock(m_lock);
			m_plugins.reserve(m_plugins.size() + batch.plugins.size());
			for (auto& plugin : batch.plugins) {
				m_plugins.push_back(std::move(plugin));
			}
		}
		// Release a send-window credit on the server.
		m_conn->send(serializeMessage(MsgImportVrsceneAck{batch.batchIndex}));
		break;
	}
	case MsgType::ImportOnProgress: {
		const auto progress = deserializeMessage<MsgImportOnProgress>(stream);
		invokePythonCallback(CB_PROGRESS, ZmqServer::get().getPythonCallback(CB_PROGRESS),
		                     m_importId, progress.pluginsDone, progress.pluginsTotal, progress.stage);
		break;
	}
	case MsgType::ImportOnResult:
		finish(deserializeMessage<MsgImportOnResult>(stream));
		break;

	default:
		Logger::error("Unsupported message type on a scene import connection: %1%", static_cast<int>(type));
	}
}


void VrsceneImportClient::finish(const proto::MsgImportOnResult& result) {
	if (m_completed.exchange(true)) {
		return;
	}

	// Tell the server the result has been received so it can reap the worker.
	// The connection is kept alive until releaseImport so the ack is flushed.
	m_conn->send(serializeMessage(MsgImportVrsceneAck{GOODBYE_BATCH_INDEX}));

	Interop::VrsceneImportResult pyResult;
	pyResult.status = result.status;
	pyResult.errorText = result.errorText;
	pyResult.errorFile = result.errorFile;
	pyResult.errorLine = result.errorLine;
	pyResult.pluginCount = result.pluginCount;
	pyResult.paramCount = result.paramCount;
	pyResult.sceneBaseDir = result.sceneBaseDir;

	invokePythonCallback(CB_FINISHED, ZmqServer::get().getPythonCallback(CB_FINISHED), m_importId, pyResult);
}


nb::list VrsceneImportClient::getImportedScene(const nb::dict& largeAttrs) {

	// {pluginType: set of attr names to expose as numpy arrays}
	tsl::robin_map<std::string, std::set<std::string>> largeByType;
	for (const auto& item : largeAttrs) {
		auto& attrNames = largeByType[nb::cast<std::string>(item.first)];
		for (nb::handle attrName : nb::cast<nb::list>(item.second)) {
			attrNames.insert(nb::cast<std::string>(attrName));
		}
	}

	std::scoped_lock lock(m_lock);

	nb::list result;

	for (const auto& plugin : m_plugins) {
		const auto itLarge = largeByType.find(plugin.pluginType);
		const std::set<std::string>* largeNames = (itLarge != largeByType.end()) ? &itLarge->second : nullptr;

		nb::dict attrs;
		nb::list animated;

		for (const auto& param : plugin.params) {
			const bool preferNdarray = largeNames && (largeNames->count(param.name) != 0);
			attrs[nb::cast(param.name)] = Interop::attrValueToPython(param.value, preferNdarray);

			if (param.flags & ImportParamAnimated) {
				animated.append(nb::cast(param.name));
			}
		}

		result.append(nb::make_tuple(plugin.pluginName, plugin.pluginType, attrs, animated));
	}

	return result;
}


int startImport(const std::string& filePath, bool skipDefaults, double frameStart, double frameEnd, const std::string& typeFilter) {

	if (!ZmqServer::get().isRunning()) {
		throw std::runtime_error("Cannot import .vrscene: V-Ray server is not running");
	}

	MsgImportVrsceneRequest request;
	request.filePath = filePath;
	request.flags = skipDefaults ? ImportRequestSkipDefaults : 0;
	request.frameStart = frameStart;
	request.frameEnd = frameEnd;
	request.typeFilter = typeFilter;

	std::scoped_lock lock(g_sessionsLock);

	const int importId = g_nextImportId++;
	auto client = std::make_unique<VrsceneImportClient>(importId, std::move(request));
	client->start();
	g_sessions[importId] = std::move(client);

	return importId;
}


/// Get a session by id or throw.
static VrsceneImportClient& getSession(int importId) {
	const auto it = g_sessions.find(importId);
	if (it == g_sessions.end()) {
		throw std::runtime_error("Unknown vrscene import session id: " + std::to_string(importId));
	}
	return *it.value();
}


void cancelImport(int importId) {
	std::scoped_lock lock(g_sessionsLock);
	getSession(importId).cancel();
}


nb::list getImportedScene(int importId, const nb::dict& largeAttrs) {
	std::scoped_lock lock(g_sessionsLock);
	return getSession(importId).getImportedScene(largeAttrs);
}


void releaseImport(int importId) {
	std::unique_ptr<VrsceneImportClient> session;
	{
		std::scoped_lock lock(g_sessionsLock);
		const auto it = g_sessions.find(importId);
		if (it == g_sessions.end()) {
			return;
		}
		session = std::move(it.value());
		g_sessions.erase(it);
	}
	// Destroyed outside the lock: stopping the connection joins the poller thread.
	session.reset();
}


void releaseAllImports() {
	tsl::robin_map<int, std::unique_ptr<VrsceneImportClient>> sessions;
	{
		std::scoped_lock lock(g_sessionsLock);
		sessions.swap(g_sessions);
	}
	sessions.clear();
}

} // namespace SceneImport
} // namespace VRayForBlender
