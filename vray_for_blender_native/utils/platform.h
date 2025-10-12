// Platform-specific definitions

#pragma once


#include <ctime>

namespace platform
{
	using ProcessIdType = unsigned long;
	void allowSetForegroundWindow(ProcessIdType processId);
	bool gmtime(const std::time_t& time, std::tm& out);
}