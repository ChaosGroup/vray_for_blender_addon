#include "platform.h"

#ifdef _WIN32
	#include <Windows.h>
#endif

namespace platform
{
	void allowSetForegroundWindow(ProcessIdType processId)
	{
#ifdef _WIN32
		::AllowSetForegroundWindow(processId);
#endif
	}

	bool gmtime(const std::time_t& time, std::tm& out)
	{
#ifdef _WIN32
		return gmtime_s(&out, &time) == 0;
#else
		return gmtime_r(&time, &out) != nullptr;
#endif
	}
}