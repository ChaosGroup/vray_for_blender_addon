#include "zmq_common.hpp"
#include "zmq_agent.h"

#include <chrono>
#include <vector>

#include "cppzmq/zmq.hpp"
#include "cppzmq/zmq_addon.hpp"
#include "vassert.h"

using namespace std::chrono_literals;

namespace VrayZmqWrapper{

static const bool PING = true;
static const bool PONG = false;


zmq::multipart_t createMsg(ControlMessage msgType, zmq::message_t&& payload) {

	zmq::multipart_t msg;
	msg.add(toZmqMsg(msgType));
	msg.add(std::move(payload));

	return msg;
}

const bool ZmqAgent::Worker;
const bool ZmqAgent::Client;

ZmqAgent::ZmqAgent(zmq::context_t& ctx, const RoutingId& id, ExporterType workerType, bool isClient)
	: ctx(ctx)
	, id(id)
	, workerType(workerType)
	, isClient(isClient)
	, state(State::Idle)
{
}


ZmqAgent::~ZmqAgent() {

	vassert((std::this_thread::get_id() != pollerThread.get_id()) && "Cannot stop the poller thread from a callback");

	try {
		// Stop and wait for the poller loop thread to exit
		stop(true);

		if (pollerThread.joinable()) {
			pollerThread.join();
		}
	}
	catch (const std::exception& exc) {
		trace(Msg("Exception in ZmqAgent's destructor: ", exc.what()));
		// Nothing more we can do
		vassert(!"Exception in ZmqAgent destructor");
	}
}


void ZmqAgent::setMsgCallback(MessageCallback cb) {
	vassert(cb && "Unsubscribing is not supported");
	vassert(!msgCallback && "Multiple subscriptions for the same event are not supported");
	vassert((state == State::Idle) && "Callbacks should be registered before running the agent");
	msgCallback = cb;
}


void ZmqAgent::setErrorCallback(ErrorCallback cb) {
	vassert(cb && "Unsubscribing is not supported");
	vassert(!errorCallback && "Multiple subscriptions for the same event are not supported");
	vassert((state == State::Idle) && "Callbacks should be registered before running the agent");
	errorCallback = cb;
}


void ZmqAgent::setTraceCallback(TraceCallback cb) {
	vassert(cb && "Unsubscribing is not supported");
	vassert(!traceCallback && "Multiple subscriptions for the same event are not supported");
	vassert((state == State::Idle) && "Callbacks should be registered before running the agent");

	traceCallback = cb;
}


const RoutingId& ZmqAgent::routingId() const {
	return id;
}


ExporterType ZmqAgent::getWorkerType() const {
	return workerType;
}


bool ZmqAgent::isStopped() const {
	return state == State::Stopped;
}


void ZmqAgent::run(const std::string& endpoint, const ZmqTimeouts& timeoutSettings) {

	vassert(state == State::Idle && "Cannot run the same ZmqAgent twice");

	timeouts = timeoutSettings;
	state = State::Running;
	pollerThread = std::move(std::thread(&ZmqAgent::pollerLoop, this, endpoint));
}


void ZmqAgent::stop(bool block) {

	State expectedState = State::Running;

	if (state.compare_exchange_strong(expectedState, State::Stopping)) {
		trace("Stopping...");
	}

	if (block){
		if (state == State::Stopping) {
			vassert(std::this_thread::get_id() != pollerThread.get_id() && "Calling blocking stop() from callback thread, deadlocked.");
			trace("Waiting for poller thread to exit");

			if (pollerThread.joinable()) {
				pollerThread.join();
			}
		}
		else {
			state = State::Stopped;
		}
	}
}


void ZmqAgent::send(zmq::message_t&& payload, ControlMessage msgType /*=ControlMessge::DATA*/) {

	vassert((state != State::Idle) && "Agent should be started before data can be sent.");

	if (state == State::Running) {
		auto msg = createMsg(msgType, std::move(payload));

		std::lock_guard<std::mutex> lock(queueMutex);
		msgQueue.push_back(std::move(msg));
	}
}


/// This is the main 
/// @param endpoint - the endpoint to connect to
void ZmqAgent::pollerLoop(std::string endpoint) {

	// stop() may have been called before the thread has managed to start. Short of killing the application,
	// there is no convenient way to handle stop requests during the connection initialization sequence, so
	// just wait for it to finish and then immediately exit the message processing loop.
	vassert((state == State::Running) || (state == State::Stopping));

	trace(Msg("Connecting to", endpoint));

	try {
		// ZMQ socket cannot be shared between threads.
		// Keep it as a local variable to avoid confusion.
		auto sock = connect(endpoint, id);

		// Initialize activity monitor
		lastActivity = Clock::now();

		// Perform handshake synchronously
		handshake(sock);

		zmq::poller_t<> pollerRecv, pollerSend;

		pollerRecv.add(sock, zmq::event_flags::pollin);
		pollerSend.add(sock, zmq::event_flags::pollout);

		// Try to send all outstanding messages. In case of connection loss,
		// an error will be received from ZmqRouter and the poller loop will exit.
		while ((state == State::Running) || !msgQueue.empty()) {
			sendPending(pollerSend);
			recvPending(pollerRecv);
		}
	}
	catch (const ZmqException& e) {
		trace(Msg("ZmqException in agent poller loop:", e.what()));
	}
	catch (const std::exception& e) {
		if (state == State::Running) {
			reportError(Msg("std::exception in agent poller loop:", e.what()));
		}
		else {
			trace(Msg("std::exception in agent poller loop:", e.what()));
		}
	}

	trace("Exit poller loop");

	state = State::Stopped;

	msgCallback   = nullptr;
	errorCallback = nullptr;
	traceCallback = nullptr;

}


/// Send up to MAX_BATCH messages from the message queue in succession
void ZmqAgent::sendPending(zmq::poller_t<>& pollerSend) {

	static const auto DONT_BLOCK = 0ms;
	static const size_t MAX_BATCH = 200;

	std::vector<zmq::poller_event<>> events(1);
	zmq::multipart_t* items[MAX_BATCH];

	size_t total = 0;

	{
		// Don't hold the lock for the duration of the send loop, because
		// this may block senders from queueing messages in case of slow sends on the socket.
		// Pushing into the deque is guaranteed to not invalidate references to items.
		std::lock_guard<std::mutex> lock(queueMutex);
		total = std::min(msgQueue.size(), MAX_BATCH);
		for (int i = 0; i < total; ++i) {
			items[i] = &msgQueue[i];
		}
	}

	size_t sent = 0;

	while ((sent < total) && (1 == pollerSend.wait_all(events, DONT_BLOCK))) {

		vassert(zmq::event_flags::pollout == events[0].events);

		auto* msg = items[sent];
		auto& sockOut = events[0].socket;

		[[maybe_unused]] auto res = msg->send(sockOut);

		// For blocking sockets, any failure is reported as an exception
		vassert(res && "EAGAIN on a blocking socket");

		++sent;
	}

	{
		std::lock_guard<std::mutex> lock(queueMutex);

		for (int i = 0; i < sent; ++i) {
			msgQueue.pop_front();
		}
	}
}


/// Receive and dispatch a message 
void ZmqAgent::recvPending(zmq::poller_t<>& pollerRecv) {

	static const auto pollTimeout = 1ms;
	std::vector<zmq::poller_event<>> events(1);

	const auto numMessages = pollerRecv.wait_all(events, pollTimeout);
	if (numMessages == 0) {
		processTick();
		return;
	}

	// Processing a message might take some time and might block sends, so process only the first
	// available message
	zmq::multipart_t msg(events[0].socket);

	CHECK_ZMQ(msg.size() == 2, "Wrong message format: expecting 2 frames");

	const auto& msgType = *msg[0].data<ControlMessage>();
	auto& payload = msg[1];

	switch (msgType) {
	case ControlMessage::DATA:
		if (msgCallback) {
			msgCallback(std::move(payload));
		}
		break;

	case ControlMessage::PING:
		sendPingMsg(PONG);
		break;

	case ControlMessage::PONG:
		// Just register activity
		break;

	case ControlMessage::ERR:
		throw ZmqException(payload.to_string());
		break;

	default:
		vassert(!"Invalid message type");
	}

	lastActivity = Clock::now();
}


/// Connect the socket to the specified address
/// @param addr - endpoint to connect to
/// @param routingId - the routing id of the socket
zmq::socket_t ZmqAgent::connect(const std::string& addr, RoutingId routingId) {
	zmq::socket_t sock(ctx, ZMQ_DEALER);

	sock.set(zmq::sockopt::routing_id, zmq::buffer(&routingId, sizeof(routingId)));
	sock.set(zmq::sockopt::connect_timeout, timeouts.connect);
	sock.set(zmq::sockopt::sndtimeo, timeouts.send);
	sock.set(zmq::sockopt::rcvtimeo, timeouts.recv);
	
	// Do not try to send outstanding data after close() has been called on the socket
	sock.set(zmq::sockopt::linger, 0);	

	// Setting the high watermark to a non-zero value 
	// will allow the send operation to report EHOSTUNREACH errors
	sock.set(zmq::sockopt::sndhwm, 0);

	sock.connect(addr);
	return sock;
}


/// Perform a synchronous handshake. Exceptions should be handled by the caller
/// @param sock - an already connected ZMQ socket
void ZmqAgent::handshake(zmq::socket_ref sock) {

	if (isClient) {
		trace("Handshake initiate");

		auto msgOut = createMsg(ControlMessage::CONNECT, toZmqMsg(HandshakeMsg{ZMQ_PROTOCOL_VERSION, static_cast<int>(workerType)}));
		
		msgOut.send(sock);

		// Synchronously wait for the response from server for the specified handshake timeout
		// checking periodically for stop() being called.

		TimePoint end = Clock::now() + std::chrono::milliseconds(timeouts.handshake);
		zmq::multipart_t response;

		do {
			response.recv(sock);
		} 
		while ((state == State::Running) && (response.size() == 0) && (Clock::now() < end));

		if (state != State::Running) {
			return;
		}

		CHECK_ZMQ(response.size() != 0, "Handshake response timed out");
		CHECK_ZMQ(response.size() == 2, "Invalid handshake response: 2 frames expected");

		const auto msgType = *response[0].data<ControlMessage>();
		const auto version = *response[1].data<Version>();

		CHECK_ZMQ(version == ZMQ_PROTOCOL_VERSION, Msg("Wrong protocol version", version));

		CHECK_ZMQ(msgType != ControlMessage::ERR, "Peer encountered an error");
		CHECK_ZMQ(msgType == ControlMessage::CONNECTED, "Wrong handshake message type");

		trace("Handshake complete");
	}
	else {
		trace("Handshake reply");

		// Reply to the handshake
		auto msgOut = createMsg(ControlMessage::CONNECTED, toZmqMsg(ZMQ_PROTOCOL_VERSION));

		msgOut.send(sock);
	}
}


/// Called once on each poll cycle to handle heartbeats 
void ZmqAgent::processTick() {

	if (timeouts.ping == NO_PING) {
		return;
	}

	auto elapsedActivity = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - lastActivity);

