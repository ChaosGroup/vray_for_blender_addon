// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <optional>
#include <string_view>

#include "conversion.hpp"
#include "vassert.h"

namespace VRayForBlender
{

void pyListToAttrList(vray::AttrListValue& attrList, std::string::iterator& listElemTypes, const nb::list& list)
{
	attrList.reserve(static_cast<int>(list.size()));

	for (const nb::handle& elem : list) {
		if (*listElemTypes == 'l') {
			listElemTypes++;

			nb::list sublist(elem);
			vray::AttrListValue subAttrList;

			pyListToAttrList(subAttrList, listElemTypes, sublist);

			attrList.append(subAttrList);
		} else {
			switch (*listElemTypes) {
			case 'f': {
				const float fExtracted = nb::cast<float>(elem);
				attrList.append(vray::AttrValue(fExtracted));
				break;
			}
			case 'i': {
				const int iExtracted = nb::cast<int>(elem);
				attrList.append(vray::AttrValue(iExtracted));
				break;
			}
			case 's': {
				attrList.append(vray::AttrValue(nb::cast<const char*>(elem)));
				break;
			}
			case 'p': {
				attrList.append(vray::AttrPlugin(nb::cast<const char*>(elem)));
				break;
			}
			default:
				vassert(!"pyListToAttrList: unsupported type");
				break;
			}
			listElemTypes++;
		}
	}
}


proto::RenderSizes fromRenderSizes(const nb::object& obj)
{
	proto::RenderSizes sz;

	sz.bitmask = nb::cast<int>(obj.attr("bitmask"));

	sz.imgWidth = static_cast<int>(nb::cast<float>(obj.attr("imgWidth")));
	sz.imgHeight = static_cast<int>(nb::cast<float>(obj.attr("imgHeight")));
	sz.bmpWidth = static_cast<int>(nb::cast<float>(obj.attr("bmpWidth")));
	sz.bmpHeight = static_cast<int>(nb::cast<float>(obj.attr("bmpHeight")));

	sz.cropRgnLeft = nb::cast<float>(obj.attr("cropRgnLeft"));
	sz.cropRgnTop = nb::cast<float>(obj.attr("cropRgnTop"));
	sz.cropRgnWidth = nb::cast<float>(obj.attr("cropRgnWidth"));
	sz.cropRgnHeight = nb::cast<float>(obj.attr("cropRgnHeight"));

	sz.rgnLeft = static_cast<int>(nb::cast<float>(obj.attr("rgnLeft")));
	sz.rgnTop = static_cast<int>(nb::cast<float>(obj.attr("rgnTop")));
	sz.rgnWidth = static_cast<int>(nb::cast<float>(obj.attr("rgnWidth")));
	sz.rgnHeight = static_cast<int>(nb::cast<float>(obj.attr("rgnHeight")));

	return sz;
}


std::vector<Interop::UVAttrLayer> fromUVAttrLayersArr(const nb::object& list)
{
	auto vec = toVector<nb::object>(list);

	std::vector<Interop::UVAttrLayer> result;
	result.reserve(vec.size());

	for (const auto& layer : vec) {
		result.push_back(
			Interop::UVAttrLayer {
				nb::cast<std::string>(layer.attr("name")),
				fromDataArray<const float[2]>(layer)
			}
		);
	}

	return result;
}


static std::optional<Interop::AttrLayer::DataType> parseAttrDataType(std::string_view s)
{
	if (s == "BYTE_COLOR")   return Interop::AttrLayer::ByteColor;
	if (s == "FLOAT_COLOR")  return Interop::AttrLayer::FloatColor;
	if (s == "FLOAT")        return Interop::AttrLayer::Float;
	if (s == "INT")          return Interop::AttrLayer::Int;
	if (s == "INT8")         return Interop::AttrLayer::Int8;
	if (s == "BOOLEAN")      return Interop::AttrLayer::Boolean;
	if (s == "FLOAT_VECTOR") return Interop::AttrLayer::FloatVector;
	if (s == "FLOAT2")       return Interop::AttrLayer::Float2;
	if (s == "INT32_2D")     return Interop::AttrLayer::Int32_2D;
	if (s == "INT16_2D")     return Interop::AttrLayer::Int16_2D;
	return std::nullopt;
}


static std::optional<Interop::AttrLayer::Domain> parseAttrDomain(std::string_view s)
{
	if (s == "POINT")  return Interop::AttrLayer::Point;
	if (s == "CORNER") return Interop::AttrLayer::Corner;
	if (s == "EDGE")   return Interop::AttrLayer::Edge;
	if (s == "FACE")   return Interop::AttrLayer::Face;
	return std::nullopt;
}


std::vector<Interop::AttrLayer> fromAttrLayersArr(const nb::object& list)
{
	auto vec = toVector<nb::object>(list);

	std::vector<Interop::AttrLayer> result;
	result.reserve(vec.size());

	for (const auto& layer : vec) {
		const std::string domain = nb::cast<std::string>(layer.attr("domain"));
		const std::string dataType = nb::cast<std::string>(layer.attr("dataType"));

		const auto parsedType = parseAttrDataType(dataType);
		const auto parsedDomain = parseAttrDomain(domain);
		if (!parsedType || !parsedDomain) {
			continue; // Unsupported Blender attribute type/domain; skip silently.
		}

		const uint8_t* elementPtr = toPtr<uint8_t>(layer.attr("ptr"));
		const size_t elementCount = nb::cast<size_t>(layer.attr("count"));
		result.push_back(
			Interop::AttrLayer {
				nb::cast<std::string>(layer.attr("name")),
				*parsedType,
				*parsedDomain,
				elementPtr,
				elementCount
			}
		);
	}

	return result;
}


} // end namespace VrayForBlender::Interop
