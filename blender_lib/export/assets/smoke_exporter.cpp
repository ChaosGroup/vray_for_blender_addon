// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "smoke_exporter.h"

#include "export/plugin_desc.hpp"
#include "export/zmq_exporter.h"


using namespace VRayBaseTypes;


namespace VRayForBlender::Assets
{


AttrValue exportVRayNodePhxShaderSim(std::string objName, 
									std::string cachePath, 
									AttrTransform transform, 
									int domainRes[3],
									ZmqExporter& exporter)
{
	const std::string pluginName = objName + "@PhxShaderSim";

	const std::string fluidCacheName = pluginName + "@PhxShaderCache";

	PluginDesc fluidCacheAttrs(fluidCacheName, "PhxShaderCache");
	fluidCacheAttrs.add("grid_size_x", domainRes[0]);
	fluidCacheAttrs.add("grid_size_y", domainRes[1]);
	fluidCacheAttrs.add("grid_size_z", domainRes[2]);
	fluidCacheAttrs.add("cache_path", cachePath);
	AttrPlugin fluidCache = exporter.exportPlugin(fluidCacheAttrs);


	PluginDesc phxShaderSim(pluginName, "PhxShaderSim");
	phxShaderSim.add("play_speed", 1);
	phxShaderSim.add("node_transform", transform);
	phxShaderSim.add("renderAsVolumetric", 1);
	phxShaderSim.add("cache", fluidCache);


	PluginDesc phxShaderGlobalVolume("__PhxShaderGlobalVolume__", "PhxShaderSimVol");
	exporter.exportPlugin(phxShaderGlobalVolume);

	return exporter.exportPlugin(phxShaderSim);
}

} // end namespace VRayForBlender::Assets