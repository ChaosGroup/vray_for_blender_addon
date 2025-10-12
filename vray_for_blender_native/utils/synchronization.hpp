#pragma once

#include <Python.h>
#include <boost/python.hpp>

#include "vassert.h"

/// Smart GIL lock
/// To be called from a C++ thread to acquire the GIL 
class WithGIL
{
	WithGIL(const WithGIL&) = delete;
	WithGIL& operator=(const WithGIL&) = delete;

public:
	WithGIL() :
		gstate(PyGILState_Ensure())
	{
	}

	~WithGIL() {
		PyGILState_Release(gstate);
	}

private:
	PyGILState_STATE gstate;
};


/// Smart GIL unlock
/// To be called from a Python thread to temporarily release the GIL
class WithNoGIL {
	WithNoGIL(const WithNoGIL&) = delete;
	WithNoGIL& operator=(const WithNoGIL&) = delete;

public:
	WithNoGIL()
		: threadState(PyEval_SaveThread())
	{
		vassert(threadState && "Failed to save thread state");
	}


	~WithNoGIL() {
		// Calling PyEval_RestoreThread during thread finalization will terminate the thread
		if (!_Py_IsFinalizing()) {
			PyEval_RestoreThread(threadState);
		}
	}

private:
	PyThreadState* threadState;  // C++ thread state
};
