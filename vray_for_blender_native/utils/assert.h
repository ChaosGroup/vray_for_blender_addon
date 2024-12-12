#pragma once 

#include <cassert>

// Usually, the DEBUG define is used to turn on asserts. In this project however we don't 
// ever build in debug mode. To avoid confusion and to make sure no other debug features
// are inadvertently turned on, use dedicated define WITH_ASSERTS instead

#ifdef WITH_ASSERTS
	// Assert condition
	#define VRAY_ASSERT(test) assert(test)
#else
	#if defined(__GNUC__)
			#define ASSERT_PRINT_POS(a) fprintf(stderr, "ASSERT failed: %s:%d, %s(), at \'%s\'\n", __FILE__, __LINE__, __func__, #a)
	#elif defined(_MSC_VER)
			#define ASSERT_PRINT_POS(a) fprintf(stderr, "ASSERT failed: %s:%d, %s(), at \'%s\'\n", __FILE__, __LINE__, __FUNCTION__, #a)
	#else
			#define ASSERT_PRINT_POS(a) fprintf(stderr, "ASSERT failed: %s:%d, at \'%s\'\n", __FILE__, __LINE__, #a)
	#endif

	/// Test condition and print a message if failed
	#define VRAY_ASSERT(test) (void)((!(test)) ? (ASSERT_PRINT_POS(test), 0) : 0)
#endif
