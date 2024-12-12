// Copyright (c) 2023, Chaos Software Ltd

#pragma once

#include <atomic>
#include <condition_variable>
#include <chrono>
#include <memory>
#include <mutex>
#include <zmq_agent.h>
#include <zmq_router.h>

/// This is a wrapper around std::condition_variable.
/// Performs a blocking wait for a notification from another thread
class ConditionalWait {
	ConditionalWait(const ConditionalWait&) = delete;
	ConditionalWait& operator=(const ConditionalWait&) = delete;

	using Clock = std::chrono::steady_clock;
	using TimePoint = std::chrono::time_point<Clock>;

public:
	ConditionalWait() = default;
	
	/// Wait for a notification for the specified period.
	/// @return true if wait is successful, false if timed out
	bool wait(const  std::chrono::milliseconds& timeout) {
		TimePoint end = Clock::now() + timeout;

		std::unique_lock<std::mutex> lock(mut);

		while (!complete) {
			const auto timeLeft = end - Clock::now();
			if (timeLeft <= std::chrono::milliseconds(0)){
				return false;
			}

			cond.wait_for(lock, timeLeft);
		}

		return true;
	}

	/// Notifies that the condition is fulfilled
	void notify() {
		{
			std::lock_guard<std::mutex> lock(mut);
			complete = true;
		}

		cond.notify_one();
	}

private:
	std::mutex mut;
	std::condition_variable cond;
	bool complete = false;
};


using ZmqRouterPtr = std::unique_ptr<VrayZmqWrapper::ZmqRouter>;
using ZmqAgentPtr = std::unique_ptr<VrayZmqWrapper::ZmqAgent>;

/// This flag will enable traces from agent and router. Only uUse to debug failing tests.
static const bool WITH_TRACES_ON = false;

void setUpTracing(ZmqAgentPtr& client, ZmqRouterPtr& router);