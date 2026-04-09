// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "types.h"
#include "conversion.hpp"

namespace VRayForBlender::Interop
{
using ExporterType = VrayZmqWrapper::ExporterType;

void ExporterSettings::setDRHosts(nb::object hosts) {
	drHosts = toVector<std::string>(hosts);
}

ExporterType ExporterSettings::getExporterType() const
{
	return static_cast<ExporterType>(get_exporterType());
}


MeshData::MeshData(nb::object obj) :
	name               (nb::cast<std::string>(obj.attr("name"))),
	normalsDomain      (static_cast<NormalsDomain>(static_cast<int>(nb::cast<int>(obj.attr("normalsDomain"))))),
	vertices           (fromDataArray<float[3]>(obj.attr("vertices"))),
	loops              (fromDataArray<unsigned int>(obj.attr("loops"))),
	loopTris           (fromDataArray<unsigned int[3]>(obj.attr("loopTris"))),
	loopTriPolys       (fromDataArray<unsigned int>(obj.attr("loopTriPolys"))),
	polyMtlIndices     (fromDataArray<unsigned int>(obj.attr("polyMtlIndices"))),
	normals			   (fromDataArray<float[3]>(obj.attr("normals"))),
	uvLayers           (fromUVAttrLayersArr(obj.attr("loopUVs"))),
	colorLayers        (fromAttrLayersArr(obj.attr("loopColors"))),
	options			   (nb::cast<MeshExportOptions>(obj.attr("options"))),
	ref				   (obj)
{
	const auto& subdivAttr = obj.attr("subdiv");
	subdiv.enabled = nb::cast<bool>(subdivAttr.attr("enabled"));
	if (subdiv.enabled) {
		subdiv.level = nb::cast<int>(subdivAttr.attr("level"));
		subdiv.type = nb::cast<int>(subdivAttr.attr("type"));
		subdiv.useCreases = nb::cast<bool>(subdivAttr.attr("useCreases"));
	}

	if ((normalsDomain < NormalsDomain::Face) || (NormalsDomain::Corner < normalsDomain))
	{
		throw std::runtime_error("Invalid normalsDomain value");
	}
}

HairData::HairData(nb::object obj) :
	name			(nb::cast<std::string>(obj.attr("name"))),
	type			(nb::cast<std::string>(obj.attr("type"))),
	widthsInPixels	(nb::cast<bool>(obj.attr("widthsInPixels"))),
	useHairBSpline	(nb::cast<bool>(obj.attr("useHairBSpline"))),
	points			(fromDataArray<float[3]>(obj.attr("points"))),
	pointRadii		(fromNdArray<float>(obj.attr("pointRadii"))),
	strandSegments	(fromNdArray<int>(obj.attr("strandSegments"))),
	uvs				(fromNdOrDataArray<float>(obj.attr("uvs"), type == "CURVES")),
	vertColors		(fromNdArray<float>(obj.attr("vertColors"))),
	ref				(obj),
	psys            ((ParticleSystem*)(size_t)nb::cast<size_t>(obj.attr("psys"))),
	firstToExport   (nb::cast<int>(obj.attr("firstToExport"))),
	totalParticles  (nb::cast<int>(obj.attr("totalParticles"))),
	maxSteps        (nb::cast<int>(obj.attr("maxSteps"))),
	shape           (nb::cast<float>(obj.attr("shape"))),
	rootRadius      (nb::cast<float>(obj.attr("rootRadius"))),
	tipRadius       (nb::cast<float>(obj.attr("tipRadius")))
{}


PointCloudData::PointCloudData(nb::object obj) :
	name		(nb::cast<std::string>(obj.attr("name"))),
	renderType	(nb::cast<int>(obj.attr("renderType"))),
	points		(fromNdArray<float[3]>(obj.attr("points"))),
	uvs			(fromNdArray<float[2]>(obj.attr("uvs"))),
	radii		(fromNdArray<float>(obj.attr("radii"))),
	colors		(fromNdArray<float[3]>(obj.attr("colors"))),
	ref			(obj)
{}


InstancerData::InstancerData(nb::object obj) :
	name		(nb::cast<std::string>(obj.attr("name"))),
	frame		(nb::cast<int>(obj.attr("frame"))),
	itemCount	(nb::cast<int>(obj.attr("count"))),
	data		(fromDataArray<char>(obj.attr("arr"))),
	ref			(obj)
{}


SmokeData::SmokeData(const nb::object& obj) :
	name		(nb::cast<std::string>(obj.attr("name"))),
	cacheDir	(nb::cast<std::string>(obj.attr("cacheDir"))),
	transform	(fromMat<4>(obj.attr("transform"))),
	domainRes	(toVector<int>(obj.attr("domainRes"))),
	ref			(obj)
{
}


std::string ZmqServerArgs::getAddress(int serverPort) const 
{
	return "tcp://127.0.0.1:" + std::to_string(serverPort);
}


} // end namespace VRayForBlender::Interop
