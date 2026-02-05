#include "zmq_server.h"
#include "vassert.h"
#include "utils/logger.hpp"
#include "plugin_desc.hpp"

#include <filesystem>
#include <limits>
#include <random>

#include <ipc.h>
#include <zmq_common.hpp>
#include <zmq_message.hpp>

#include <boost/python/dict.hpp>
#include <boost/process.hpp>

#include "api/interop/conversion.hpp"

namespace fs = std::filesystem;
namespace bp = boost::process;


using namespace std::chrono_literals;
using namespace VRayForBlender;
using namespace VrayZmqWrapper;
using namespace VRayBaseTypes;

#define ZMQ_SERVER_ALLOW_ATTACH 0

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

}

const int ZmqServer::SERVER_INACTIVITY_INTERVAL;
const int ZmqServer::RENDERER_INACTIVITY_INTERVAL;
const int ZmqServer::HANDSHAKE_TIMEOUT;


ZmqServer::~ZmqServer() {
	stop();
}


void ZmqServer::start(const ZmqServerArgs& args) {
	assert(!m_conn && "ZmqServer::start() method can only be called once.");
	assert(nullptr != m_ctx.handle());

	m_args = args;
	m_ctx = zmq::context_t(1);

	// Reset the stop token as this function may be called multiple times
	std::stop_source fresh; 
	m_stopSource.swap(fresh);

	runServer();
}


