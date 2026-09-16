// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "seh_guard.h"

#ifdef _WIN32

// WIN32_LEAN_AND_MEAN / NOMINMAX come from the build definitions.
#include <windows.h>

#include <string.h>

namespace VrayZmqWrapper {
namespace details {

namespace {

/// STATUS_FATAL_APP_EXIT, the code libzmq's zmq_abort() raises on Windows.
constexpr DWORD ZMQ_ABORT_EXCEPTION_CODE = 0x40000015;

/// libzmq passes its assertion text as the first exception parameter. Copied here in
/// the filter, which runs before unwinding - by the __except body the frame is gone.
int captureFilter(DWORD code, const EXCEPTION_POINTERS* ex, char* errMsg, size_t errMsgSize) {
	if (code != ZMQ_ABORT_EXCEPTION_CODE) {
		return EXCEPTION_CONTINUE_SEARCH;
	}

	const EXCEPTION_RECORD* record = ex ? ex->ExceptionRecord : nullptr;

	if (record && (record->NumberParameters >= 1) && record->ExceptionInformation[0]) {
		strncpy_s(errMsg, errMsgSize,
		          reinterpret_cast<const char*>(record->ExceptionInformation[0]), _TRUNCATE);
	}
	else {
		strncpy_s(errMsg, errMsgSize, "no message", _TRUNCATE);
	}

	return EXCEPTION_EXECUTE_HANDLER;
}

} // namespace


bool invokeGuarded(void (*body)(void*), void* ctx, char* errMsg, size_t errMsgSize) {
	__try {
		body(ctx);
		return true;
	}
	__except (captureFilter(GetExceptionCode(), GetExceptionInformation(), errMsg, errMsgSize)) {
		return false;
	}
}

} // namespace details
} // namespace VrayZmqWrapper

#endif // _WIN32
