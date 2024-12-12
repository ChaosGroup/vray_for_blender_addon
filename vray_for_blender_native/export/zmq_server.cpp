#include "zmq_server.h"
#include "utils/assert.h"
#include "utils/logger.hpp"
#include "plugin_desc.hpp"

#include <filesystem>
#include <fstream>
#include <limits>
#include <random>

#include <ipc.h>
#include <zmq_common.hpp>
#include <zmq_message.hpp>

#include <boost/python/dict.hpp>
#include <boost/process.hpp>

namespace fs = std::filesystem;
namespace bp = boost::process;


using namespace std::chrono_literals;
using namespace VRayForBlender;
using namespace VrayZmqWrapper;
using namespace VRayBaseTypes;


#define SAFE_CALL(exp) \
	try{\
		exp;\
	}\
	catch (const std::exception& exc) {\
		Logger::error("Exception in {}: {}", #exp, exc.what());\
	}\
	catch (...) {\
		Logger::error("Unknown exception in {}", #exp);\
	}



namespace {
	static const RoutingId   ID_CONTROL_CONN                      = 1;
	static const int		 EPHEMERAL_PORT_NUM                   = -1; // A special value indicating ZmqServer should listen on an ephemeral port
	static const int         EXIT_CODE_IRRECOVERABLE_ERROR        = 2;	// Do not relaunch ZmqServer if it has exited with this code
	
	static const auto		ZMQ_SERVER_RESTART_TIMEOUT = 2s;		// Time between restarts when ZmqServer process exits
	static const auto		ZMQ_SERVER_ENDPOINT_READ_TIMEOUT = 1s;	// Timeout for reading the ZmqServer listen port number after it has been published

}


void ZmqServer::start(const ZmqServerArgs& args) {
	assert(!m_conn && "ZmqServer::start() method can only be called once.");
	assert(nullptr == m_ctx.handle());

	m_args = args;
	m_ctx = zmq::context_t(1);

	runServer();
}


void ZmqServer::stop() {
	Logger::info("Blender: Stopping communication channel ...");
	
	m_processRunner.request_stop();
	
	{
		std::scoped_lock lock(m_lockConn);
		m_conn.reset();
	}

	m_ctx.close();

	if (m_processRunner.joinable()) {
		m_processRunner.join();
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

void ZmqServer::setPythonCallback(const std::string &name, py::object callback)
{
	m_pyCallbacks[name] = callback;
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

py::object ZmqServer::getPythonCallback(const std::string &name)
{
	if(m_pyCallbacks.find(name) != m_pyCallbacks.end()) {
		return m_pyCallbacks[name];
	}
	
	Logger::warning("No python callback with name '{}'", name);
	return py::object();
}


zmq::context_t& ZmqServer::context() {
	assert(m_conn && "Zmq context not initialized. Call ZmqServer::start() method first.");
	return m_ctx;
}


const ZmqServerArgs& ZmqServer::getArgs() const {
	return m_args;
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

	m_processRunner = std::jthread([this](std::stop_token stopToken) {
		
		while(!stopToken.stop_requested()) {

			bp::child zmqServer = startServerProcess();
			bool started = false;

			if (zmqServer.running()) {
				if (obtainServerEndpointInfo()) {
					m_zmqServerPID = zmqServer.id();
					Logger::info("ZmqServer started. Process ID: {}. Port: {}", m_zmqServerPID.load(), m_zmqServerPort.load());
			
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

			while(zmqServer.running() && !stopToken.stop_requested()) {
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
			m_zmqServerPID  = 0;
			m_zmqServerPort = 0;

			if (!stopToken.stop_requested()) {
				// The ZMQ Server process stopped unexpectedly.
				int exitCode = zmqServer.exit_code();
				Logger::error("ZmqServer process exited with code {}: {}", exitCode, VrayZmqWrapper::getServerReturnCodeStr(exitCode));
				if(exitCode==(int)VrayZmqWrapper::ServerReturnCode::NO_LICENSE) {
					break;
				}
				invokePythonCallback("renderAbortCallback", getPythonCallback("renderAbort"));
				startControlConn();
				if (zmqServer.exit_code() == EXIT_CODE_IRRECOVERABLE_ERROR) {
					stop();
					return;
				}
			}
		}
	});
}


bp::child ZmqServer::startServerProcess() {
	
	// Add environment variables VRayZmqServer process needs to the current environment. 
	auto env = boost::this_process::environment();

	env["VRAY_ZMQSERVER_APPSDK_PATH"] = m_args.vrayLibPath;
	env["PATH"] = m_args.appSDKPath;
	env["QT_PLUGIN_PATH"] = m_args.appSDKPath;
	env["QT_QPA_PLATFORM_PLUGIN_PATH"] = (fs::path(m_args.appSDKPath) / "platforms").string();

	auto args = std::vector<std::string>({
		"-p",             std::to_string(m_args.port),
		"-log",           std::to_string(m_args.logLevel),
		"-blenderPID",    std::to_string(m_args.blenderPID),
		"-vfbSettings",	  m_args.vfbSettingsFile,
		"-renderThreads", std::to_string(m_args.renderThreads),
		"-pluginVersion", m_args.pluginVersion,
		"-noHeartbeat",
		m_args.headlessMode ? "-headlessMode" : ""
	});
	
	if (!m_args.dumpLogFile.empty()) {
		args.push_back("-dumpInfoLog");
		args.push_back(m_args.dumpLogFile);
	}
	auto process = bp::child(m_args.exePath, bp::args(args));
	process.detach();
	return process;
}


bool ZmqServer::obtainServerEndpointInfo() {
	

	if (m_args.port != EPHEMERAL_PORT_NUM) {
		m_zmqServerPort = m_args.port;
		return true;
	}
	
	// ZmqServer is listening on an ephemeral port. Once the port is open, ZmqServer 
	// will publish it to shared memory with an ID set to SHARED_PORT_MAPPING_ID.
	int zmqServerPort = 0;

	SharedMemoryReader reader(std::to_string(m_args.blenderPID), SHARED_PORT_MAPPING_ID);
	
	if ( reader.open(std::chrono::milliseconds(HANDSHAKE_TIMEOUT)) && 
			reader.read(ZMQ_SERVER_ENDPOINT_READ_TIMEOUT, &zmqServerPort)) {
		
		m_zmqServerPort = zmqServerPort;
		return true;
	}
	
	Logger::error("Failed to obtain endpoint info from ZmqServer, error: {}, Reconnecting.", reader.getLastError());
	return false;
}


void ZmqServer::startControlConn() {

	const std::string endpoint = m_args.getAddress(m_zmqServerPort);

	Logger::info("Blender: Starting control client for {}", endpoint);

	auto newConn = std::make_unique<ZmqAgent>(m_ctx, ID_CONTROL_CONN, ExporterType::FIRST_TYPE, ZmqAgent::Client);

	newConn->setMsgCallback([this](zmq::message_t&& payload) {
			SAFE_CALL(handleMsg(VRayMessage::fromZmqMessage(payload)))
		});

	newConn->setErrorCallback([this, connPtr = newConn.get()](const std::string& err) {
			Logger::error("Blender: control connection error: '{}', reconnecting.", err);
			connPtr->stop();
		});

	newConn->setTraceCallback([](const std::string& msg) {
			Logger::debug("Blender: control connection: {}", msg);
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


void ZmqServer::importCosmosAsset(const VrayZmqWrapper::VRayMessage &message)
{
	WithGIL gil;//This is needed for safe python dictionary creation

	py::dict locationMap;
	const auto &assetNames = *message.getAssetMetaData().assetNames.getData();
	const auto &assetLocations = *message.getAssetMetaData().assetLocations.getData();

	assert(assetNames.size() == assetLocations.size());

	for (size_t i = 0; i < assetNames.size(); i++) {
		locationMap[assetNames[i]] = assetLocations[i];
	}

	std::string type;
	switch (message.getAssetMetaData().type) {
		case VRayMessage::ImportedAssetType::Material:
			type = "Material";
			break;
		case VRayMessage::ImportedAssetType::VRMesh:
			type = "VRMesh";
			break;
		case VRayMessage::ImportedAssetType::HDRI:
			type = "HDRI";
			break;
	}

	invokePythonCallback("importCosmosAsset",
	                     getPythonCallback("assetImport"),
	                     type,
	                     message.getAssetMetaData().materialFile,
	                     message.getAssetMetaData().objectFile,
	                     message.getAssetMetaData().lightFile,
	                     locationMap);
}

void ZmqServer::handleStatusMsg(const VrayZmqWrapper::VRayMessage& message)
{
	switch (message.getVrayStatus()) {
		case VRayMessage::VRayStatusType::MainRendererAvailable:
			m_mainRendererCreated = true;
			break;
		case VRayMessage::VRayStatusType::MainRendererUnavailable:
			m_mainRendererCreated = false;
			break;
		case VRayMessage::VRayStatusType::LicenseAcquired:
			m_licenseAcquired = true;
			break;
		case VRayMessage::VRayStatusType::LicenseDropped:
			m_licenseAcquired = false;
			break;
	}
}


void ZmqServer::handleMsg(const VrayZmqWrapper::VRayMessage &message)
{
	const auto msgType = message.getType();
	switch (msgType) {
		case VRayMessage::Type::ImportAsset:
			importCosmosAsset(message);
			break;

		case VRayMessage::Type::Control:
			switch (message.getControlAction()) {
				case VRayMessage::ControlAction::StartViewportRender:
					invokePythonCallback("startRendering", getPythonCallback("renderStart"), true);
					break;
				case VRayMessage::ControlAction::StartProductionRender:
					invokePythonCallback("startRendering", getPythonCallback("renderStart"), false);
					break;
				case VRayMessage::ControlAction::UpdateVfbSettings:
					invokePythonCallback("updateVfbSettings", getPythonCallback("vfbSettingsUpdate"), message.getVfbSettings());
					break;
				case VRayMessage::ControlAction::UpdateVfbLayers:
					invokePythonCallback("updateVfbLayers", getPythonCallback("vfbLayersUpdate"), message.getVfbLayers());
					break;
				case VRayMessage::ControlAction::LogMessage:
					Logger::get().log(static_cast<Logger::LogLevel>(message.getLogLevel()), true, message.getLogMessage(), 0);					
					break;
				case VRayMessage::ControlAction::VRayStatus:
					handleStatusMsg(message);
					break;
			}
			break;

		// The switch is used because in the future there 
		// might be more message types for handling
	}
}


