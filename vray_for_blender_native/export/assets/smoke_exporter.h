#pragma once

#include <string>
#include <base_types.h>



namespace VRayForBlender {

	// Forward declarations
	class ZmqExporter;

	namespace Assets
	{
		VRayBaseTypes::AttrValue exportVRayNodePhxShaderSim(std::string objName, 
															std::string cachePath, 
															VRayBaseTypes::AttrTransform transform, 
															int domainRes[3],
															ZmqExporter& exporter);
	}

} // namespace VRayForBlender