	if ((timeouts.inactivity != HEARTBEAT_NO_TIMEOUT) && (timeouts.inactivity < elapsedActivity.count())) {
		throw ZmqException(Msg("Heartbeat timeout limit has been reached, inactive for", elapsedActivity.count(), "ms"));
	}

	auto elapsedPing = std::chrono::duration_cast<std::chrono::milliseconds>(Clock::now() - lastPing);

	if ((timeouts.ping < elapsedPing.count()) && (timeouts.ping < elapsedActivity.count())) {
		sendPingMsg(PING);
		lastPing = Clock::now();
	}
}


/// Send a heartbeat message
/// @param ping - true for PING, false for PONG
void ZmqAgent::sendPingMsg(bool ping) {
	trace(Msg("Send", (ping ? "PING" : "PONG")));

	auto msgID = ping ? ControlMessage::PING : ControlMessage::PONG;
	send(zmq::message_t(), msgID);
}


/// Invoke the error callback, if subscribed to
/// @param errMsg - the error message
void ZmqAgent::reportError(const std::string& errMsg) {

	// The error callback may be called from within catch blocks, so we cannot
	// allow exceptions to escape
	try {
		if (errorCallback) {
			errorCallback(errMsg);
		}
	}
	catch (...) {
		vassert(!"Exception in error handler");
	}
}


/// Invoke the trace callback, if subscribed to
/// @param errMsg - the error message
void ZmqAgent::trace(const std::string& errMsg) {

	// The trace callback may be called from within catch blocks, so we cannot
	// allow exceptions to escape
	try {
		if (traceCallback) {
			traceCallback(errMsg);
		}
	}
	catch (...) {
		vassert(!"Exception in trace callback");
	}
}

}; // end VrayZmqWrapper namespace 