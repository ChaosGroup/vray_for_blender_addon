// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <chrono>
#include <deque>
#include <mutex>
#include <string>
#include <thread>


#define ZMQ_HAVE_POLLER
#define ZMQ_BUILD_DRAFT_API		// for poller_t

#include "cppzmq/zmq.hpp"
#include "cppzmq/zmq_addon.hpp"

#include "zmq_common.hpp"


////////////////////////////////////////////////////////////////////////////////
/// ZmqAgent is part of the implementation of the communication protocol
/// between Blender and Vray ZmqServer. It is designed to be used in the following
/// pattern:
///
///     ZmqAgent(Blender) <--> ZmqRouter <--> ZmqAgent(Worker)
///
/// Clients (Blender side) connect to the router (server side).The router creates
/// a worker for each connected client and relays any further communication
/// between them.
///
////////////////////////////////////////////////////////////////////////////////

namespace VrayZmqWrapper{

class ZmqAgent {
	using Clock     = std::chrono::steady_clock;
	using TimePoint = std::chrono::time_point<Clock>;
	using MsgQueue  = std::deque<zmq::multipart_t>;

	// Disable copy and assign
	ZmqAgent (const ZmqAgent&) = delete;
	ZmqAgent& operator= (const ZmqAgent&) = delete;

	enum class State : int {
		Idle = 0,   // Created
		Running,    // The poller loop is running
		Stopping,   // Stop signaled to the poller loop
		Stopped     // Poller thread has exited
	};

public:
	static const bool Client = true;
	static const bool Worker = false;

	using MessageCallback = std::function<void(zmq::message_t&& payload)>;
	using ErrorCallback   = std::function<void(const std::string& msg)>;
	using TraceCallback   = std::function<void(const std::string& msg)>;

public:
	/// @param ctx - an initialized ZMQ context
	/// @param id - routing ID for the agent's connection
	/// @param isClient - set to 'true' to make this agent initiate the handshake sequence
	///                   or to 'false' to make it respond to an incoming handshake
	/// @param workerType - arbitrary ID of the type of worker to create. This ID it transparent
	///	                    to the protocol.
	ZmqAgent( zmq::context_t& ctx, const RoutingId& id, ExporterType workerType, bool isClient);
	~ZmqAgent();

	/// Run communication on the socket in a separate thread.
	/// @param endpoint - endpoint to connect to
	/// @param timeoutSettings - timeout settings for the ZMQ socket
	void run (const std::string& endpoint, const ZmqTimeouts& timeoutSettings );

	/// Signal the poller thread to stop, optionally waiting for it to exit
	/// @param block - wait for the poller thread to exit
	void stop (bool block = false);

	/// Add a message to the outgoing message queue. This method is thread-safe.
	/// @param payload - a ZMQ message
	/// @param msgType - protocol message type
	void send (zmq::message_t&& payload, ControlMessage msgType = ControlMessage::DATA);

	/// Subscribe for messages received from the socket. This subscription is obligatory.
	void setMsgCallback  (MessageCallback cb);

	/// Subscribe for error notifications
	void setErrorCallback (ErrorCallback cb);

	/// Subscribe for debug trace notifications
	void setTraceCallback (TraceCallback cb);

	/// Returns the routing ID of the ZMQ connection
	const RoutingId& routingId() const;

	/// Returns the worker type of the ZMQ connection
	ExporterType getWorkerType() const;

	/// Returns 'true' when the poller thread has exited and the agent can safely be destroyed
	bool isStopped () const;

private:
	void pollerLoop	(std::string addr);

	zmq::socket_t connect(const std::string& addr, RoutingId id);
	void handshake	 (zmq::socket_ref sock);
	void sendPending (zmq::poller_t<>& pollerSend);
	void recvPending (zmq::poller_t<>& pollerRecv);
	void processTick ();

	void sendPingMsg (bool ping);
	void reportError (const std::string& errMsg);
	void trace       (const std::string& errMsg);

private:

	// After some testing(with pyzmq) dealer/router with tcp can get up to about 150k 250 byte messages
	// or about a single PluginUpdate(about 30mb/s throughput). This is fairly similar accross all
	// operating sytstems and this MAX_BATCH here is almost never reached(because python update calls
	// are slow enough). MAX_MATCH is big so that we allow as many small messages as possible, but the
	// main limiting factor is MAX_MSG_BYTES in sendPending(...) to prevent a bunch of large messages
	// e.g. meshes, hair, partcles from blocking everything else.
	static const size_t MAX_BATCH = 16384;       ///< The max number of messages that will be sent in a single batch.
	zmq::multipart_t* msgBufferItems[MAX_BATCH]; ///< The temporary buffer used for sending messages.

	zmq::context_t &ctx;            ///< The zmq context
	RoutingId id;                   ///< ZMQ routing ID
	ExporterType workerType;		///< The type of worker to create. This value is transparent to the protocol.
	bool isClient;                  ///< Client will initiate the handshake
	std::atomic<State> state;       ///< The running state of the agent

	ZmqTimeouts timeouts;           ///< Timeout settings
	MsgQueue msgQueue;              ///< Queue for outgoing messages
	std::mutex queueMutex;          ///< Guards msgQueue
	std::thread pollerThread;       ///< Thread for the polling operations

	TimePoint lastActivity;         ///< Last time when activity was detected on the connected peer
	TimePoint lastPing;             ///< Last time when a ping was sent
	MessageCallback msgCallback;    ///< Callback for the message processing function
	ErrorCallback errorCallback;    ///< Callback for asynchronous errors
	TraceCallback traceCallback;    ///< Callback for debug traces
};

};  // end VrayZmqWrapper namespace
