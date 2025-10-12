#pragma once


#include "api/interop/types.h"
#include "render_image.h"
#include "utils/platform.h"

#include "zmq_common.hpp"
#include "zmq_message.hpp"
#include "zmq_agent.h"

#include <stop_token>
#include <string>
#include <thread>

#ifdef __clang__
#pragma clang diagnostic push
#pragma clang diagnostic ignored "-Wdeprecated-declarations"
#endif
#include <boost/process.hpp>
#ifdef __clang__
#pragma clang diagnostic pop
#endif

#include <unordered_map>

// Forward declarations
struct PluginDesc;

namespace proto = VrayZmqWrapper;

namespace VRayForBlender {

using namespace Interop;

/// ZmqServer singleton manages the zmq context and control connection to ZmqServer.
class ZmqServer {
public:
	// Disable the heartbeat on the control connection. The liveness of ZmqServer
	// will be checked by other means.
	static const int SERVER_INACTIVITY_INTERVAL = VrayZmqWrapper::HEARTBEAT_NO_TIMEOUT;

	static const int RENDERER_INACTIVITY_INTERVAL = 5000;   //ms

	// Creating the renderer is part of the handshake. It also carries
	// out the second part of the license check procedure, hence the relatively big interval.
	static const int HANDSHAKE_TIMEOUT = 15000;  //ms


	using ZmqAgentPtr = std::unique_ptr<VrayZmqWrapper::ZmqAgent>;

public:
	void start(const ZmqServerArgs& args);
	void stop();
	bool isRunning() const;
	void setLogLevel(int lvl);
	std::string getEndpoint() const;

	/// Indicates if the V-Ray instance in the ZmqServer is fully initialized
	bool vrayInitialized() const;

	///  Indicates that license is obtained
	bool licenseAcquired() const;

	// Adds python callback to the callbacks list
	void setPythonCallback(const std::string &name, py::object callback);

	/// Gets a stored callback
	py::object getPythonCallback(const std::string &name);

	/// Sends message to ZmqServer through control connection
	/// @param msg Message to be sent
	/// @param allowServerToStealFocus allows the ZmqServer process to steal the windows's focus after the message is sent
	/// Note: For example control messages for opening Cosmos and Collaboration need that.
	void sendMessage(zmq::message_t &&msg, bool allowServerToStealFocus = false);

	zmq::context_t& context();

	const ZmqServerArgs& getArgs() const;

	/// This class' singleton instance accessor.
	static ZmqServer& get();

private:
	/// Run ZmqSrever process in a thread that will monitor and restart it as needed.
	void runServer();

	/// Start VRayZmqServer process.
	boost::process::child startServerProcess();

	bool obtainServerEndpointInfo();

	void startControlConn();

	/// Process a message from ZmqServer
	void handleMsg(const zmq::message_t& msg);

	void processControlOnImportAsset(const proto::MsgControlOnImportAsset& message);
	void processControlOnRendererStatus(const proto::MsgControlOnRendererStatus& message);
	void processControlOnCosmosAssetsDownloaded(const proto::MsgControlOnCosmosDownloadedAssets& message);
	void processControlOnScannedEncodedParameters(const proto::MsgControlOnScannedEncodedParameters& message);


private:
	zmq::context_t   m_ctx;               /// Zmq context. Needed for all communication operations through ZMQ
	ZmqAgentPtr      m_conn;              /// The connection to ZMQ Server used for control (non-render) messages
	ZmqServerArgs	 m_args;              /// Configuration information for the ZmqServer process

	std::thread      m_processRunner;     /// A thread which will start and monitor ZmqServer process
	std::stop_source m_stopSource;        //// A stop source object for stopping the process runner thread.
	std::atomic_int  m_zmqServerPID = 0;  /// The process ID of ZMQ server, != 0 if server is running
	std::atomic_int  m_zmqServerPort = 0; /// The port on which ZmqServer listens for connections

	mutable std::mutex m_lock;				  /// Synchronize internal state
	mutable std::recursive_mutex m_lockConn;  /// Synchronize access to the zmq agent

	std::unordered_map<std::string, py::object> m_pyCallbacks; /// Stores python callbacks 

	std::atomic_bool m_mainRendererCreated = false; /// Flag for indication that the main renderer in the ZMQ server is created
	std::atomic_bool m_licenseAcquired = false; /// Flag for indication of license acquisition

};

} // namespace VRayForBlender

