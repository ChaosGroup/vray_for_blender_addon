#include "types.h"
#include "conversion.hpp"
#include "export/zmq_server.h"

namespace VRayForBlender::Interop
{
using ExporterType = VrayZmqWrapper::ExporterType;

ExporterType ExporterSettings::getExporterType() const
{
	return static_cast<ExporterType>(get_exporterType());
}



MeshData::MeshData(py::object obj) :
	name            (py::extract<std::string>(obj.attr("name"))),
	normalsDomain   (static_cast<NormalsDomain>(static_cast<int>(py::extract<int>(obj.attr("normalsDomain"))))),
	vertices        (fromDataArray<float[3]>(obj.attr("vertices"))),
	loops           (fromDataArray<unsigned int>(obj.attr("loops"))),
	loopTris        (fromDataArray<unsigned int[3]>(obj.attr("loopTris"))),
	loopTriPolys    (fromNdArray<unsigned int>(obj.attr("loopTriPolys"))),
	polyMtlIndices  (fromNdArray<unsigned int>(obj.attr("polyMtlIndices"))),
	normals			(fromDataArray<float[3]>(obj.attr("normals"))),
	uvLayers        (fromAttrLayersArr<float[2]>(obj.attr("loopUVs"))),
	colorLayers     (fromAttrLayersArr<MLoopCol>(obj.attr("loopColors"))),
	options			(py::extract<MeshExportOptions>(obj.attr("options"))),
	ref				(obj)
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
	segments		(py::extract<int>(obj.attr("segments"))),
	width			(py::extract<float>(obj.attr("width"))),
	fadeWidth		(py::extract<bool>(obj.attr("fadeWidth"))),
	widthsInPixels	(py::extract<bool>(obj.attr("widthsInPixels"))),
	useHairBSpline	(py::extract<bool>(obj.attr("useHairBSpline"))),
	matWorld		(fromMat<4>(obj.attr("matWorld"))),
	points			(fromNdArray<float[3]>(obj.attr("points"))),
	pointRadii		(fromNdArray<float>(obj.attr("pointRadii"))),
	strandSegments	(fromNdArray<int>(obj.attr("strandSegments"))),
	uvs				(fromNdArray<float[2]>(obj.attr("uvs"))),
	vertColors		(fromNdArray<float[3]>(obj.attr("vertColors"))),
	ref				(obj)
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
	return std::format("tcp://127.0.0.1:{}", serverPort);
}


void ZmqControlConn::start(const ZmqServerArgs& args)
{
	VRayForBlender::ZmqServer::get().start(args);
}


void ZmqControlConn::stop()
{
	VRayForBlender::ZmqServer::get().stop();
}


bool ZmqControlConn::check()
{
	return VRayForBlender::ZmqServer::get().isRunning();
}


// ZmqServer logs in two ways : the logs from VRay are sent on the wire, ZMQ server own logs
// are printed directly to the console used by Blender. The first kind gets its log level
// from the VRay message and are filtered on the client. The level for the messages printed
// to the console is set by the following call.
void ZmqControlConn::setLogLevel(int level)
{
	using namespace VrayZmqWrapper;
	return VRayForBlender::ZmqServer::get().sendMessage(VRayMessage::msgControlSetLogLevel(level));
}


} // end namespace VRayForBlender::Interop
