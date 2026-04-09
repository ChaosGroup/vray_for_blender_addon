// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <span>
#include <vector>
#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/vector.h>

#include <base_types.h>
#include <zmq_message.hpp>

#include "types.h"

namespace nb = nanobind;
namespace vray = VRayBaseTypes;
namespace proto = VrayZmqWrapper;

namespace VRayForBlender
{

void pyListToAttrList(vray::AttrListValue& attrList, std::string::iterator& listElemTypes, const nb::list& list);

std::vector<Interop::UVAttrLayer> fromUVAttrLayersArr(const nb::object& list);

std::vector<Interop::AttrLayer> fromAttrLayersArr(const nb::object& list);

/// Convert AttrList to nanobind::list.
template<typename T>
nb::list toPyList(const vray::AttrList<T>& attrList)
{
	nb::list pyList;
	const typename vray::AttrList<T>::DataArrayPtr attrListData = attrList.getData();

	for (const auto& elem : *attrListData)
		pyList.append(elem);

	return pyList;
}

/// Convert nanobind::list to AttrList.
template<typename T>
vray::AttrList<T> toAttrList(const nb::list &pyList)
{
	nb::gil_scoped_acquire gil;
	vray::AttrList<T> attrList;

	for (auto elem : pyList) {
		T extractedVal = nb::cast<T>(elem);
		attrList.append(extractedVal);
	}

	return attrList;
}


proto::RenderSizes fromRenderSizes (const nb::object& obj);


/// Convert any iterable python type to a vector. If using a heterogeneous container,
/// the caller must make sure that all elements are of the same type
template<typename T>
inline std::vector<T> toVector(const nb::object& iterable)
{
	return nb::cast<std::vector<T>>(iterable);
}


/// Convert Python int to void*
template<class T>
T* toPtr(const nb::object& a)
{
	const size_t val = nb::cast<size_t>(a);
	return reinterpret_cast<T*>(val);
}


template <class T>
std::span<const T> fromDataArray(const nb::object& arr)
{
	const T* ptr = toPtr<T>(arr.attr("ptr"));
	const size_t count = nb::cast<size_t>(arr.attr("count"));

	return std::span<const T>(ptr, count);
}


template <class T>
std::span<const T> fromNdArray(const nb::object& attr)
{
	if constexpr (std::is_array_v<T>) {
		using ElementType = std::remove_all_extents_t<T>;
		constexpr size_t N = sizeof(T) / sizeof(ElementType);

		auto arr = nb::cast<nb::ndarray<ElementType, nb::c_contig>>(attr);
		if (arr.ndim() != 2 || arr.shape(1) != N) {
			throw std::runtime_error("fromNdArray: unexpected array shape.");
		}
		const T* ptr = reinterpret_cast<const T*>(arr.data());
		const size_t count = arr.shape(0);
		return std::span<const T>(ptr, count);

	} else {
		auto arr = nb::cast<nb::ndarray<T, nb::c_contig>>(attr);
		if (arr.ndim() != 1) {
			throw std::runtime_error("fromNdArray: unexpected array shape, expected 1D array.");
		}
		const T* ptr = arr.data();
		const size_t count = arr.shape(0);
		return std::span<const T>(ptr, count);
	}
}


// Returns a read-only span of T from a Python attribute.
// Uses NdArray conversion if condition is true; otherwise, uses DataArray conversion.
template <class T>
std::span<const T> fromNdOrDataArray(const nb::object& attr, bool isDataArray)
{
	return (isDataArray)? fromDataArray<T>(attr) : fromNdArray<T>(attr);
}


template <class T>
std::span<const T> fromPyArray(const nb::object& arr)
{
	nb::object buffer = arr.attr("buffer_info")();
	auto info = toVector<size_t>(buffer);

	return std::span<const T>(reinterpret_cast<T*>(info[0]), info[1]);
}


template<size_t size>
std::vector<float> fromMat(const nb::object& mat)
{
	std::vector<float> vec = toVector<float>(mat);

	if (vec.size() != size * size) {
		throw std::runtime_error(std::string("Not a matrix" + std::to_string(size)));
	}

	return vec;
}


} // end namespace VRayForBlender