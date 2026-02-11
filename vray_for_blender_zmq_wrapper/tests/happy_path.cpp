// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <catch_amalgamated.hpp>

#include <future>
#include <iostream>
#include <thread>
#include <mutex>


#include "zmq_agent.h"
#include "zmq_router.h"
#include "tools.hpp"

using namespace std::chrono_literals;
using namespace VrayZmqWrapper;

namespace {
	zmq::context_t ctx;
	auto timeouts      = ZmqTimeouts();
	const RoutingId clientId = 1;
	const int workerType = 1;
	const std::string clientEndpoint = "tcp://127.0.0.1:5556";
	const std::string workerEndpoint = "inproc://test_router";
}

using ZmqAgentPtr = std::unique_ptr<ZmqAgent>;




TEST_CASE("Actor creation / destruction")
{
	SECTION("Agent create") {
		auto agent = std::make_unique<ZmqAgent>(ctx, clientId, workerType, true);
		REQUIRE(agent->routingId() == clientId);
		REQUIRE_FALSE(agent->isStopped());

		REQUIRE_NOTHROW(agent.reset());
	}

	SECTION("Idle agent sync stop") {
		auto agent = std::make_unique<ZmqAgent>(ctx, clientId, workerType, true);
		agent->stop(true);

		REQUIRE(agent->isStopped());
	}

	SECTION("Agent run/stop") {
		auto agent = std::make_unique<ZmqAgent>(ctx, clientId, workerType, true);
		agent->run(clientEndpoint, timeouts);

		REQUIRE_NOTHROW(agent.reset());
	}

	SECTION("Router create") {
		auto router = std::make_unique<ZmqRouter>(ctx);
		REQUIRE_NOTHROW(router.reset());
	}

	SECTION("Router run/stop") {
		auto router = std::make_unique<ZmqRouter>(ctx);
		router->run(clientEndpoint, workerEndpoint, timeouts);

		REQUIRE_NOTHROW(router.reset());
	}
}


TEST_CASE("Basic communication") {

	auto router = std::make_unique<ZmqRouter>(ctx);
	auto client = std::make_unique<ZmqAgent>(ctx, clientId, workerType, true);
	const std::string msg = "A message";
	ConditionalWait notifier;
	setUpTracing(client, router);
	
	SECTION("Client connect") {

		bool connected = false;
		router->setNewWorkerCallback([&connected, &notifier](auto worker) {
			connected = true;
			notifier.notify();
		});

		router->run(clientEndpoint, workerEndpoint, timeouts);
		client->run(clientEndpoint, timeouts);
		notifier.wait(1s);
		REQUIRE(connected);
	}

	SECTION("Worker slow handshake") {
		// This is not exactly a component test, but it covers a common use-case for worker creation
		bool msgReceived = false;
		std::future<void> startResult;
		ZmqAgentPtr worker;

		router->setNewWorkerCallback([&worker, &msgReceived, &notifier, &startResult](auto agent) {
			worker = std::move(agent);

			startResult = std::async(std::launch::async, [&worker, &notifier, &msgReceived]() {
				
				std::this_thread::sleep_for(1s);

				worker->setMsgCallback([&msgReceived, &notifier](zmq::message_t&& /*payload*/) {
					msgReceived = true;
					notifier.notify();
				});

				worker->setErrorCallback([&msgReceived](std::string errMsg) {
					FAIL(std::string("Worker error callback: ") + errMsg);
				});

				worker->run(workerEndpoint, timeouts);
			});
		});

		router->run(clientEndpoint, workerEndpoint, timeouts);
		std::this_thread::sleep_for(500ms);

		// Set the inactivity timeout to less than the time it takes to strat the worker
		ZmqTimeouts tmouts;
		tmouts.ping = 200;
		tmouts.inactivity = 1000;
		tmouts.handshake = 7000;

		client->run(clientEndpoint, tmouts);
		
		// The message will be sent after the handshake is complete
		zmq::message_t payload(msg);
		client->send(std::move(payload));

		// Wait for 
		notifier.wait(7s);
		startResult.wait();
		REQUIRE(msgReceived);
	}


	SECTION("Message sent from client and received by worker") {
		/*
		* Client sends a one-way message to the worker
		*/
		bool msgReceived = false;
		ZmqAgentPtr worker;

		router->setNewWorkerCallback([&worker, &msgReceived, &msg, &notifier](auto agent) {
			worker = std::move(agent);
			
			worker->setMsgCallback([&msgReceived, &msg, &notifier](zmq::message_t&& payload) {
				REQUIRE(payload.to_string() == msg);
				msgReceived = true;
				notifier.notify();
			});

			worker->setErrorCallback([&msgReceived](std::string errMsg) {
				FAIL(std::string("Worker error callback: ") + errMsg);
			});

			worker->run(workerEndpoint, timeouts);

		});

		client->setErrorCallback([&msgReceived](std::string errMsg) {
			FAIL(std::string("Client error callback: " + errMsg));
		});

		router->run(clientEndpoint, workerEndpoint, timeouts);
		std::this_thread::sleep_for(500ms);

		client->run(clientEndpoint, timeouts);
		
		zmq::message_t payload(msg);
		client->send(std::move(payload));
		
		notifier.wait(5s);
		REQUIRE(msgReceived);
	}


	SECTION("Message roundtrip") {
		/* 
		* Client sends a message to the worker and receives back the same message
		*/
		bool msgReceived = false;
		ZmqAgentPtr worker;

		router->setNewWorkerCallback([&worker, &msgReceived, &msg, &notifier](auto agent) {
			worker = std::move(agent);

			worker->setMsgCallback([&msgReceived, &msg, &worker](zmq::message_t&& payload) {
				REQUIRE(payload.to_string() == msg);
				worker->send(std::move(payload));
			});

			worker->setErrorCallback([&msgReceived](std::string errMsg) {
				FAIL(std::string("Worker error callback: ") + errMsg);
			});

			worker->run(workerEndpoint, timeouts);

		});

		client->setErrorCallback([&msgReceived, &msg](std::string errMsg) {
			FAIL(std::string("Client error callback: " + errMsg));
		});

		client->setMsgCallback([&msgReceived, &msg, &notifier](zmq::message_t&& payload) {
			REQUIRE(payload.to_string() == msg);
			msgReceived = true;
			notifier.notify();
		});

		router->run(clientEndpoint, workerEndpoint, timeouts);
		std::this_thread::sleep_for(500ms);

		client->run(clientEndpoint, timeouts);
		
		zmq::message_t payload(msg);
		client->send(std::move(payload));

		notifier.wait(5s);
		REQUIRE(msgReceived);
	}
}

