#pragma once

#ifdef _WIN32
	#include <Windows.h>
	#include <debugapi.h>
#else
	#include <signal.h>
#endif

#include <stdio.h>

// Usually, the NDEBUG preprocessor macro is used to turn off asserts. In this project however
// NDEBUG is defined in all build configurations. To avoid confusion and to make sure no other
// debug features are inadvertently turned on, use the dedicated VASSERT_ENABLED macro instead.
// Note that it deliberately the same macro that enables assert functionality in vraysdk/vassert
// so that this implementation was a drop-in replacement for the one in vraysdk.
#ifdef VASSERT_ENABLED

	inline void vassertTrap() {
	#if _WIN32
		if (IsDebuggerPresent()) {
			__debugbreak();
		} else {
			fprintf(stderr, "ASSERT: Terminating application.\n");
			abort();
		}
	#else // not _WIN32
		fprintf(stderr, "ASSERT: Terminating application.\n");
		raise(SIGTRAP)
	#endif // _WIN32
	}

	#if defined(__GNUC__)
		#define ASSERT_PRINT_POS(a) fprintf(stderr, "ASSERT failed: %s:%d, %s(), at \'%s\'\n", __FILE__, __LINE__, __func__, #a)
	#elif defined(_MSC_VER)
		#define ASSERT_PRINT_POS(a) fprintf(stderr, "ASSERT failed: %s:%d, %s(), at \'%s\'\n", __FILE__, __LINE__, __FUNCTION__, #a)
	#else
		#define ASSERT_PRINT_POS(a) fprintf(stderr, "ASSERT failed: %s:%d, at \'%s\'\n", __FILE__, __LINE__, #a)
	#endif


	// The vassert macro is deliberately the same as the one defined in vraysdk/vassert so that this
	// implememtation was a drop-in replacement for the one in vraysdk.
	#define vassert(test) \
		do {\
			if (!(test)) { ASSERT_PRINT_POS(test); vassertTrap(); } \
		} while(0)


#else // not VASSERT_ENABLED

	#define vassert(test) ((void)0)

#endif // VASSERT_ENABLED