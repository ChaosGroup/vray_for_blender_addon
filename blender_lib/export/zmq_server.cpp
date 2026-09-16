// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "zmq_server.h"
#include "vassert.h"
#include "utils/logger.hpp"

#include <filesystem>
#include <optional>

#ifdef _WIN32
#include <windows.h>
#else
#include <cstdlib>
#endif

#include <ipc.h>
#include <seh_guard.h>
#include <zmq_common.hpp>
#include <zmq_message.hpp>

#include <boost/process/child.hpp>
#include <boost/process/start_dir.hpp>

#include "api/interop/conversion.hpp"

namespace fs = std::filesystem;
namespace bp = boost::process;


using namespace std::chrono_literals;
using namespace VRayForBlender;
using namespace VrayZmqWrapper;
using namespace VRayBaseTypes;

#define ZMQ_SERVER_ALLOW_ATTACH 0

/// Reported to the Python abort callback when the client side failed rather than the
/// ZmqServer process exiting. Must match the string tested in zmq_process.py.
static const char* CLIENT_COMM_ERROR = "Client communication error";

namespace {

/// Sets the variables ZmqServer needs on this process - it inherits them - and restores them on
/// destruction. PATH is not among them, see startServerProcess().
///
/// Not boost's environment view: a block boost rebuilds from a copy of ours loses entries, and
/// the server then fails to load its DLLs. Native encoding throughout, because the narrow Win32
/// API converts through the active code page and the guard writes back what it read.
class ScopedEnv {
public:
	ScopedEnv() = default;
	ScopedEnv(const ScopedEnv&) = delete;
	ScopedEnv& operator=(const ScopedEnv&) = delete;

	~ScopedEnv() {
		for (auto it = m_saved.rbegin(); it != m_saved.rend(); ++it) {
			assign(it->first, it->second ? it->second->c_str() : nullptr);
		}
	}

	/// Both arguments are UTF-8.
	void set(const std::string& name, const std::string& value) {
		const String nativeName = toNative(name);
		const String nativeValue = toNative(value);

		m_saved.emplace_back(nativeName, get(nativeName));
		assign(nativeName, nativeValue.c_str());
	}

private:
#ifdef _WIN32
	using Char = wchar_t;
#else
	using Char = char;
#endif
	using String = std::basic_string<Char>;

	static String toNative(const std::string& utf8) {
#ifdef _WIN32
		if (utf8.empty()) {
			return String();
		}

		const int size = static_cast<int>(utf8.size());
		const int length = ::MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), size, nullptr, 0);
		if (length <= 0) {
			return String();
		}

		String wide(length, L'\0');
		::MultiByteToWideChar(CP_UTF8, 0, utf8.c_str(), size, wide.data(), length);
		return wide;
#else
		return utf8;
#endif
	}

	static std::optional<String> get(const String& name) {
#ifdef _WIN32
		// With no buffer the reported size includes the terminator, with one it does not - and a
		// result of at least 'size' means the value grew in between and has to be re-read.
		String value;
		for (DWORD size = ::GetEnvironmentVariableW(name.c_str(), nullptr, 0); size != 0; ) {
			value.resize(size);
			const DWORD length = ::GetEnvironmentVariableW(name.c_str(), value.data(), size);
			if (length == 0) {
				break;
			}
			if (length < size) {
				value.resize(length);
				return value;
			}
			size = length;
		}
		return std::nullopt;
#else
		const char* value = ::getenv(name.c_str());
		return value ? std::optional<String>(value) : std::nullopt;
#endif
	}

	/// A null value removes the variable.
	static void assign(const String& name, const Char* value) {
#ifdef _WIN32
		::SetEnvironmentVariableW(name.c_str(), value);
#else
		if (value) {
			::setenv(name.c_str(), value, 1);
		}
		else {
			::unsetenv(name.c_str());
		}
#endif
	}

	std::vector<std::pair<String, std::optional<String>>> m_saved;
};

} // namespace

