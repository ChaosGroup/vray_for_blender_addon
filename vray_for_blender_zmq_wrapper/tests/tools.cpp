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