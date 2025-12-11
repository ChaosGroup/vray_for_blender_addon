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


proto::RenderSizes fromRenderSizes(const py::object& obj)
{
	proto::RenderSizes sz;

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


std::vector<Interop::UVAttrLayer> fromUVAttrLayersArr(const py::object& list)
{
	auto vec = toVector<py::object>(list);

	std::vector<Interop::UVAttrLayer> result;

	for (const auto& layer : vec) {
		result.push_back(
			Interop::UVAttrLayer {
				py::extract<std::string>(layer.attr("name")),
				fromDataArray<const float[2]>(layer)
			}
		);
	}

	return result;
}


std::vector<Interop::AttrLayer> fromAttrLayersArr(const py::object& list)
{
	auto vec = toVector<py::object>(list);

	std::vector<Interop::AttrLayer> result;

	for (const auto& layer : vec) {
		const std::string domain = py::extract<std::string>(layer.attr("domain"));
		const std::string dataType = py::extract<std::string>(layer.attr("dataType"));
		const uint8_t* elementPtr = toPtr<uint8_t>(layer.attr("ptr"));
		const size_t elementCount = py::extract<size_t>(layer.attr("count"));
		result.push_back(
			Interop::AttrLayer {
				py::extract<std::string>(layer.attr("name")),
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
