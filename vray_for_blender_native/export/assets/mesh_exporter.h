#pragma once

#include "export/plugin_desc.hpp"

namespace VRayForBlender {

	namespace Interop{
		struct MeshData;
	}

	namespace Assets
	{
		void fillMeshData(const Interop::MeshData& meshData, PluginDesc &pluginDesc);
		void fillGeometry(const Interop::MeshData& meshData, PluginDesc& pluginDesc);
		void fillChannelsData(const Interop::MeshData& meshData, PluginDesc &pluginDesc);
	}

} // namespace VRayForBlender

