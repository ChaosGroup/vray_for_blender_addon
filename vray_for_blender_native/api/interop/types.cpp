#include "types.h"
#include "conversion.hpp"
#include "export/zmq_server.h"

namespace VRayForBlender::Interop
{
using ExporterType = VrayZmqWrapper::ExporterType;

void ExporterSettings::setDRHosts(py::object hosts) {
	drHosts = toVector<std::string>(hosts);
}

ExporterType ExporterSettings::getExporterType() const
{
	return static_cast<ExporterType>(get_exporterType());
}



MeshData::MeshData(py::object obj) :
	name               (py::extract<std::string>(obj.attr("name"))),
	normalsDomain      (static_cast<NormalsDomain>(static_cast<int>(py::extract<int>(obj.attr("normalsDomain"))))),
	vertices           (fromDataArray<float[3]>(obj.attr("vertices"))),
	loops              (fromDataArray<unsigned int>(obj.attr("loops"))),
	loopTris           (fromDataArray<unsigned int[3]>(obj.attr("loopTris"))),
	loopTriPolys       (fromDataArray<unsigned int>(obj.attr("loopTriPolys"))),
	polyMtlIndices     (fromDataArray<unsigned int>(obj.attr("polyMtlIndices"))),
	normals			   (fromDataArray<float[3]>(obj.attr("normals"))),
	uvLayers           (fromUVAttrLayersArr(obj.attr("loopUVs"))),
	colorLayers        (fromAttrLayersArr(obj.attr("loopColors"))),
	options			   (py::extract<MeshExportOptions>(obj.attr("options"))),
	ref				   (obj)
{
	const auto& subdivAttr = obj.attr("subdiv");
	subdiv.enabled = py::extract<bool>(subdivAttr.attr("enabled"));
	if (subdiv.enabled) {
		subdiv.level = py::extract<int>(subdivAttr.attr("level"));
		subdiv.type = py::extract<int>(subdivAttr.attr("type"));
		subdiv.useCreases = py::extract<bool>(subdivAttr.attr("useCreases"));
	}

	if ((normalsDomain < NormalsDomain::Face) || (NormalsDomain::Corner < normalsDomain))
	{
		throw std::runtime_error("Invalid normalsDomain value");
	}
}

HairData::HairData(py::object obj) :
	name			(py::extract<std::string>(obj.attr("name"))),
	type			(py::extract<std::string>(obj.attr("type"))),
	widthsInPixels	(py::extract<bool>(obj.attr("widthsInPixels"))),
	useHairBSpline	(py::extract<bool>(obj.attr("useHairBSpline"))),
	points			(fromDataArray<float[3]>(obj.attr("points"))),
	pointRadii		(fromNdArray<float>(obj.attr("pointRadii"))),
	strandSegments	(fromNdArray<int>(obj.attr("strandSegments"))),
	uvs				(fromNdOrDataArray<float>(obj.attr("uvs"), type == "CURVES")),
	vertColors		(fromNdArray<float>(obj.attr("vertColors"))),
	ref				(obj),
	psys            ((ParticleSystem*)(size_t)py::extract<size_t>(obj.attr("psys"))),
	firstToExport   (py::extract<int>(obj.attr("firstToExport"))),
	totalParticles  (py::extract<int>(obj.attr("totalParticles"))),
	maxSteps        (py::extract<int>(obj.attr("maxSteps"))),
	shape           (py::extract<float>(obj.attr("shape"))),
	rootRadius      (py::extract<float>(obj.attr("rootRadius"))),
	tipRadius       (py::extract<float>(obj.attr("tipRadius")))
{}


PointCloudData::PointCloudData(py::object obj) :
	name		(py::extract<std::string>(obj.attr("name"))),
	renderType	(py::extract<int>(obj.attr("renderType"))),
	points		(fromNdArray<float[3]>(obj.attr("points"))),
	uvs			(fromNdArray<float[2]>(obj.attr("uvs"))),
	radii		(fromNdArray<float>(obj.attr("radii"))),
	colors		(fromNdArray<float[3]>(obj.attr("colors"))),
	ref			(obj)
{}


InstancerData::InstancerData(py::object obj) :
	name		(py::extract<std::string>(obj.attr("name"))),
	frame		(py::extract<int>(obj.attr("frame"))),
	itemCount	(py::extract<int>(obj.attr("count"))),
	data		(fromDataArray<char>(obj.attr("arr"))),
	ref			(obj)
{}


InstanceData::InstanceData(const py::object& obj) :
	index		(py::extract<int>(obj.attr("index"))),
	nodePlugin	(py::extract<std::string>(obj.attr("nodePlugin"))),
	tm			(fromMat<4>(obj.attr("tm"))),
	vel			(fromMat<4>(obj.attr("vel")))
{}


SmokeData::SmokeData(const py::object& obj) :
	name		(py::extract<std::string>(obj.attr("name"))),
	cacheDir	(py::extract<std::string>(obj.attr("cacheDir"))),
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
