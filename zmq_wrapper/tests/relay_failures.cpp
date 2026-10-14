// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <catch_amalgamated.hpp>

#include <atomic>
#include <iostream>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

#include "zmq_agent.h"
#include "zmq_router.h"
#include "tools.hpp"

using namespace std::chrono_literals;
using namespace VrayZmqWrapper;

namespace {
	zmq::context_t ctx;
	const RoutingId agentId = 4242;
	const ExporterType workerType = ExporterType::IPR_VFB;
	const std::string workerEndpoint = "inproc://test_relay_failures";

	// Enough messages for a broken tunnel to fail many times before the sender reads the error
	const int BURST_SIZE = 1000;

	const std::string FAILED_TO_SERVER = "Failed to relay message to server";
	const std::string FAILED_TO_CLIENT = "Failed to relay message to client";


	/// Disable heartbeats, so that agent errors can only come from failed relays
	ZmqTimeouts noHeartbeat() {
		ZmqTimeouts timeouts;
		timeouts.ping       = NO_PING;
		timeouts.inactivity = HEARTBEAT_NO_TIMEOUT;
		return timeouts;
	}


	/// Poll for a condition until it is fulfilled or the timeout expires
	/// @return true if the condition was fulfilled
	template <class Pred>
	bool waitFor(Pred pred, std::chrono::milliseconds timeout) {
		const auto end = std::chrono::steady_clock::now() + timeout;

		while (!pred()) {
			if (std::chrono::steady_clock::now() >= end) {
				return false;
			}
			std::this_thread::sleep_for(10ms);
		}
		return true;
	}


	/// Thread-safe list of strings, received from callbacks
	class MsgLog {
	public:
		void add(const std::string& msg) {
			std::scoped_lock lock(mutex);
			msgs.push_back(msg);
		}

		size_t size() const {
			std::scoped_lock lock(mutex);
			return msgs.size();
		}

		/// Number of messages containing a substring
		size_t count(const std::string& substr) const {
			std::scoped_lock lock(mutex);
			size_t result = 0;
			for (const auto& msg : msgs) {
				result += (msg.find(substr) != std::string::npos) ? 1 : 0;
			}
			return result;
		}

	private:
		mutable std::mutex mutex;
		std::vector<std::string> msgs;
	};


	/// Queue a burst of messages to send through an agent
	void sendBurst(ZmqAgent& agent) {
		for (int i = 0; i < BURST_SIZE; ++i) {
			agent.send(zmq::message_t(std::string("burst")));
		}
	}


	/// Create an agent which logs its errors
	ZmqAgentPtr createAgent(bool isClient, MsgLog& errors) {
		auto agent = std::make_unique<ZmqAgent>(ctx, agentId, workerType, isClient);

		agent->setMsgCallback([](zmq::message_t&& /*payload*/) {});
		agent->setErrorCallback([&errors](const std::string& msg) {
			errors.add(msg);
		});

		return agent;
	}


	/// A router which runs the workers it creates and records the traces and errors of both
	class RouterFixture {
	public:
		RouterFixture() : router(std::make_unique<ZmqRouter>(ctx)) {
			router->setTraceCallback([this](const std::string& msg) {
				traces.add(msg);
				if (WITH_TRACES_ON) {
					std::cout << "ROUTER TRACE: " << msg << std::endl;
				}
			});

			router->setErrorCallback([this](std::string /*msg*/) {
				routerError = true;
			});

			router->setNewWorkerCallback([this](ZmqAgentPtr agent) {
				agent->setMsgCallback([this](zmq::message_t&& /*payload*/) {
					++workerMsgs;
				});

				agent->setErrorCallback([this](const std::string& msg) {
					workerErrors.add(msg);
				});

				agent->run(workerEndpoint, noHeartbeat());

				std::scoped_lock lock(workerMutex);
				worker = std::move(agent);
			});
		}

		/// Run the router and return the endpoint for the clients
		std::string run() {
			return runRouterEphemeral(router, workerEndpoint, noHeartbeat());
		}

		/// Connect a client and wait until a message reaches the worker created for it
		bool connect(ZmqAgent& client, const std::string& endpoint) {
			workerMsgs = 0;

			client.run(endpoint, noHeartbeat());
			client.send(zmq::message_t(std::string("hello")));

			return waitFor([this] { return workerMsgs > 0; }, 5s);
		}

		/// Disconnect the current worker, breaking the tunnel
		void resetWorker() {
			ZmqAgentPtr agent;
			{
				std::scoped_lock lock(workerMutex);
				agent = std::move(worker);
			}
			agent.reset();
		}

		/// Queue a burst of messages to send through the current worker
		void sendBurstFromWorker() {
			std::scoped_lock lock(workerMutex);
			sendBurst(*worker);
		}

