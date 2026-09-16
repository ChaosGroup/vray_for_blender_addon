// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include "utils/logger.hpp"


 ///////////////////////////////////////////////////////////////////////////////
 /////////////////////    Boost Python macros       ////////////////////////////
 ///////////////////////////////////////////////////////////////////////////////

 /// Usage: 
 /// struct A {
 ///  PROPERTY(bool, isOn, false)

/// Define a property with read and write accessors
#define PROPERTY_NO_DEFAULT(TYPE, NAME)\
	TYPE NAME;\
	TYPE get_##NAME() const { return NAME; }\
	void set_##NAME(const TYPE& value) { NAME = value; }


/// Define a property with read and write accessors
#define PROPERTY(TYPE, NAME, DEFAULT)\
	TYPE NAME = DEFAULT;\
	TYPE get_##NAME() const { return NAME; }\
	void set_##NAME(const TYPE& value) { NAME = value; }



/// Declare free function
#define FUN(NAME)  #NAME, NAME

/// Declare class property
#define ADD_RW_PROPERTY(CLASS, NAME) 	def_prop_rw(#NAME, &CLASS::get_##NAME, &CLASS::set_##NAME)




///////////////////////////////////////////////////////////////////////////////
/////////////////////   Python interop helpers    /////////////////////////////
///////////////////////////////////////////////////////////////////////////////


namespace nb = nanobind;

/// Invoke a Python callback given a weak ref to the callback object
/// This function will lock th GIL and handle any exceptions produces by the call
template <typename ...TArgs>
bool invokePythonCallback(const std::string& callbackName, const nb::handle& callback, const TArgs&... args) {
	nb::gil_scoped_acquire gil;

	// The handle is non-owning and its slot can be reassigned from another thread, so calling
	// through it is a use-after-free (VBLD-2803). Take a reference now the GIL is held; `owned` is
	// declared after `gil` so its decref is GIL-protected too.
	nb::object owned;

	try {
		if (callback) {
			owned = nb::borrow(callback);
			owned(args...);
		}

		return true;
	}
	catch (const nb::python_error& e) {
		Logger::error("Python exception in '%1%' callback", callbackName);
		Logger::always(e.what());
	}
	catch (const std::exception& exc) {
		Logger::error("Exception in %1% callback: %2%", callbackName, exc.what());
	}
	catch (...) {
		Logger::error("Unknown exception in %1% callback", callbackName);
	}

	return false;
}

