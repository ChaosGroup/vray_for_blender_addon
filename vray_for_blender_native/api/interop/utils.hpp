#pragma once

#include <Python.h>
#include <boost/python.hpp>
#include "utils/synchronization.hpp"
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
#define ADD_RW_PROPERTY(CLASS, NAME) 	add_property(#NAME, &CLASS::get_##NAME, &CLASS::set_##NAME)




///////////////////////////////////////////////////////////////////////////////
/////////////////////   Python interop helpers    /////////////////////////////
///////////////////////////////////////////////////////////////////////////////


namespace py = boost::python;


inline bool isNone(const py::object& obj)
{
	static const py::object PyObjNone;
	return obj.ptr() == PyObjNone.ptr();
}


inline std::string getLastPyError(){
 
	namespace py = boost::python;

	PyObject* excType = nullptr;
	PyObject* value = nullptr;
	PyObject*stacktrace = nullptr;

	PyErr_Fetch(&excType, &value, &stacktrace);

	if (!excType || !value) {
		return "getLastPyError: Unable to extract Python error info.";
	}

	std::stringstream ss;
  
	py::object pyExc(py::handle<>(py::borrowed(value)));

	auto pyTypeName = pyExc.attr("__class__").attr("__name__");
	auto pyMsg = pyExc.attr("__str__")();

	std::string typeName = py::extract<std::string>(pyTypeName);
	std::string msg = py::extract<std::string>(pyMsg);
	
	ss << typeName << " :: " << msg << std::endl;


	if (stacktrace != nullptr) {
		try {
			py::object extypeObj(py::handle<>(py::borrowed(excType)));
			py::object valueObj(py::handle<>(py::borrowed(value)));
			py::object stacktraceObj(py::handle<>(py::borrowed(stacktrace)));
		
		
			py::object modStacktrace = py::import("traceback");
			py::object lines = modStacktrace.attr("format_exception")(extypeObj, valueObj, stacktraceObj);
		
			for (int i = 0; i < py::len(lines); ++i) {
				ss << py::extract<std::string>(lines[i])();
			}
		}
		catch(...) {
			ss << "Traceback generation failed" << std::endl;
		}
	}

	// PyErr_Fetch clears the error state. We don't want to mess with it, so restore it.
	PyErr_Restore(excType, value, stacktrace);

	return ss.str();
}


// Array deleted to be used with PyCapsule
template <class T>
inline void deleteNativeArray(PyObject* self) {
	auto* array = static_cast<T*>(PyCapsule_GetPointer(self, nullptr));
	delete[] array;
}


/// Invoke a Python callback given a weak ref to the callback object
/// This function will lock th GIL and handle any exceptions produces by the call
template <typename ...TArgs>
bool invokePythonCallback(const std::string& callbackName, const py::object& cbWeakRef, const TArgs&... args) {
	WithGIL gil;

	try {
		py::object callback = cbWeakRef();

		if (!callback.is_none()) {
			callback(args...);
		}

		return true;
	}
	catch (const py::error_already_set&) {
		Logger::error("Python exception in '%1%' callback", callbackName);
		Logger::always(getLastPyError());
	}
	catch (const std::exception& exc) {
		Logger::error("Exception in %1% callback: %2%", callbackName, exc.what());
	}
	catch (...) {
		Logger::error("Unknown exception in %1% callback", callbackName);
	}

	return false;
}

