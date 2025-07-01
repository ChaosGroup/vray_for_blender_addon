#include "zmq_router.h"
#include "zmq_agent.h"

#include <vector>

#include "cppzmq/zmq.hpp"
#include "cppzmq/zmq_addon.hpp"
#include "vassert.h"


 ////////////////////////////////////////////////////////////////////////////////
 /// ZmqRouter is a wrapper around ZMQ Ryter socket implementing the
 /// communication protocol between Blender and Vray ZmqServer. It is
 /// designed for usage in the following pattern:
 ///
 ///     ZmqAgent(Blender) <--> ZmqRouter <--> ZmqAgent(RendererController)
 /// 
 ////////////////////////////////////////////////////////////////////////////////

namespace VrayZmqWrapper{

/// Constructor
/// @param ctx - initialized ZMQ context 
ZmqRouter::ZmqRouter(zmq::context_t& ctx) :
	ctx(ctx)
{
}


ZmqRouter::~ZmqRouter() {

	try {
		stop();

		if (pollerThread.joinable()) {
			pollerThread.join();
		}
	} 
	catch(const std::exception& exc) {
		trace(Msg("Exception in ZmqRouter's destructor:", exc.what()));
		vassert(!"Exception in ZmqRouter's destructor.");
	}
}


void ZmqRouter::setNewWorkerCallback(NewWorkerCallback cb) {
	vassert(cb && "Unsubscribing is not supported");
	vassert(!newWorkerCallback && "Multiple subscriptions are not supported");
	vassert(!pollerThread.joinable() && "Callbacks should be registered before the router is run");
	
	newWorkerCallback = cb;
}


void ZmqRouter::setStartedCallback(StartedCallback cb) {
	vassert(cb && "Unsubscribing is not supported");
	vassert(!startedCallback && "Multiple subscriptions are not supported");
	vassert(!pollerThread.joinable() && "Callbacks should be registered before the router is run");

	startedCallback = cb;
}


void ZmqRouter::setErrorCallback(ErrorCallback cb) {
	vassert(cb && "Unsubscribing is not supported");
	vassert(!errorCallback && "Multiple subscriptions are not supported");
	vassert(!pollerThread.joinable() && "Callbacks should be registered before the router is run");
	
	errorCallback = cb;
}


void ZmqRouter::setTraceCallback(TraceCallback cb) {
	vassert(cb && "Unsubscribing is not supported");
	vassert(!traceCallback && "Multiple subscriptions are not supported");
	vassert(!pollerThread.joinable() && "Callbacks should be registered before the router is run");

	traceCallback = cb;
}


void ZmqRouter::run(const std::string& clientEndpoint, const std::string& workerEndopint, const ZmqTimeouts& timeoutSettings) {
	vassert(!clientEndpoint.empty() && !workerEndopint.empty());
	vassert(!pollerThread.joinable() && "Cannot run the same ZmqRouter twice");
	vassert(newWorkerCallback && "NewWorkerCallback should be set before the router is run");

	timeouts = timeoutSettings;
	pollerThread = std::move(std::thread(&ZmqRouter::pollerLoop, this, clientEndpoint, workerEndopint));
}


/// Signal the poller loop to stop. This method will not block.
void ZmqRouter::stop() {

	trace("Stop signaled");
	stopPolling = true;
}


void ZmqRouter::pollerLoop(std::string clientEndpoint, std::string workerEndopint) {

	static const auto pollTimeout = std::chrono::milliseconds(100);

	try {
		sockClient = bind(clientEndpoint);
		sockServer = bind(workerEndopint);

		notifyStarted(sockClient);
		
		trace(Msg("Listening for client connections on", clientEndpoint));
		trace(Msg("Listening for worker connections on", workerEndopint));
		
		std::vector<zmq::poller_event<>> events(2);
		zmq::poller_t<> pollerRecv, pollerSend;
		
		pollerRecv.add(sockClient, zmq::event_flags::pollin);
		pollerRecv.add(sockServer, zmq::event_flags::pollin);
		pollerSend.add(sockClient, zmq::event_flags::pollout);
		pollerSend.add(sockServer, zmq::event_flags::pollout);
		
		while (!stopPolling)	{
			const auto recvEvents = pollerRecv.wait_all(/*r*/events, pollTimeout);

			for (int i = 0; i < recvEvents; ++i) {
				auto& sock = events[i].socket;

				zmq::multipart_t msg(sock);

				if(msg.size() != 3){
					trace("Wrong message format");
					continue;
				}

				const auto& msgType = *msg[1].data<ControlMessage>();

				switch(msgType) {
					case ControlMessage::CONNECT:
					{
						processHandshake(sock, msg);
						break;
					}

					default:
						// Relay the message to the peer of the receiving socket
						relayMsg(sock, msg);
						break;
				}
			}
		}
	}
	catch (const zmq::error_t& e) {
		reportError(Msg("Exception in poller loop:", e.what()));
	}

	trace("Exit poller loop");
}


/// Create and bind a socket
/// @param endpoint - The 'listen' endpoint
/// @return The created socket
zmq::socket_t ZmqRouter::bind(const std::string& endpoint) {
	zmq::socket_t sock(ctx, ZMQ_ROUTER);

	sock.set(zmq::sockopt::sndtimeo, timeouts.send);
	sock.set(zmq::sockopt::rcvtimeo, timeouts.recv);
	sock.set(zmq::sockopt::router_mandatory, 1);  // Reply with E_UNREACHABLE when route not found
	sock.set(zmq::sockopt::sndhwm, 0); // Disable high watermark (available memory will be the limit)
	sock.bind(endpoint);

	return sock;
}


/// Process a CONNECT message from the client. 
/// @param sock - the connecting socket
/// @param msg - the payload of the CONNECT message
void ZmqRouter::processHandshake(zmq::socket_ref sock, const zmq::multipart_t& msg) {
	vassert(newWorkerCallback);

	if (sock.handle() != sockClient.handle()) {
		trace("Invalid message received from worker: CONNECT. Only clients can send this message.");
		return;
	}

	const auto routingId    = *msg[0].data<RoutingId>();
	const auto handshakeMsg = *msg[2].data<HandshakeMsg>();
	
	trace(Msg("Client", shorten(routingId), ", renderer type", handshakeMsg.workerType, "CONNECT"));

	if (handshakeMsg.protoVersion != ZMQ_PROTOCOL_VERSION) {
		const auto errMsg = Msg("Wrong protocol version, expected", ZMQ_PROTOCOL_VERSION);

		trace(errMsg);
		replyWithError(sock, routingId, errMsg);
		return;
	}

	// Create a new worker and hand it out. The socket is created with the same ID as that 
	// of the requesting socket
	try {
		auto worker = std::make_unique<ZmqAgent>(ctx, routingId, static_cast<ExporterType>(handshakeMsg.workerType), ZmqAgent::Worker);
		newWorkerCallback(std::move(worker));
	}
	catch (const std::exception& exc) {
		// Something is wrong with the peer, but we can't do anything about it 
		const auto errMsg = Msg("New worker creation failed", shorten(routingId), ", exception:", exc.what());
		trace(errMsg);
		replyWithError(sock, routingId, errMsg);
	}
}


/// Relay a message received on a socket to its peer
/// @param fromSock - the socket on which the message was received
/// @param msg - the message received on the socket
void ZmqRouter::relayMsg(zmq::socket_ref fromSock, zmq::multipart_t& msg)
{
	const auto routingId = *msg[0].data<RoutingId>();
	const bool isFromSvr = (fromSock.handle() == sockServer.handle());

	try {
		msg.send(isFromSvr ? sockClient : sockServer);
	}
	catch (const std::exception& exc) {
		// Something is wrong with the peer, but we can't do anything about it 
		const auto errMsg = Msg("Failed to relay message to", isFromSvr ? "client" : "server", shorten(routingId), ", exception:", exc.what());
		trace(errMsg);
		replyWithError(fromSock, routingId, errMsg);
	}
}


/// When an error occurs on a connection, this method will notify the peer of the failed agent. 
/// @param toSock - the peer's socket
/// @param routingId - the routing ID of the tunnel
/// @param errMsg - a textual description of the error
void ZmqRouter::replyWithError(zmq::socket_ref toSock, const RoutingId& routingId, const std::string& errMsg) {

	try {
		zmq::multipart_t msg;
		msg.add(toZmqMsg(routingId));
		msg.add(toZmqMsg(ControlMessage::ERR));
		msg.add(toZmqMsg(errMsg));

		msg.send(toSock);
	}
	catch (const std::exception& exc) {
		trace(Msg("Failed to send error reply to", shorten(routingId), ",exception", exc.what()));
	}
}


/// Invoke the error callback, if subscribed to
/// @param errMsg - a description of the error
void ZmqRouter::reportError(const std::string& errMsg) const {

	// The error callback is called from within catch blocks, so we cannot
	// allow it to throw exceptions
	try {
		if (errorCallback) {
			errorCallback(errMsg);
		}
	}
	catch (...) {
		vassert(!"Exception in error handler");
	}
}



/// Invoke the debug trace callback, if subscribed to
/// @param msg - the message to trace
void ZmqRouter::trace(const std::string& msg) {

	// The error callback is called from within catch blocks, so we cannot
	// allow it to throw exceptions
	try {
		if (traceCallback) {
			traceCallback(msg);
		}
	}
	catch (...) {
		vassert(!"Exception in trace handler");
	}
}


void ZmqRouter::notifyStarted(zmq::socket_ref clientListenSocket) {

	try { 
		if (startedCallback) {
			// Client listen socket might be bound on an ephemeral port. 
			// Notify the host about the actual port number.
			const std::string endpoint = clientListenSocket.get(zmq::sockopt::last_endpoint);
			const std::string port = endpoint.substr(endpoint.find_last_of(':') + 1);
			int portNum = std::atoi(port.c_str());

			startedCallback(portNum);
		}
	}
	catch (...) {
		vassert(!"Exception in 'started' handler");
	}
}


}; // end VrayZmqWrapper namespace



