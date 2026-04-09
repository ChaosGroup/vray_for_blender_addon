// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "conversion.hpp"

namespace VRayForBlender
{

void pyListToAttrList(vray::AttrListValue& attrList, std::string::iterator& listElemTypes, const nb::list& list)
{
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
				const std::string sExtracted = nb::cast<std::string>(elem);
				attrList.append(vray::AttrValue(sExtracted));
				break;
			}
			case 'p': {
				const std::string pExtracted = nb::cast<std::string>(elem);
				attrList.append(vray::AttrPlugin(pExtracted));
				break;
			}
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


std::vector<Interop::AttrLayer> fromAttrLayersArr(const nb::object& list)
{
	auto vec = toVector<nb::object>(list);

	std::vector<Interop::AttrLayer> result;

	for (const auto& layer : vec) {
		const std::string domain = nb::cast<std::string>(layer.attr("domain"));
		const std::string dataType = nb::cast<std::string>(layer.attr("dataType"));
		const uint8_t* elementPtr = toPtr<uint8_t>(layer.attr("ptr"));
		const size_t elementCount = nb::cast<size_t>(layer.attr("count"));
		result.push_back(
			Interop::AttrLayer {
				nb::cast<std::string>(layer.attr("name")),
				dataType == "BYTE_COLOR" ? Interop::AttrLayer::Byte : Interop::AttrLayer::Float,
				domain == "POINT" ? Interop::AttrLayer::Point : Interop::AttrLayer::Corner,
				elementPtr,
				elementCount
			}
		);
	}

	return result;
}


} // end namespace VrayForBlender::Interop
