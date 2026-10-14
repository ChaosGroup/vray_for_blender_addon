// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "tools.hpp"
#include <iostream>



static std::mutex mutexTrace;


void setUpTracing(ZmqAgentPtr& client, ZmqRouterPtr& router) {

if (WITH_TRACES_ON) {
		router->setTraceCallback([](const std::string& msg) {
			std::scoped_lock lock(mutexTrace);
			std::cout << "ROUTER TRACE: " << msg << std::endl;
		});

		client->setTraceCallback([](const std::string& msg) {
			std::scoped_lock lock(mutexTrace);
			std::cout << "CLIENT TRACE: " << msg << std::endl;
		});
	}
}


std::string runRouterEphemeral(ZmqRouterPtr& router, const std::string& workerEndpoint, const VrayZmqWrapper::ZmqTimeouts& timeouts) {
	ConditionalWait started;
	std::string clientEndpoint;

	router->setStartedCallback([&started, &clientEndpoint](int port) {
		clientEndpoint = "tcp://127.0.0.1:" + std::to_string(port);
		started.notify();
	});

	router->run("tcp://127.0.0.1:0", workerEndpoint, timeouts);
	started.wait(std::chrono::milliseconds(3000));

	return clientEndpoint;
}