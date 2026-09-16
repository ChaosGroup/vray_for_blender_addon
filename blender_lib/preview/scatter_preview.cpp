// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "scatter_preview.h"

#include <mutex>

#include <nanobind/stl/string.h>
#include <tsl/robin_map.h>

#include "api/interop/import_conversion.h"
#include "api/interop/utils.hpp"
#include "export/zmq_server.h"


namespace VRayForBlender {
namespace ScatterPreview {

using namespace VrayZmqWrapper;

namespace {

	const char* CB_RESULT = "scatterPreviewResult";
	const char* CB_PRESET_RESULT = "scatterPresetResult";

	std::mutex g_lock;
	tsl::robin_map<int, MsgScatterPreviewResult> g_results;
	tsl::robin_map<int, MsgScatterPresetResult> g_presetResults;

} // anonymous namespace


void onResult(const MsgScatterPreviewResult& result) {
	{
		std::scoped_lock lock(g_lock);
		g_results[result.requestId] = result;
	}

	// Runs on the ZMQ poller thread. Hold the GIL across the fetch and the call, or the main thread
	// can free the callable in between (VBLD-2803). Lock order GIL -> g_lock, as it is from Python.
	nb::gil_scoped_acquire gil;

	// The Python callback only carries scalars; the arrays are pulled on the main thread
	// via getResult so the (possibly large) transform buffer is not touched off-thread.
	invokePythonCallback(CB_RESULT, ZmqServer::get().getPythonCallback(CB_RESULT),
	                     result.requestId, result.status, result.errorText, result.instanceCount);
}


nb::tuple getResult(int requestId) {
	MsgScatterPreviewResult result;
	{
		std::scoped_lock lock(g_lock);
		auto it = g_results.find(requestId);
		if (it == g_results.end()) {
			return nb::make_tuple(nb::none(), nb::none());
		}
		result = it->second;
	}

	nb::object transforms = Interop::attrValueToPython(result.transforms, /*preferNdarray*/ true);
	nb::object topo = Interop::attrValueToPython(result.topo, /*preferNdarray*/ true);
	return nb::make_tuple(transforms, topo);
}


void releaseResult(int requestId) {
	std::scoped_lock lock(g_lock);
	g_results.erase(requestId);
}


void onPresetResult(const MsgScatterPresetResult& result) {
	{
		std::scoped_lock lock(g_lock);
		g_presetResults[result.requestId] = result;
	}

	// Same non-owning-handle hazard as onResult - hold the GIL across the fetch and the call.
	nb::gil_scoped_acquire gil;

	// Scalars only, like onResult: the parameters are pulled on the main thread by getPresetResult.
	invokePythonCallback(CB_PRESET_RESULT, ZmqServer::get().getPythonCallback(CB_PRESET_RESULT),
	                     result.requestId, result.status, result.errorText);
}


nb::tuple getPresetResult(int requestId) {
	MsgScatterPresetResult result;
	{
		std::scoped_lock lock(g_lock);
		auto it = g_presetResults.find(requestId);
		if (it == g_presetResults.end()) {
			return nb::make_tuple(nb::none(), nb::none());
		}
		result = it->second;
	}

	nb::list plugins;
	for (const auto& plugin : result.plugins) {
		nb::dict attrs;
		for (const auto& param : plugin.params) {
			// A preset holds parameters, never bulk geometry, so no attribute needs an ndarray.
			attrs[nb::cast(param.name)] = Interop::attrValueToPython(param.value, /*preferNdarray*/ false);
		}
		plugins.append(nb::make_tuple(plugin.pluginName, plugin.pluginType, attrs));
	}

	nb::list assetIds;
	if (const auto ids = result.modelAssetIds.getData()) {
		for (const std::string& assetId : *ids) {
			assetIds.append(nb::cast(assetId));
		}
	}

	return nb::make_tuple(plugins, assetIds);
}


void releasePresetResult(int requestId) {
	std::scoped_lock lock(g_lock);
	g_presetResults.erase(requestId);
}


void clear() {
	std::scoped_lock lock(g_lock);
	g_results.clear();
	g_presetResults.clear();
}

} // namespace ScatterPreview
} // namespace VRayForBlender
