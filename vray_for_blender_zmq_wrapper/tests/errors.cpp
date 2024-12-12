// Copyright (c) 2023, Chaos Software Ltd

#include <catch_amalgamated.hpp>

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
	const std::string clientEndpoint = "tcp://127.0.0.1:5557";
	const std::string workerEndpoint = "inproc://test_router";
	const bool WITH_TRACES_ON = false;
}

using ZmqAgentPtr = std::unique_ptr<ZmqAgent>;



TEST_CASE("Communication error conditions") {

	auto router = std::make_unique<ZmqRouter>(ctx);
	auto client = std::make_unique<ZmqAgent>(ctx, clientId, workerType, true);
	const std::string msg = "A message";
	bool connError = false;
	ConditionalWait notifier;
	
	setUpTracing(client, router);

	SECTION("Client disconnect") {
		/*
		* Client sends a message to the worker and disconnects before receiving a response
		*/

		bool msgReceived = false;
		ZmqAgentPtr worker;

		router->setNewWorkerCallback([&worker, &connError, &msg, &notifier](auto agent) {
			worker = std::move(agent);

			worker->setMsgCallback([&connError, &msg, &worker, &notifier](zmq::message_t&& payload) {
				REQUIRE(payload.to_string() == msg);
			});

			worker->setErrorCallback([&connError, &notifier](std::string msg) {
				connError = true;
				notifier.notify();
			});


			worker->run(workerEndpoint, timeouts);

		});

		client->setErrorCallback([&connError, &notifier](std::string errMsg) {
			FAIL(std::string("Client error callback: ") + errMsg);
		});

		client->setMsgCallback([&msgReceived, &msg, &notifier](zmq::message_t&& /*payload*/) {
			FAIL("Client should have been disconnected");
		});


		router->run(clientEndpoint, workerEndpoint, timeouts);
		std::this_thread::sleep_for(500ms);

		client->run(clientEndpoint, timeouts);

		zmq::message_t payload(msg);
		client->send(std::move(payload));
		std::this_thread::sleep_for(500ms);
		client.reset();

		notifier.wait(5s);
		REQUIRE(connError);
	}

	
	SECTION("Worker disconnect") {
		/*
		* Client sends a message to the worker and disconnects before receiving a response
		*/

		bool msgReceived = false;
		ZmqAgentPtr worker;

		router->setNewWorkerCallback([&worker, &connError, &msg, &notifier](auto agent) {
			worker = std::move(agent);

			worker->setMsgCallback([&connError, &msg, &worker, &notifier](zmq::message_t&& payload) {
				REQUIRE(payload.to_string() == msg);
			});

			worker->setErrorCallback([&connError, &notifier](std::string msg) {
				FAIL(std::string("Worker error callback: ") + msg);
			});

			worker->run(workerEndpoint, timeouts);

		});

		client->setErrorCallback([&connError, &notifier](std::string errMsg) {
			// This should not be a heartbeat error
			REQUIRE(std::string::npos == errMsg.find("Heartbeat"));
			connError = true;
			notifier.notify();
		});

		client->setMsgCallback([&msgReceived, &msg, &notifier](zmq::message_t&& /*payload*/) {
			FAIL("Client should have been disconnected");
		});

		router->run(clientEndpoint, workerEndpoint, timeouts);
		std::this_thread::sleep_for(500ms);

		client->run(clientEndpoint, timeouts);

		std::this_thread::sleep_for(1s);
		zmq::message_t payload(msg);
		client->send(std::move(payload));
		worker.reset();

		notifier.wait(5s);
		REQUIRE(connError);
	}
}



TEST_CASE("Heartbeat timeout error conditions") {

	auto router = std::make_unique<ZmqRouter>(ctx);
	auto client = std::make_unique<ZmqAgent>(ctx, clientId, workerType, true);
	ConditionalWait notifier;
	ZmqTimeouts hbTimeouts;
	hbTimeouts.ping = 100;
	hbTimeouts.inactivity = 500;

	setUpTracing(client, router);

	SECTION("Worker inactivity") {
		/*
		* Client sends a message to the worker. Worker blocks the message processing.
		* Client should get a heartbeat error.
		*/

		bool errorReceived = false;
		ZmqAgentPtr worker;

		router->setNewWorkerCallback([&worker, &errorReceived, &notifier, &hbTimeouts](auto agent) {
			worker = std::move(agent);

			worker->setMsgCallback([&errorReceived, &worker, &notifier](zmq::message_t&& /*payload*/) {
				// Block message processing so that a heartbeat could be missed by the client
				std::this_thread::sleep_for(1s);
			});

			worker->setErrorCallback([&errorReceived, &notifier](std::string msg) {
				FAIL(std::string("Client error callback: ") + msg);
			});

			worker->run(workerEndpoint, hbTimeouts);

			});

		client->setErrorCallback([&errorReceived, &notifier](std::string msg) {
			REQUIRE(std::string::npos != msg.find("Heartbeat"));
			errorReceived = true;
			notifier.notify();
		});

		router->run(clientEndpoint, workerEndpoint, timeouts);
		std::this_thread::sleep_for(500ms);

		client->run(clientEndpoint, hbTimeouts);

		const std::string msg = "A message";
		zmq::message_t payload(msg);
		client->send(std::move(payload));
		
		notifier.wait(5s);
		REQUIRE(errorReceived);
	}



}

