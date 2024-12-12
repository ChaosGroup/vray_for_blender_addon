#pragma once

#include <span>
#include <vector>
#include <boost/python.hpp>

#include <base_types.h>
#include <zmq_message.hpp>

#include "types.h"

namespace py = boost::python;
using ndarray = boost::python::numpy::ndarray;
namespace vray = VRayBaseTypes;


namespace VRayForBlender
{

void pyListToAttrList(vray::AttrListValue& attrList, std::string::iterator& listElemTypes, const py::list& list);
VrayZmqWrapper::VRayMessage::RenderSizes fromRenderSizes (const py::object& obj);



/// Convert any iterable python type to a vector. If using a heterogeneous container,
/// the caller must make sure that all elements are of the same type 
template<typename T>
inline std::vector<T> toVector(const py::object& iterable)
{
	return std::vector<T>(py::stl_input_iterator<T>(iterable), py::stl_input_iterator<T>());
}


/// Convert Python int to void*
template<class T>
T* toPtr(const py::api::const_object_attribute& a)
{
	const size_t val = py::extract<size_t>(a);
	return reinterpret_cast<T*>(val);
}


template <class T>
std::span<const T> fromDataArray(const py::object& arr)
{
	const T* ptr = toPtr<T>(arr.attr("ptr"));
	const size_t count = py::extract<size_t>(arr.attr("count"));

	return std::span<const T>(ptr, count);
}


template <class T>
std::span<const T> fromNdArray(const py::object& attr)
{
	ndarray arr = py::extract<ndarray>(attr);
	const T* ptr = reinterpret_cast<const T*>(arr.get_data());
	const size_t count = arr.shape(0);

	return std::span<const T>(ptr, count);
}


template <class T>
std::span<const T> fromPyArray(const py::object& arr)
{
	py::object buffer = arr.attr("buffer_info")();
	auto info = toVector<size_t>(buffer);

	return std::span<const T>(reinterpret_cast<T*>(info[0]), info[1]);
}


template<class T>
std::vector<Interop::AttrLayer<const T>> fromAttrLayersArr(const py::object& list)
{
	auto vec = toVector<py::object>(list);

	std::vector<Interop::AttrLayer<const T>> result;

	for (const auto& layer : vec) {

		auto& refLayer = result.emplace_back(Interop::AttrLayer<const T>());
		refLayer.name = py::extract<std::string>(layer.attr("name"));
		refLayer.data = fromDataArray<T>(layer);
	}

	return result;
}


template<size_t size>
std::vector<float> fromMat(const py::object& mat)
{
	std::vector<float> vec = toVector<float>(mat);

	if (vec.size() != size * size) {
		throw std::runtime_error(std::string("Not a matrix" + std::to_string(size)));
	}

	return vec;
}



} // end namespace VRayForBlender