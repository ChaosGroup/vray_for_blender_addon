#pragma once

#include <base_types.h>



namespace VRayForBlender {

	// Forward declarations
	namespace Interop {
		struct PointCloudData;
		struct InstancerData;
	}

	class ZmqExporter;

	namespace Assets
	{
		VRayBaseTypes::AttrValue exportPointCloud(const Interop::PointCloudData& pc, ZmqExporter& exporter);
		VRayBaseTypes::AttrValue exportInstancer (const Interop::InstancerData& inst, ZmqExporter& exporter);
	}

} // namespace VRayForBlender