	public:
		MsgLog traces;                      ///< Router traces
		MsgLog workerErrors;                ///< Errors of the workers created by the router
		std::atomic_bool routerError = false;
		std::atomic_int workerMsgs = 0;

	private:
		std::mutex workerMutex;
		ZmqAgentPtr worker;                 ///< The last worker created by the router

	public:
		// Declared last so that it is destroyed first, before the state its callbacks use
		ZmqRouterPtr router;
	};
}


TEST_CASE("Relay failure deduplication") {

	RouterFixture fixture;

	SECTION("Repeated failures are reported once") {
		/*
		* The worker disconnects and the client keeps sending. Each message fails to relay,
		* but only the first failure is traced and replied to.
		*/
		MsgLog clientErrors;
		auto client = createAgent(ZmqAgent::Client, clientErrors);

		const std::string endpoint = fixture.run();
		REQUIRE(fixture.connect(*client, endpoint));

		fixture.resetWorker();
		std::this_thread::sleep_for(200ms);  // Let the router drop the worker's connection

		sendBurst(*client);

		REQUIRE(waitFor([&] { return clientErrors.size() > 0; }, 5s));
		std::this_thread::sleep_for(300ms);  // Let the rest of the burst reach the router

		REQUIRE(fixture.traces.count(FAILED_TO_SERVER) == 1);
		REQUIRE(clientErrors.size() == 1);
		REQUIRE(clientErrors.count(FAILED_TO_SERVER) == 1);
		REQUIRE_FALSE(fixture.routerError);
	}


	SECTION("A reconnect with the same ID is reported again") {
		/*
		* A client reconnects with the routing ID of a failed tunnel, as the control connection
		* does. The failures of the new tunnel should be reported, although the failures of the
		* old one are still within their grace period.
		*/
		const std::string endpoint = fixture.run();

		MsgLog clientErrors1;
		auto client1 = createAgent(ZmqAgent::Client, clientErrors1);

		REQUIRE(fixture.connect(*client1, endpoint));

		fixture.resetWorker();
		std::this_thread::sleep_for(200ms);

		sendBurst(*client1);
		REQUIRE(waitFor([&] { return clientErrors1.size() > 0; }, 5s));
		REQUIRE(fixture.traces.count(FAILED_TO_SERVER) == 1);

		const auto firstFailure = std::chrono::steady_clock::now();

		client1.reset();
		std::this_thread::sleep_for(500ms);  // Let the router release the routing ID

		MsgLog clientErrors2;
		auto client2 = createAgent(ZmqAgent::Client, clientErrors2);

		REQUIRE(fixture.connect(*client2, endpoint));

		fixture.resetWorker();
		std::this_thread::sleep_for(200ms);

		sendBurst(*client2);

		REQUIRE(waitFor([&] { return clientErrors2.size() > 0; }, 5s));
		REQUIRE(clientErrors2.count(FAILED_TO_SERVER) == 1);
		REQUIRE(fixture.traces.count(FAILED_TO_SERVER) == 2);
		REQUIRE_FALSE(fixture.routerError);

		// The old failure must not have expired, otherwise the reset on CONNECT is not what was tested
		REQUIRE(std::chrono::steady_clock::now() - firstFailure < ZmqRouter::RELAY_FAILURE_GRACE);
	}


	SECTION("Failures are reported again after the grace period") {
		/*
		* The client disconnects and the worker keeps sending. After the grace period has expired,
		* the same failure from a new worker with the same routing ID should be reported again.
		*/
		const std::string endpoint = fixture.run();

		MsgLog clientErrors;
		auto client = createAgent(ZmqAgent::Client, clientErrors);

		REQUIRE(fixture.connect(*client, endpoint));

		client.reset();
		std::this_thread::sleep_for(200ms);  // Let the router drop the client's connection

		fixture.sendBurstFromWorker();

		REQUIRE(waitFor([&] { return fixture.workerErrors.size() > 0; }, 5s));
		REQUIRE(fixture.traces.count(FAILED_TO_CLIENT) == 1);

		fixture.resetWorker();
		std::this_thread::sleep_for(ZmqRouter::RELAY_FAILURE_GRACE + 2 * ZmqRouter::RELAY_FAILURE_PURGE_INTERVAL);

		// The handshake reply of the new worker fails to relay to the client
		MsgLog workerErrors;
		auto worker = createAgent(ZmqAgent::Worker, workerErrors);
		worker->run(workerEndpoint, noHeartbeat());

		REQUIRE(waitFor([&] { return workerErrors.size() > 0; }, 5s));
		REQUIRE(workerErrors.count(FAILED_TO_CLIENT) == 1);
		REQUIRE(fixture.traces.count(FAILED_TO_CLIENT) == 2);
		REQUIRE_FALSE(fixture.routerError);
	}
}
