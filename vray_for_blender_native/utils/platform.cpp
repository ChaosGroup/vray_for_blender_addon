#include "platform.h"

namespace platform
{
	void allowSetForegroundWindow(ProcessIdType processId)
	{
#ifdef WIN32
		::AllowSetForegroundWindow(processId);
#endif
	}
}