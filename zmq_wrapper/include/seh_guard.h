// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <stdexcept>
#include <string>
#include <type_traits>

////////////////////////////////////////////////////////////////////////////////
/// libzmq has no assertion hook: on Windows zmq_abort() raises STATUS_FATAL_APP_EXIT
/// instead of returning an error, killing the host process. Catching it does not make
/// libzmq usable again - the goal is only to fail with a logged reason rather than
/// take Blender down silently.
///
/// Under /EHsc an SEH unwind runs no C++ destructors, so objects in the guarded scope
/// leak. Deliberate: their destructors would re-enter an already broken libzmq.
////////////////////////////////////////////////////////////////////////////////

namespace VrayZmqWrapper {

/// Thrown in place of a libzmq abort. Not a ZmqException: those are ordinary
/// connection errors, this is always fatal.
class ZmqAbortError : public std::runtime_error {
public:
	explicit ZmqAbortError(const std::string& msg) : std::runtime_error(msg)
	{}
};


#ifdef _WIN32

namespace details {

/// Run body(ctx) under __try/__except. Separate function because MSVC rejects __try
/// in any function needing C++ object unwinding (C2712), hence the function pointer.
/// @param body        - code to run
/// @param ctx         - opaque argument forwarded to body
/// @param errMsg      - receives libzmq's own assertion text on failure
/// @param errMsgSize  - size of the errMsg buffer, must be > 0
/// @return true if body ran to completion, false if libzmq aborted
bool invokeGuarded(void (*body)(void*), void* ctx, char* errMsg, size_t errMsgSize);

} // namespace details


/// Run fn, translating a libzmq abort into a ZmqAbortError. Anything else propagates
/// untouched.
template <typename Fn>
void runGuarded(Fn&& fn) {
	using FnType = std::remove_reference_t<Fn>;

	char errMsg[256] = { 0 };

	// Captureless so it converts to a plain function pointer.
	const auto trampoline = [](void* ctx) { (*static_cast<FnType*>(ctx))(); };

	if (!details::invokeGuarded(trampoline, &fn, errMsg, sizeof(errMsg))) {
		// Only the errno/WSA string reaches us; libzmq prints the assertion site itself.
		throw ZmqAbortError(std::string("libzmq aborted: ") + errMsg
		                    + " (libzmq logged the assertion site to stderr)");
	}
}

#else

template <typename Fn>
void runGuarded(Fn&& fn) {
	fn();
}

#endif

} // namespace VrayZmqWrapper