#ifdef _WIN32
/// Intercepts the console close event (clicking X on the console window) to shut down
/// ZMQ cleanly while threads are still alive. Without this, the static ZmqServer destructor
/// would call zmq_ctx_term() after Windows has already killed all threads, causing a crash.
static BOOL WINAPI zmqConsoleCtrlHandler(DWORD ctrlType) {
	if (ctrlType == CTRL_CLOSE_EVENT) {
		VRayForBlender::ZmqServer::get().stop();
	}
	return FALSE;
}
#endif

#define SAFE_CALL(exp) \
	try{\
		exp;\
	}\
	catch (const std::exception& exc) {\
		Logger::error("Exception in %1%: %2%", #exp, exc.what());\
	}\
	catch (...) {\
		Logger::error("Unknown exception in %1%", #exp);\
	}



namespace {
	static const RoutingId   ID_CONTROL_CONN                      = 1;
	static const int		 EPHEMERAL_PORT_NUM                   = -1; // A special value indicating ZmqServer should listen on an ephemeral port
	static const int         EXIT_CODE_IRRECOVERABLE_ERROR        = 2;	// Do not relaunch ZmqServer if it has exited with this code

	static const auto		ZMQ_SERVER_RESTART_TIMEOUT = 2s;		// Time between restarts when ZmqServer process exits
	static const auto		ZMQ_SERVER_ENDPOINT_READ_TIMEOUT = 1s;	// Timeout for reading the ZmqServer listen port number after it has been published
	static const auto		GRACEFUL_SHUTDOWN_TIMEOUT = 30s;		// How long to wait for the server to exit on its own after requesting a graceful
																	// shutdown, before escalating to SIGKILL. Releasing the license may take a while
																	// on a slow network, so be generous.

}

const int ZmqServer::SERVER_INACTIVITY_INTERVAL;
const int ZmqServer::RENDERER_INACTIVITY_INTERVAL;
const int ZmqServer::HANDSHAKE_TIMEOUT;


ZmqServer::~ZmqServer() {
	stop();
}


void ZmqServer::start(const ZmqServerArgs& args) {
	vassert(!m_conn && "ZmqServer::start() can only be called in stopped state.");

	// Double-start would leak a second server process in release builds.
	if (m_conn) {
		Logger::warning("ZmqServer::start() called while already running - ignored");
		return;
	}

#ifdef _WIN32
	SetConsoleCtrlHandler(zmqConsoleCtrlHandler, TRUE);
#endif

	m_args = args;

	// On abort m_ctx stays null and ZmqAbortError escapes to the caller, which turns it
	// into a start() failure Python can report.
	runGuarded([this] { m_ctx = std::make_unique<zmq::context_t>(1); });

	// Reset the stop token as this function may be called multiple times
	std::stop_source fresh;
	m_stopSource.swap(fresh);

	runServer();
}


void ZmqServer::stop() {
	Logger::info("Blender: Stopping communication channel ...");

	sendMessage(serializeMessage(MsgControlShutdown{}));

	m_stopSource.request_stop();

	{
		std::scoped_lock lock(m_lockConn);
		m_conn.reset();
	}

	// zmq_ctx_term can abort like creation can. Caught here rather than rethrown: stop()
	// runs from ~ZmqServer, where throwing would mean std::terminate.
	try {
		runGuarded([this] {
			if (m_ctx) {
				m_ctx->close();
			}
		});
	}
	catch (const ZmqAbortError& e) {
		// close() was abandoned mid-way, so the context still holds a live handle and
		// ~context_t would call it again - that second failure trips cppzmq's assert(),
		// which no SEH guard can catch. Leak it deliberately; we are shutting down.
		Logger::error("Blender: libzmq aborted during shutdown: %1%", e.what());
		(void)m_ctx.release();
	}

	if (m_processRunner.joinable()) {
		m_processRunner.join();
	}

	m_stopSource = std::stop_source();

	// Since we store python refs in the list we need to destroy the list in GIL. Check if m_pyCallbacks
	// is empty, stop is also called from the destructor of ZmqServer i.e. after Py_Finalize.
	if (!m_pyCallbacks.empty()) {
		nb::gil_scoped_acquire gil;
		m_pyCallbacks.clear();
	}

	Logger::info("Blender: communication channel stopped.");
}


bool ZmqServer::isRunning() const {
	// ZMQ protocol ensures that the logical connection will be kept alive even when the
	// physical connection fails. For a limited amount of time, we can continue sending messages
	// even if the server process is unavailable.
	std::scoped_lock lock(m_lockConn);
	return (m_zmqServerPID != 0) && !!m_conn && !m_conn->isStopped();
}


void ZmqServer::sendMessage(zmq::message_t &&msg, bool allowServerToStealFocus)
{
	std::scoped_lock lock(m_lockConn);

	if (isRunning()) {
		if (allowServerToStealFocus) {
			platform::allowSetForegroundWindow(static_cast<platform::ProcessIdType>(m_zmqServerPID));
		}
		m_conn->send(std::move(msg));
	}
}


void ZmqServer::setPythonCallback(const std::string &name, nb::callable&& callback)
{
	std::scoped_lock lock(m_lock);
	m_pyCallbacks[name] = std::move(callback);
}


nb::handle ZmqServer::getPythonCallback(const std::string &name)
{
	std::scoped_lock lock(m_lock);

	auto cbIter = m_pyCallbacks.find(name);
	if(cbIter != m_pyCallbacks.end()) {
		return cbIter->second;
	}

	Logger::warning("No python callback with name '%1%'", name);
	return nb::callable();
}


zmq::context_t& ZmqServer::context() {
	vassert(m_ctx && "Zmq context not initialized. Call ZmqServer::start() method first.");
	return *m_ctx;
}


const ZmqServerArgs& ZmqServer::getArgs() const {
	return m_args;
}


int ZmqServer::getProcessID() const {
	return m_zmqServerPID;
}


ZmqServer& ZmqServer::get() {
	static ZmqServer svr;
	return svr;
}


std::string ZmqServer::getEndpoint() const {
	return m_args.getAddress(m_zmqServerPort);
}

bool ZmqServer::vrayInitialized() const
{
	return m_mainRendererCreated && m_licenseAcquired;
}

bool ZmqServer::licenseAcquired() const
{
	return m_licenseAcquired;
}

std::pair<std::string, float> ZmqServer::getRenderStage() const
{
	std::scoped_lock l(m_renderStageLock);
	return { m_renderStage, m_renderStageProgress.load() };
}

void ZmqServer::clearRenderStage()
{
	std::scoped_lock l(m_renderStageLock);
	m_renderStage.clear();
	m_renderStageProgress = 0.0f;
}


void ZmqServer::runServer() {
	Logger::get().setLogLevel((Logger::LogLevel)m_args.logLevel);
	m_processRunner = std::thread([this](std::stop_token stopToken) {

		while(!stopToken.stop_requested()) {
			bp::child zmqServer = startServerProcess();
			bool started = false;

			if (zmqServer.running()) {
				if (obtainServerEndpointInfo(zmqServer.id())) {
					m_zmqServerPID = zmqServer.id();
					Logger::info("ZmqServer started. Process ID: %1%. Port: %2%", m_zmqServerPID.load(), m_zmqServerPort.load());
#ifndef _WIN32
					// On Unix systems in case the server crashes we need to manually remove the old shared file,
					// otherwise we would instantly read the old zmq server port in obtainServerEndpointInfo(...).
					if (m_zmqServerPID != 0) {
						SharedMemoryWriter::remove(std::to_string(m_zmqServerPID), SHARED_PORT_MAPPING_ID);
					}
#endif
					startControlConn();
					started = true;
				}
				else {
					Logger::error("ZmqServer failed to open endpoint, relaunching.");
					zmqServer.terminate();
				}
			}
			else {
				Logger::error("ZmqServer process failed to start, relaunching.");
			}

			if (!started) {
				std::this_thread::sleep_for(ZMQ_SERVER_RESTART_TIMEOUT);
			}

			while (
#if not ZMQ_SERVER_ALLOW_ATTACH
				zmqServer.running() &&
#endif
				!stopToken.stop_requested()) {

				// Server process is running. Check periodically whether the control connection
				// is still alive.
				std::this_thread::sleep_for(100ms);

				std::scoped_lock lock(m_lockConn);
				if (!!m_conn && m_conn->isStopped() && zmqServer.running()) {
					// An error has occurred on the connection and it has been stopped.
					// The server is still running, try to reconnect.
					startControlConn();
				}
			}

			// Server has stopped
			m_zmqServerPID = 0;
			m_zmqServerPort = 0;

			if (!stopToken.stop_requested()) {
				// The ZMQ Server process stopped unexpectedly.
				int exitCode = zmqServer.exit_code();
				std::string retCodeStr = VrayZmqWrapper::getServerReturnCodeStr(exitCode);

				Logger::error("ZmqServer process exited with code %1%: %2%", exitCode, retCodeStr);
				if (exitCode == (int)VrayZmqWrapper::ServerReturnCode::NO_LICENSE) {
					break;
				}

				invokePythonCallback("zmqServerAbortCallback", getPythonCallback("zmqServerAbort"), retCodeStr);
				if (zmqServer.exit_code() == EXIT_CODE_IRRECOVERABLE_ERROR) {
					stop();
					return;
				}
			}
			else {
				// Give the server a chance to shut down gracefully (releasing its license and
				// cleaning up the shared memory objects) before killing it.
				const auto deadline = std::chrono::steady_clock::now() + GRACEFUL_SHUTDOWN_TIMEOUT;
				while (zmqServer.running() && (std::chrono::steady_clock::now() < deadline)) {
					std::this_thread::sleep_for(50ms);
				}

				if (zmqServer.running()) {
					Logger::warning("ZmqServer did not shut down gracefully, terminating.");
					zmqServer.terminate();
				}
			}
		}
	}, m_stopSource.get_token());
}

bp::child ZmqServer::startServerProcess() {
	// Restored when 'env' goes out of scope, by which point the child has its own copy.
	ScopedEnv env;
	env.set("VRAY_ZMQSERVER_APPSDK_PATH", m_args.vrayLibPath);
	env.set("QT_PLUGIN_PATH", m_args.appSDKPath);

#ifdef _WIN32
	env.set("QT_QPA_PLATFORM_PLUGIN_PATH", (fs::path(m_args.appSDKPath) / "platforms").string());
#else
	env.set("QTWEBENGINE_RESOURCES_PATH", (fs::path(m_args.appSDKPath) / "resources").string());
	env.set("QTWEBENGINE_LOCALES_PATH", (fs::path(m_args.appSDKPath) / "translations" / "qtwebengine_locales").string());
#endif

	auto args = std::vector<std::string>({
		"-p",              std::to_string(m_args.port),
		"-log",            std::to_string(m_args.logLevel),
		"-blenderPID",     std::to_string(m_args.blenderPID),
		"-vfbSettings",	   m_args.vfbSettingsFile,
		"-pluginVersion",  m_args.pluginVersion,
		"-blenderVersion", m_args.blenderVersion,
		"-license",        m_args.licenseType,
		"-noHeartbeat",
		m_args.headlessMode ? "-headlessMode" : "",
		m_args.enableQtLogs ? "-enableQtLogging" : ""
	});

	if (!m_args.dumpLogFile.empty()) {
		args.push_back("-dumpInfoLog");
		args.push_back(m_args.dumpLogFile);
	}
	// Windows has no rpath and the server's shared libraries live in the AppSDK folder, one level
	// below the exe. A process' working directory is searched ahead of PATH, so starting it there
	// resolves them without touching our PATH. Unix has rpath and keeps the inherited directory.
	std::string startDir;
#ifdef _WIN32
	std::error_code dirError;
	if (fs::is_directory(m_args.appSDKPath, dirError)) {
		startDir = m_args.appSDKPath;
	}
	else {
		Logger::error("AppSDK folder is missing, ZmqServer will fail to start: %1%", m_args.appSDKPath);
	}
#endif

	// An empty start_dir would make boost throw, on a thread with no handler above it.
	auto process = startDir.empty()
		? bp::child(m_args.exePath, bp::args(args))
		: bp::child(m_args.exePath, bp::args(args), bp::start_dir(startDir));
	process.detach();
	return process;
}


bool ZmqServer::obtainServerEndpointInfo(int zmqServerPID) {

	if (m_args.port != EPHEMERAL_PORT_NUM) {
		m_zmqServerPort = m_args.port;
		return true;
	}

	// ZmqServer is listening on an ephemeral port. Once the port is open, ZmqServer
	// will publish it to shared memory with an ID set to SHARED_PORT_MAPPING_ID.
	int zmqServerPort = 0;

	SharedMemoryReader reader(std::to_string(zmqServerPID), SHARED_PORT_MAPPING_ID);

	if (reader.open(std::chrono::milliseconds(HANDSHAKE_TIMEOUT))) {
		// Since we don't lock in ShmReader::open on Unix systems it's possible that the memory is mapped before
		// the writer writes our data and we end up seeing the old data(i.e. the zeroes from the shm::truncate).
		const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(HANDSHAKE_TIMEOUT);
		while (std::chrono::steady_clock::now() < deadline) {
			if (reader.read(ZMQ_SERVER_ENDPOINT_READ_TIMEOUT, &zmqServerPort) && (zmqServerPort > 0)) {
					m_zmqServerPort = zmqServerPort;
					return true;
			}
			std::this_thread::sleep_for(10ms);
		}
	}

	Logger::error("Failed to obtain endpoint info from ZmqServer, error: %1%, Reconnecting.", reader.getLastError());
	return false;
}


void ZmqServer::startControlConn() {

	if (!m_ctx) {
		// The context was abandoned after a libzmq abort. The monitor thread can still
		// reach here while shutting down, and there is nothing left to connect with.
		return;
	}

	const std::string endpoint = m_args.getAddress(m_zmqServerPort);

	Logger::info("Blender: Starting control client for %1%", endpoint);

	auto newConn = std::make_unique<ZmqAgent>(*m_ctx, ID_CONTROL_CONN, ExporterType::FIRST_TYPE, ZmqAgent::Client);

	newConn->setMsgCallback([this](zmq::message_t&& payload) {
			SAFE_CALL(handleMsg(payload))
		});

	newConn->setErrorCallback([this, connPtr = newConn.get()](const std::string& err) {
			if (connPtr->hasAborted()) {
				// Nothing to reconnect to. Same path as a ZmqServer crash: resets the
				// renderers and tells the user a restart is needed.
				Logger::error("Blender: control connection died: '%1%'", err);
				invokePythonCallback("zmqServerAbortCallback", getPythonCallback("zmqServerAbort"),
				                     std::string(CLIENT_COMM_ERROR));
			}
			else {
				Logger::error("Blender: control connection error: '%1%', reconnecting.", err);
			}
			connPtr->stop();
		});

	newConn->setTraceCallback([](const std::string& msg) {
			Logger::debug("Blender: control connection: %1%", msg);
		});

	ZmqTimeouts timeouts;
	timeouts.handshake  = HANDSHAKE_TIMEOUT;
	timeouts.inactivity = SERVER_INACTIVITY_INTERVAL;
	timeouts.ping       = NO_PING;

	{
		std::scoped_lock lock(m_lockConn);

		m_conn = std::move(newConn);
		m_conn->run(endpoint, timeouts);
	}

	Logger::info("Blender: Control client started");

}


void ZmqServer::processControlOnImportAsset(const MsgControlOnImportAsset& message)
{
	nb::gil_scoped_acquire gil; // This is needed for safe python dictionary creation(in the CosmosSettings struct)

	CosmosAssetSettings cosmosSettings;
	cosmosSettings.matFile = message.materialFile;
	cosmosSettings.objFile = message.objectFile;
	cosmosSettings.lightFile = message.lightFile;
	cosmosSettings.luminaireFile = message.luminaireFile;
	cosmosSettings.settingsFile = message.settingsFile;
	cosmosSettings.setInstanceToken = message.setInstanceToken;
	cosmosSettings.packageId = message.packageId;
	cosmosSettings.revisionId = message.revisionId;
	cosmosSettings.assetName = message.packageName;
	cosmosSettings.isAnimated = message.isAnimated;
	cosmosSettings.planeWidth = message.planeWidth;
	cosmosSettings.planeHeight = message.planeHeight;
	cosmosSettings.applyTriplanarMapping = message.applyTriplanarMapping;
	cosmosSettings.texRealWorldWidth = message.texRealWorldWidth;
	cosmosSettings.texRealWorldHeight = message.texRealWorldHeight;
	// Drop-origin fields (populated only for drag-and-drop imports).
	cosmosSettings.hasDropCoords = message.hasDropCoords;
	cosmosSettings.worldX = message.worldX;
	cosmosSettings.worldY = message.worldY;
	cosmosSettings.worldZ = message.worldZ;
	cosmosSettings.dropTargetObject = message.dropTargetObject;
	cosmosSettings.dropTargetSlot = message.dropTargetSlot;
	cosmosSettings.hasHitNormal = message.hasHitNormal;
	cosmosSettings.normalX = message.normalX;
	cosmosSettings.normalY = message.normalY;
	cosmosSettings.normalZ = message.normalZ;
	cosmosSettings.surfaceAttachment = message.surfaceAttachment;
	cosmosSettings.forceNormalAlign = message.forceNormalAlign;

	const AttrListString::DataArrayPtr assetData=message.assetNames.getData();
	const AttrListString::DataArrayPtr assetLocationsData=message.assetLocations.getData();
	const auto& assetNames = *assetData;
	const auto& assetLocations = *assetLocationsData;

	vassert(assetNames.size() == assetLocations.size());

	for (size_t i = 0; i < assetNames.size(); i++) {
		cosmosSettings.locationsMap[assetNames[i].c_str()] = assetLocations[i];
	}

	switch (message.assetType) {
		case ImportedAssetType::Material:
			cosmosSettings.assetType = "Material";
			break;
		case ImportedAssetType::VRMesh:
			cosmosSettings.assetType = "VRMesh";
			break;
		case ImportedAssetType::HDRI:
			cosmosSettings.assetType = "HDRI";
			break;
		case ImportedAssetType::Extras:
			cosmosSettings.assetType = "Extras";
			break;
		case ImportedAssetType::ParallaxInterior:
			cosmosSettings.assetType = "ParallaxInterior";
			break;
		case ImportedAssetType::AssetSet:
			cosmosSettings.assetType = "AssetSet";
			break;
		case ImportedAssetType::ScatterPreset:
			cosmosSettings.assetType = "ScatterPreset";
			break;
	}

	invokePythonCallback("importCosmosAsset", getPythonCallback("assetImport"), cosmosSettings);
}


void ZmqServer::processControlOnCosmosAssetsDownloaded(const MsgControlOnCosmosDownloadedAssets& message) {
	nb::gil_scoped_acquire gil;
	nb::list relinkedPaths = toPyList(message.relinkedPaths);
	invokePythonCallback("setCosmosDownloadAssets", getPythonCallback("setCosmosDownloadAssets"), int(message.downloadStatus), relinkedPaths);
}


void ZmqServer::processControlOnScannedEncodedParameters(const MsgControlOnScannedEncodedParameters& message) {
	if (message.licensed) {
		nb::gil_scoped_acquire gil;
		nb::list paramBlock = toPyList(message.encodedParams);
		invokePythonCallback("scannedParamBlock", getPythonCallback("scannedParamBlock"), message.materialId, message.nodeName, paramBlock);
	}
}

void ZmqServer::processControlOnRendererStatus(const MsgControlOnRendererStatus& message)
{
	switch (message.status) {
	case VRayStatusType::MainRendererAvailable:
		m_mainRendererCreated = true;
		break;
	case VRayStatusType::MainRendererUnavailable:
		m_mainRendererCreated = false;
		break;
	case VRayStatusType::LicenseAcquired:
		m_licenseAcquired = true;
		break;
	case VRayStatusType::LicenseDropped:
		m_licenseAcquired = false;
		break;
	}
}


void ZmqServer::handleMsg(const zmq::message_t& msg)
{
	DeserializerStream stream(reinterpret_cast<const char*>(msg.data()), msg.size());

	MsgType msgType = MsgType::None;
	stream >> msgType;

	switch (msgType) {
		case MsgType::ControlOnImportAsset: {
			const MsgControlOnImportAsset& message = deserializeMessage<MsgControlOnImportAsset>(stream);
			processControlOnImportAsset(message);
			break;
		}
		case MsgType::ControlOnCosmosDownloadSize: {
			const MsgControlOnCosmosDownloadSize& message = deserializeMessage<MsgControlOnCosmosDownloadSize>(stream);
			invokePythonCallback("setCosmosDownloadSize", getPythonCallback("setCosmosDownloadSize"), int(message.relinkStatus), message.downloadSizeMb);
			break;
		}
		case MsgType::ControlOnCosmosDownloadedAssets: {
			const MsgControlOnCosmosDownloadedAssets& message = deserializeMessage<MsgControlOnCosmosDownloadedAssets>(stream);
			processControlOnCosmosAssetsDownloaded(message);
			break;
		}
		case MsgType::ControlOnScannedLicenseCheck: {
			const MsgControlOnScannedLicenseCheck& message = deserializeMessage<MsgControlOnScannedLicenseCheck>(stream);
			invokePythonCallback("scannedLicense", getPythonCallback("scannedLicense"), message.license);
			break;
		}
		case MsgType::ControlOnScannedEncodedParameters: {
			const MsgControlOnScannedEncodedParameters& message = deserializeMessage<MsgControlOnScannedEncodedParameters>(stream);
			processControlOnScannedEncodedParameters(message);
			break;
		}
		case MsgType::ControlOnStartViewportRender:
			invokePythonCallback("startRendering", getPythonCallback("renderStart"), true);
			break;

		case MsgType::ControlOnStartProductionRender:
			invokePythonCallback("startRendering", getPythonCallback("renderStart"), false);
			break;

		case MsgType::ControlOnUpdateVfbSettings: {
			const auto& message = deserializeMessage<MsgControlOnUpdateVfbSettings>(stream);
			invokePythonCallback("updateVfbSettings", getPythonCallback("vfbSettingsUpdate"), message.vfbSettings);
			break;
		}
		case MsgType::ControlOnUpdateVfbLayers: {
			const auto& message = deserializeMessage<MsgControlOnUpdateVfbLayers>(stream);
			invokePythonCallback("updateVfbLayers", getPythonCallback("vfbLayersUpdate"), message.vfbLayersJson);
			break;
		}
		case MsgType::ControlOnLogMessage: {
			const auto& message = deserializeMessage<MsgControlOnLogMessage>(stream);
			Logger::get().log(static_cast<Logger::LogLevel>(message.logLevel), true, message.logMessage);
			break;
		}
		case MsgType::ControlOnRendererStatus: {
			const auto& message = deserializeMessage<MsgControlOnRendererStatus>(stream);
			processControlOnRendererStatus(message);
			break;
		}
		case MsgType::ControlOnRenderStage: {
			const auto& message = deserializeMessage<MsgControlOnRenderStage>(stream);
			{
				std::scoped_lock l(m_renderStageLock);
				m_renderStage = message.stage;
			}
			// Stages reporting no work units send totalElements == 0.
			m_renderStageProgress = (message.totalElements != 0)
				? (static_cast<float>(message.elements) / message.totalElements)
				: 0.0f;
			break;
		}
		case MsgType::ControlOnGetComputeDevices:{
			const auto& message = deserializeMessage<MsgControlOnGetComputeDevices>(stream);
			int deviceType = static_cast<int>(message.deviceType);
			nb::gil_scoped_acquire gil;
			nb::list computeDevices = toPyList(message.computeDevices);
			nb::list defaultDeviceStates = toPyList(message.defaultDeviceStates);
			invokePythonCallback("updateComputeDevices", getPythonCallback("updateComputeDevices"), deviceType, computeDevices, defaultDeviceStates);
			break;
		}
		case MsgType::ControlOnAutoUpdateCheckChanged:{
			const auto& message = deserializeMessage<MsgControlOnAutoUpdateCheckChanged>(stream);
			const bool autoCheck = message.autoCheck;
			invokePythonCallback("autoUpdateCheckChanged", getPythonCallback("autoUpdateCheckChanged"), autoCheck);
			break;
		}
		case MsgType::ControlOnAppUpdateRequested:{
			invokePythonCallback("appUpdateRequested", getPythonCallback("appUpdateRequested"));
			break;
		}
		case MsgType::ControlOnLightMixTransferToScene: {
			const auto& message = deserializeMessage<MsgControlOnLightMixTransferToScene>(stream);
			auto callback = getPythonCallback("lightMixTransferToScene");
			if (!callback.is_none()) {
				nb::gil_scoped_acquire gil;
				nb::list pyChanges;
				for (const auto& c : message.changes) {
					pyChanges.append(nb::make_tuple(c.pluginName, c.colorR, c.colorG, c.colorB, c.intensityMult, c.enabled));
				}
				invokePythonCallback("lightMixTransferToScene", callback, pyChanges);
			}
			break;
		}
		case MsgType::ControlOnVFBMenu: {
			const auto& message = deserializeMessage<MsgControlOnVFBMenu>(stream);
			invokePythonCallback("vfbMenu", getPythonCallback("vfbMenu"), message.mode, message.targetName, message.objectName, message.distance);
			break;
		}
		case MsgType::ControlOnAddRenderElementToScene: {
			const auto& message = deserializeMessage<MsgControlOnAddRenderElementToScene>(stream);
			invokePythonCallback("addRenderElementToScene", getPythonCallback("addRenderElementToScene"), message.renderElementType);
			break;
		}
		case MsgType::ControlOnVFBShowMessagesWindow: {
			invokePythonCallback("vfbShowMessagesWindow", getPythonCallback("vfbShowMessagesWindow"));
			break;
		}
		case MsgType::ControlOnVFBRenderRegionChanged: {
			const auto& message = deserializeMessage<MsgControlOnVFBRenderRegionChanged>(stream);
			invokePythonCallback("vfbRenderRegionChanged",
				getPythonCallback("vfbRenderRegionChanged"),
				message.x, message.y, message.width, message.height, message.enabled);
			break;
		}
		case MsgType::ControlOnSwitchLicenseToCommunity:
			invokePythonCallback("switchLicenseToCommunity", getPythonCallback("switchLicenseToCommunity"));
			break;

		default:
			break;
	}
}
