// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <list>
#include <mutex>


#define ZMQ_HAVE_POLLER
#define ZMQ_BUILD_DRAFT_API	// for poller_t

#include "cppzmq/zmq.hpp"

#include "zmq_common.hpp"
#include "zmq_agent.h"


 ////////////////////////////////////////////////////////////////////////////////
 /// ZmqRouter is part of the implementation of the communication protocol
 /// between Blender and Vray ZmqServer. It creates a tunnel between a pair of 
 /// ZmqAgents in the following intended configuration
 ///
 ///     ZmqAgent(Blender) <--> ZmqRouter <--> ZmqAgent(RendererController)
 /// 
 /// Clients (Blender side) connect to the router (server side ). The router creates 
 /// a rendering worker for each connected client and relays any further communication 
 /// between them
 /// 
 ////////////////////////////////////////////////////////////////////////////////
namespace VrayZmqWrapper{

class ZmqRouter {

private:
	// Disable copy and assign
	ZmqRouter (const ZmqRouter&) = delete;
	ZmqRouter& operator= (const ZmqRouter&) = delete;

public:
	using ZmqAgentPtr       = std::unique_ptr<ZmqAgent>;
	using NewWorkerCallback = std::function<void(ZmqAgentPtr)>;
	using StartedCallback   = std::function<void(int clientListenPort)>;
	using ErrorCallback     = std::function<void(std::string msg)>;
	using TraceCallback     = std::function<void(const std::string &msg)>;

public:
	explicit ZmqRouter(zmq::context_t& ctx);
	~ZmqRouter();

	/// Run the poller loop
	/// @param clientEndpoint - endpoint for clients to connect to
	/// @param workerEndpoint - endpoint for workers to connect to
	/// @param timeoutSettings - timeout settings for the connections on both endpoints
	void run    (const std::string& clientEndpoint, const std::string& workerEndpoint, const ZmqTimeouts& timeoutSettings);
	
	// Signal the poller loop to stop
	void stop   ();

	/// New worker notification
	void setNewWorkerCallback(NewWorkerCallback cb);

	/// Router has successfully opened a listening socket
	void setStartedCallback(StartedCallback cb);
	
	/// Error notification
	void setErrorCallback(ErrorCallback cb);

	/// Debug trace notification
	void setTraceCallback(TraceCallback cb);

private:
	zmq::socket_t bind (const std::string& serverEndpoint);

	void processHandshake(zmq::socket_ref sock, const zmq::multipart_t& msg);

	// Relay a message between client and server, in both directions
	void relayMsg(zmq::socket_ref sock, zmq::multipart_t& msg);

	// Reply with error to the peer of a failed socket
	void replyWithError (zmq::socket_ref toSock, const RoutingId& routingId, const std::string& errMsg);
	

	// Poller thread procedure
	void pollerLoop	(std::string clientAddr, std::string endpointServer);

	// Invoke the error callback
	void reportError    (const std::string& err) const;

	// Invoke the debug trace callback
	void trace(const std::string& msg);

	// Invoke the router started callback
	void notifyStarted(zmq::socket_ref clientListenSocket);

private:
	zmq::context_t& ctx;                    ///< The zmq context, the same for all sockets
	ZmqTimeouts timeouts;                   ///< Communication timeout settings
	
	std::thread pollerThread;               ///< The thread that runs the poller loop
	std::atomic_bool stopPolling = false;   ///< Flag to signal stopPolling to the poller loop
	std::atomic_int clientPort = 0;			///< Port to which the client listener is bound
	
	zmq::socket_t sockClient;               ///< Socket listening for client requests
	zmq::socket_t sockServer;               ///< Socket listening for server requests
	
	NewWorkerCallback newWorkerCallback;    ///< Hands out a newly created worker in idle state upon new client connection.
	StartedCallback startedCallback;        ///< Provides information about the client endpoint the router has opened.
	ErrorCallback errorCallback;            ///< Error report callback
	TraceCallback traceCallback;            ///< Debug trace callback

};

};  // end VrayZmqWrapper namespace 