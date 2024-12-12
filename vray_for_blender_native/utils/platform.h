// Platform-specific definitions

#pragma once

#ifdef _WIN32 // Windows
	#include <Windows.h>
#endif



namespace platform
{

#ifdef _WIN32 // Windows
	using ProcessIdType = DWORD;
#endif

	void allowSetForegroundWindow(ProcessIdType processId);
}