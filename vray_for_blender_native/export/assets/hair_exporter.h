#pragma once

#include <base_types.h>



namespace VRayForBlender {

	// Forward declarations
	namespace Interop {
		struct HairData;
	}

	class ZmqExporter;

	namespace Assets
	{
		VRayBaseTypes::AttrValue exportGeomHair(const Interop::HairData& hair, ZmqExporter& exporter);
	}


} // namespace VRayForBlender