void ZmqServer::stop() {
	Logger::info("Blender: Stopping communication channel ...");

	m_stopSource.request_stop();

	{
		std::scoped_lock lock(m_lockConn);
		m_conn.reset();
	}

	m_ctx.close();

	if (m_processRunner.joinable()) {
		m_processRunner.join();
	}
	m_stopSource = std::stop_source();

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


void ZmqServer::setPythonCallback(const std::string &name, py::object callback)
{
	std::scoped_lock lock(m_lock);
	m_pyCallbacks[name] = callback;
}


py::object ZmqServer::getPythonCallback(const std::string &name)
{
	std::scoped_lock lock(m_lock);

	if(m_pyCallbacks.find(name) != m_pyCallbacks.end()) {
		return m_pyCallbacks[name];
	}

	Logger::warning("No python callback with name '%1%'", name);
	return py::object();
}


zmq::context_t& ZmqServer::context() {
	assert(m_conn && "Zmq context not initialized. Call ZmqServer::start() method first.");
	return m_ctx;
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


void ZmqServer::runServer() {

	m_processRunner = std::thread([this](std::stop_token stopToken) {

		while(!stopToken.stop_requested()) {
#ifndef _WIN32
			// On Unix systems in case the server crashes we need to manually remove the old shared file,
			// otherwise we would instantly read the old zmq server port in obtainServerEndpointInfo(...).
			if (m_zmqServerPID != 0) {
				SharedMemoryWriter::remove(std::to_string(m_zmqServerPID), SHARED_PORT_MAPPING_ID);
			}
#endif

			bp::child zmqServer = startServerProcess();
			bool started = false;
			
			if (zmqServer.running()) {
				if (obtainServerEndpointInfo(zmqServer.id())) {
					Logger::info("ZmqServer started. Process ID: %1%. Port: %2%", m_zmqServerPID.load(), m_zmqServerPort.load());
					m_zmqServerPID = zmqServer.id();
					
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
				// Terminating the ZMQ server if a stop is requested.
				// Otherwise, it will continue running with no way to stop it while Blender is running.
				zmqServer.terminate();
			}
		}
	}, m_stopSource.get_token());
}

bp::child ZmqServer::startServerProcess() {

	// Add environment variables VRayZmqServer process needs to the current environment.
	auto env = boost::this_process::environment();
	env["VRAY_ZMQSERVER_APPSDK_PATH"] = m_args.vrayLibPath;
	env["QT_PLUGIN_PATH"] = m_args.appSDKPath;

#ifdef _WIN32
	env["PATH"] = m_args.appSDKPath;
	env["QT_QPA_PLATFORM_PLUGIN_PATH"] = (fs::path(m_args.appSDKPath) / "platforms").string();
#else
	env["QTWEBENGINE_RESOURCES_PATH"] = (fs::path(m_args.appSDKPath) / "resources").string();
	env["QTWEBENGINE_LOCALES_PATH"] = (fs::path(m_args.appSDKPath) / "translations" / "qtwebengine_locales").string();
#endif

	auto args = std::vector<std::string>({
		"-p",              std::to_string(m_args.port),
		"-log",            std::to_string(m_args.logLevel),
		"-blenderPID",     std::to_string(m_args.blenderPID),
		"-vfbSettings",	   m_args.vfbSettingsFile,
		"-pluginVersion",  m_args.pluginVersion,
		"-blenderVersion", m_args.blenderVersion,
		"-noHeartbeat",
		m_args.headlessMode ? "-headlessMode" : "",
		m_args.enableQtLogs ? "-enableQtLogging" : ""
	});

	if (!m_args.dumpLogFile.empty()) {
		args.push_back("-dumpInfoLog");
		args.push_back(m_args.dumpLogFile);
	}
	auto process = bp::child(m_args.exePath, bp::args(args));
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

	if ( reader.open(std::chrono::milliseconds(HANDSHAKE_TIMEOUT)) &&
			reader.read(ZMQ_SERVER_ENDPOINT_READ_TIMEOUT, &zmqServerPort)) {
		m_zmqServerPort = zmqServerPort;
		return true;
	}

	Logger::error("Failed to obtain endpoint info from ZmqServer, error: %1%, Reconnecting.", reader.getLastError());
	return false;
}


void ZmqServer::startControlConn() {

	const std::string endpoint = m_args.getAddress(m_zmqServerPort);

	Logger::info("Blender: Starting control client for %1%", endpoint);

	auto newConn = std::make_unique<ZmqAgent>(m_ctx, ID_CONTROL_CONN, ExporterType::FIRST_TYPE, ZmqAgent::Client);

	newConn->setMsgCallback([this](zmq::message_t&& payload) {
			SAFE_CALL(handleMsg(payload))
		});

	newConn->setErrorCallback([this, connPtr = newConn.get()](const std::string& err) {
			Logger::error("Blender: control connection error: '%1%', reconnecting.", err);
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
	WithGIL gil; // This is needed for safe python dictionary creation(the asset import queue)

	CosmosAssetSettings cosmosSettings;
	cosmosSettings.matFile = message.materialFile;
	cosmosSettings.objFile = message.objectFile;
	cosmosSettings.lightFile = message.lightFile;
	cosmosSettings.packageId = message.packageId;
	cosmosSettings.revisionId = message.revisionId;
	cosmosSettings.isAnimated = message.isAnimated;

	const AttrListString::DataArrayPtr assetData=message.assetNames.getData();
	const AttrListString::DataArrayPtr assetLocationsData=message.assetLocations.getData();
	const auto& assetNames = *assetData;
	const auto& assetLocations = *assetLocationsData;

	assert(assetNames.size() == assetLocations.size());

	for (size_t i = 0; i < assetNames.size(); i++) {
		cosmosSettings.locationsMap[assetNames[i]] = assetLocations[i];
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
	}

	invokePythonCallback("importCosmosAsset", getPythonCallback("assetImport"), cosmosSettings);
}


void ZmqServer::processControlOnCosmosAssetsDownloaded(const MsgControlOnCosmosDownloadedAssets& message) {
	py::list relinkedPaths = toPyList(message.relinkedPaths);
	invokePythonCallback("setCosmosDownloadAssets", getPythonCallback("setCosmosDownloadAssets"), int(message.downloadStatus), relinkedPaths);
}


void ZmqServer::processControlOnScannedEncodedParameters(const MsgControlOnScannedEncodedParameters& message) {
	if (message.licensed) {
		py::list paramBlock = toPyList(message.encodedParams);
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
		case MsgType::ControlOnGetComputeDevices:{
			const auto& message = deserializeMessage<MsgControlOnGetComputeDevices>(stream);
			int deviceType = static_cast<int>(message.deviceType);
			py::list computeDevices = toPyList(message.computeDevices);
			py::list defaultDeviceStates = toPyList(message.defaultDeviceStates);
			invokePythonCallback("updateComputeDevices", getPythonCallback("updateComputeDevices"), deviceType, computeDevices, defaultDeviceStates);
			break;
		}
		default:
			break;
	}
}
