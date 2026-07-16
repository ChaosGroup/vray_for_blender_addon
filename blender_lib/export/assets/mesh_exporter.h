// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

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

