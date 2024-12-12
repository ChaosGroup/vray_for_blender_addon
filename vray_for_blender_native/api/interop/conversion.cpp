#pragma once

#include "conversion.hpp"


namespace VRayForBlender
{

void pyListToAttrList(vray::AttrListValue& attrList, std::string::iterator& listElemTypes, const py::list& list)
{
	for (auto i = 0; i < py::len(list); i++) {
		py::api::const_object_item elem = list[i];
		if (*listElemTypes == 'l') {
			listElemTypes++;

			py::list sublist(elem);
			vray::AttrListValue subAttrList;

			pyListToAttrList(subAttrList, listElemTypes, sublist);

			attrList.append(subAttrList);
		}
		else {
			switch (*listElemTypes) {
			case 'f': {
				float fExtracted = py::extract<float>(elem);
				attrList.append(vray::AttrValue(fExtracted));
				break;
			}
			case 'i': {
				int iExtracted = py::extract<int>(elem);
				attrList.append(vray::AttrValue(iExtracted));
				break;
			}
			case 's': {
				std::string sExtracted = py::extract<std::string>(elem);
				attrList.append(vray::AttrValue(sExtracted));
				break;
			}
			case 'p': {
				std::string pExtracted = py::extract<std::string>(elem);
				attrList.append(vray::AttrPlugin(pExtracted));
				break;
			}
			}
			listElemTypes++;
		}

	}
}


VrayZmqWrapper::VRayMessage::RenderSizes fromRenderSizes(const py::object& obj)
{
	VrayZmqWrapper::VRayMessage::RenderSizes sz;

	sz.bitmask = py::extract<int>(obj.attr("bitmask"));

	sz.imgWidth = static_cast<int>(py::extract<float>(obj.attr("imgWidth")));
	sz.imgHeight = static_cast<int>(py::extract<float>(obj.attr("imgHeight")));
	sz.bmpWidth = static_cast<int>(py::extract<float>(obj.attr("bmpWidth")));
	sz.bmpHeight = static_cast<int>(py::extract<float>(obj.attr("bmpHeight")));

	sz.cropRgnLeft = py::extract<float>(obj.attr("cropRgnLeft"));
	sz.cropRgnTop = py::extract<float>(obj.attr("cropRgnTop"));
	sz.cropRgnWidth = py::extract<float>(obj.attr("cropRgnWidth"));
	sz.cropRgnHeight = py::extract<float>(obj.attr("cropRgnHeight"));

	sz.rgnLeft = static_cast<int>(py::extract<float>(obj.attr("rgnLeft")));
	sz.rgnTop = static_cast<int>(py::extract<float>(obj.attr("rgnTop")));
	sz.rgnWidth = static_cast<int>(py::extract<float>(obj.attr("rgnWidth")));
	sz.rgnHeight = static_cast<int>(py::extract<float>(obj.attr("rgnHeight")));

	return sz;
}



} // end namespace VrayForBlender::Interop
