// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "import_conversion.h"

#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>

#include <zmq_message.hpp>

#include "utils/logger.hpp"


namespace VRayForBlender {
namespace Interop {

namespace vray = VRayBaseTypes;

namespace {

nb::tuple toTuple(const vray::AttrVector& v) {
	return nb::make_tuple(v.x, v.y, v.z);
}

nb::tuple toTuple(const vray::AttrMatrix& m) {
	return nb::make_tuple(toTuple(m.v0), toTuple(m.v1), toTuple(m.v2));
}

nb::tuple toTuple(const vray::AttrTransform& tm) {
	return nb::make_tuple(toTuple(tm.m), toTuple(tm.offs));
}

/// Plugin references use the parser's link-string convention: "name" or "name::output".
nb::object toLinkString(const vray::AttrPlugin& plugin) {
	if (plugin.output.empty()) {
		return nb::cast(plugin.plugin);
	}
	return nb::cast(plugin.plugin + "::" + plugin.output);
}

/// Zero-copy numpy view over an AttrList's buffer. The capsule owns a copy of the
/// AttrList, i.e. a bump of the shared_ptr that keeps the buffer alive (the same
/// idiom as getImageImpl in python_api.cpp).
template <typename ElemT, typename ListElemT>
nb::object toNdarray(const vray::AttrList<ListElemT>& list, size_t componentsPerItem) {
	using ListT = vray::AttrList<ListElemT>;
	nb::capsule owner(new ListT(list), [](void* p) noexcept {
		delete static_cast<ListT*>(p);
	});

	// list.empty() has no backing buffer to view; a null data pointer with a zero-length
	// shape is a valid (never dereferenced) numpy array, so callers always get an ndarray
	// back - not a plain list - regardless of count.
	const auto* data = list.empty() ? nullptr
	                                : reinterpret_cast<const ElemT*>(list.getData()->data());

	if (componentsPerItem == 1) {
		const size_t shape[1] = { static_cast<size_t>(list.getCount()) };
		return nb::ndarray<nb::numpy, const ElemT, nb::c_contig>(data, 1, shape, owner).cast();
	}

	const size_t shape[2] = { static_cast<size_t>(list.getCount()), componentsPerItem };
	return nb::ndarray<nb::numpy, const ElemT, nb::c_contig>(data, 2, shape, owner).cast();
}

template <typename ListElemT, typename ToItemFn>
nb::list toPyList(const vray::AttrList<ListElemT>& list, ToItemFn toItem) {
	nb::list result;
	if (!list.empty()) {
		for (const auto& item : *list.getData()) {
			result.append(toItem(item));
		}
	}
	return result;
}

} // anonymous namespace


nb::object attrValueToPython(const vray::AttrValue& value, bool preferNdarray) {
	using namespace VRayBaseTypes;

	switch (value.type) {
	case ValueTypeInt:
		return nb::cast(value.as<AttrSimpleType<int>>().value);
	case ValueTypeFloat:
		return nb::cast(value.as<AttrSimpleType<float>>().value);
	case ValueTypeString:
		return nb::cast(value.as<AttrSimpleType<std::string>>().value);
	case ValueTypeColor: {
		const auto& v = value.as<AttrColor>();
		return nb::make_tuple(v.r, v.g, v.b);
	}
	case ValueTypeAColor: {
		const auto& v = value.as<AttrAColor>();
		return nb::make_tuple(v.color.r, v.color.g, v.color.b, v.alpha);
	}
	case ValueTypeVector:
		return toTuple(value.as<AttrVector>());
	case ValueTypeVector2: {
		const auto& v = value.as<AttrVector2>();
		return nb::make_tuple(v.x, v.y);
	}
	case ValueTypeMatrix:
		return toTuple(value.as<AttrMatrix>());
	case ValueTypeTransform:
		return toTuple(value.as<AttrTransform>());
	case ValueTypePlugin:
		return toLinkString(value.as<AttrPlugin>());
	case ValueTypeListInt:
		if (preferNdarray) {
			return toNdarray<int32_t>(value.as<AttrListInt>(), 1);
		}
		return toPyList(value.as<AttrListInt>(), [](int v) { return nb::cast(v); });
	case ValueTypeListFloat:
		if (preferNdarray) {
			return toNdarray<float>(value.as<AttrListFloat>(), 1);
		}
		return toPyList(value.as<AttrListFloat>(), [](float v) { return nb::cast(v); });
	case ValueTypeListVector:
		if (preferNdarray) {
			return toNdarray<float>(value.as<AttrListVector>(), 3);
		}
		return toPyList(value.as<AttrListVector>(), [](const AttrVector& v) { return toTuple(v); });
	case ValueTypeListColor:
		if (preferNdarray) {
			return toNdarray<float>(value.as<AttrListColor>(), 3);
		}
		return toPyList(value.as<AttrListColor>(), [](const AttrColor& v) { return nb::make_tuple(v.r, v.g, v.b); });
	case ValueTypeListVector2:
		if (preferNdarray) {
			return toNdarray<float>(value.as<AttrListVector2>(), 2);
		}
		return toPyList(value.as<AttrListVector2>(), [](const AttrVector2& v) { return nb::make_tuple(v.x, v.y); });
	case ValueTypeListMatrix:
		return toPyList(value.as<AttrListMatrix>(), [](const AttrMatrix& v) { return toTuple(v); });
	case ValueTypeListTransform:
		if (preferNdarray) {
			// AttrTransform is 12 contiguous floats (v0, v1, v2, offs); a zero-copy (N, 12)
			// view lets the instancer importer decompose transforms with vectorized numpy.
			return toNdarray<float>(value.as<AttrListTransform>(), 12);
		}
		return toPyList(value.as<AttrListTransform>(), [](const AttrTransform& v) { return toTuple(v); });
	case ValueTypeListString:
		return toPyList(value.as<AttrListString>(), [](const std::string& v) { return nb::cast(v); });
	case ValueTypeListPlugin:
		return toPyList(value.as<AttrListPlugin>(), [](const AttrPlugin& v) { return toLinkString(v); });
	case ValueTypeListValue:
		return toPyList(value.as<AttrListValue>(), [preferNdarray](const AttrValue& v) {
			return attrValueToPython(v, preferNdarray);
		});
	default:
		Logger::warning("Cannot convert imported value of type %1% to Python", value.getTypeAsString());
		return nb::none();
	}
}


nb::list pluginPropertyValuesToPython(const std::vector<VrayZmqWrapper::PluginPropertyValueData>& values) {
	nb::list result;

	for (const auto& value : values) {
		result.append(nb::make_tuple(
			value.pluginName,
			value.propertyName,
			attrValueToPython(value.value, false)
		));
	}

	return result;
}

} // namespace Interop
} // namespace VRayForBlender
