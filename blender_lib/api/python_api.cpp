// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <algorithm>
#include <cstring>
#include <mutex>
#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>
#include <nanobind/stl/vector.h>
#include <nanobind/stl/list.h>
#include <nanobind/stl/pair.h>
#include <nanobind/ndarray.h>

#ifdef WITH_OSL
#include <OSL/oslquery.h>
#include "interop/osl.h"
#endif

#include <seh_guard.h>
#include <zmq_message.hpp>
#include <zmq_scatter_message.hpp>
#include "interop/types.h"
#include "interop/conversion.hpp"
#include "interop/import_conversion.h"
#include "interop/utils.hpp"
#include "import/vrscene_import_client.h"
#include "preview/scatter_preview.h"
#include "export/zmq_server.h"
#include "export/scene_exporter.h"
#include "export/scene_exporter_pro.h"
#include "export/scene_exporter_rt.h"
#include "utils/logger.hpp"
#include "vassert.h"


namespace nb = nanobind;
namespace proto = VrayZmqWrapper;

namespace VRayForBlender
{

using namespace Interop;

// Forward declarations
void deleteMainRenderer();

using ExporterUPtr = std::unique_ptr<SceneExporter>;

/// Guards the main exporter state below. Production renders create it from a Blender job thread,
/// while a teardown (e.g. the ZmqServer restart of a license type switch) runs on the main one.
std::mutex mainExporterMutex;
ExporterUPtr mainExporter;

/// Set while a render job on a Blender job thread holds the pointer returned by getMainRenderer(),
/// until it calls releaseMainRenderer(). Interactive sessions are driven from the main thread,
/// where a teardown cannot overlap with them, so they never claim the exporter.
const SceneExporter* mainExporterJobOwner = nullptr;

/// Main exporters a teardown had to hand over to a still-running job instead of destroying them
/// under it. Each is freed by that job's own thread, in releaseMainRenderer().
std::list<SceneExporter*> detachedMainExporters;

/// The exporters of the material previews currently being rendered, each on its own Blender job
/// thread which holds a raw pointer to it for the whole render. Owned by those threads, hence the
/// raw pointers: a teardown must never destroy an exporter a thread is still exporting into, so
/// entries are only ever freed by deletePreviewRenderer().
std::mutex previewExportersMutex;
std::list<SceneExporter*> previewExporters;

/// The single Chaos Scatter preview session (shared by all scatter objects; requests carry
/// the scatter plugin name). Lazily created by scatterPreviewStart.
ExporterUPtr scatterExporter;


/// Check renderer parameter and return the exporter object
inline VRayForBlender::SceneExporter* getExporter(const nb::object& renderer)
{
	auto* exporter = reinterpret_cast<VRayForBlender::SceneExporter*>(PyLong_AsVoidPtr(renderer.ptr()));
	if (!exporter){
		throw std::runtime_error("Empty renderer object");
	}
	return exporter;
}


void init(const std::string& logFile)
{
	Logger::get().addWriter(std::make_unique<ConsoleLogger>());
#ifdef __APPLE__
#if defined(__x86_64__)
    ConsoleLogger().write(LogLevel::Always, "Running VRayBlenderLib in x86_64 mode\n");
#elif defined(__arm64__) || defined(__aarch64__)
    ConsoleLogger().write(LogLevel::Always, "Running VRayBlenderLib in arm64 mode\n");
#endif
#endif
	if (!logFile.empty()) {
		auto fileLogger = std::make_unique<FileLogger>(logFile);

		if (fileLogger->good()) {
			Logger::get().addWriter(std::move(fileLogger));
		}
		else {
			ConsoleLogger().write(LogLevel::Error, "V-Ray for Blender error: Failed to open log file");
		}
	}

	Logger::get().startLogging();
}



std::pair<bool, std::string> start(const ZmqServerArgs& args) {
	try {
		// get() is inside the guard on purpose - it constructs the ZmqServer singleton.
		VrayZmqWrapper::runGuarded([&] { VRayForBlender::ZmqServer::get().start(args); });
	}
	catch (const VrayZmqWrapper::ZmqAbortError& e) {
		return {false, std::string("V-Ray cannot start: ") + e.what()};
	}
	return {true, ""};
}


void stopImpl(bool stopLogging) {
	// Unlock the GIL otherwize we will deadlock on any currently
	// executing Python callback.
	nb::gil_scoped_release noGIL;

	// Only the main renderer is torn down here - each preview exporter belongs to the preview job
	// thread still rendering it, which frees it in deletePreviewRenderer().
	deleteMainRenderer();
	SceneImport::releaseAllImports();
	scatterExporter.reset();
	ScatterPreview::clear();

	ZmqServer::get().stop();

	if (stopLogging) {
		Logger::get().stopLogging();
	}
}


void stop() {
	stopImpl(false);
}


void exit() {
	stopImpl(true);
}

// Indicates that rendering job could be started
bool isInitialized()
{
	// This is a temporary workaround until proper tracking of the status is implemented in
	// the server.
	return true;
	// return ZmqServer::get().vrayInitialized();
}

bool isRunning() {
	return VRayForBlender::ZmqServer::get().isRunning();
}


// Indicates that V-Ray has assigned license
bool hasLicense()
{
	// This is a temporary workaround until proper tracking of the status is implemented in
	// the server.
	return true;
	// return ZmqServer::get().licenseAcquired();
}


/// Whether the exporter type belongs to an interactive session, i.e. one that keeps rendering
/// into a viewport or the VFB, as opposed to the one-shot job of a production or preview render.
inline bool isInteractiveExporterType(proto::ExporterType exporterType) {
	return exporterType == proto::ExporterType::IPR_VIEWPORT ||
	       exporterType == proto::ExporterType::IPR_VFB ||
	       exporterType == proto::ExporterType::VANTAGE_LIVE_LINK;
}


void initializeRenderer(SceneExporter& exporter, ExporterSettings& settings) {
	const bool isInteractive = isInteractiveExporterType(settings.getExporterType());

	ExporterBase* renderPolicy = isInteractive ?
			static_cast<ExporterBase*>(new InteractiveExporter(settings)) :
			static_cast<ExporterBase*>(new ProductionExporter(settings));

	exporter.init(renderPolicy, settings);
}

/// Give up 'mainExporter' and return it for the caller to destroy, or an empty pointer if it had
/// to be handed over to a render job that may still be using it. Callers are expected to have
/// stopped the renderers first - VRayRenderEngine.resetAll() waits for the render job to release
/// the renderer - so a claim still standing here means that wait timed out.
/// Must be called with 'mainExporterMutex' held.
ExporterUPtr relinquishMainExporter() {
	if (mainExporter && (mainExporterJobOwner == mainExporter.get())) {
		Logger::debug("Detaching the main renderer, still in use by a render job");
		detachedMainExporters.push_back(mainExporter.release());
	}

	mainExporterJobOwner = nullptr;
	return std::move(mainExporter);
}


size_t getMainRenderer(ExporterSettings& settings) {
	vassert(settings.exporterType != (int)VrayZmqWrapper::ExporterType::PREVIEW);
	static int zmqProcessID = 0;

	// Zmq protocol may not generate an error when ZmqSever process crashes
	// as it will try to re-establish the connection. This is actually a feature that
	// we are using in order to not have to deal with networking errors. This means
	// however that the exproter needs to be reinitailized after a ZmqServer restart.
	const int zmqCurrentProcessID = ZmqServer::get().getProcessID();

	if (zmqCurrentProcessID == 0) {
		// ZmqServer has not started yet or is currently being restarted
		return 0;
	}
	const bool zmqServerRestarted = zmqCurrentProcessID != zmqProcessID;

	// Declared before the locked scope so that it is destroyed after the mutex is released:
	// ~SceneExporter joins the ZMQ poller thread, which can take a while and would needlessly
	// block every other thread waiting on 'mainExporterMutex'.
	ExporterUPtr staleExporter;
	SceneExporter* exporter = nullptr;

	{
		std::scoped_lock lock(mainExporterMutex);

		if (!mainExporter || zmqServerRestarted || mainExporter->getPluginExporter()->isStopped()) {
			staleExporter = relinquishMainExporter();
			mainExporter.reset(new SceneExporter());
			zmqProcessID = zmqCurrentProcessID;
		}

		exporter = mainExporter.get();
		mainExporterJobOwner = isInteractiveExporterType(settings.getExporterType()) ? nullptr : exporter;
	}

	// Outside the lock: initializeRenderer() talks to the ZmqServer. 'exporter' cannot be freed
	// meanwhile - a teardown racing with us hands it over instead (see relinquishMainExporter).
	initializeRenderer(*exporter, settings);
	return reinterpret_cast<size_t>(exporter);
}


void deleteMainRenderer()  {
	ExporterUPtr staleExporter; // Destroyed after the lock is released - see getMainRenderer().
	{
		std::scoped_lock lock(mainExporterMutex);
		staleExporter = relinquishMainExporter();
	}
}


/// Called by a render job once it is done with the pointer it got from getMainRenderer(). The
/// main exporter is reused across render sessions, so this normally only drops the job's claim on
/// it; one that a teardown had to detach is destroyed here, by the thread that was still using it.
void releaseMainRenderer(const nb::object& renderer) {
	const auto* exporter = getExporter(renderer);    // calls into CPython, so before the GIL drop

	// ~SceneExporter joins the ZMQ poller thread and clears Python callbacks, so drop the GIL.
	nb::gil_scoped_release noGIL;

	ExporterUPtr detached; // Destroyed after the lock is released - see getMainRenderer().
	{
		std::scoped_lock lock(mainExporterMutex);

		if (mainExporterJobOwner == exporter) {
			mainExporterJobOwner = nullptr;
		}

		if (const auto it = std::find(detachedMainExporters.begin(), detachedMainExporters.end(), exporter);
				it != detachedMainExporters.end()) {
			detached.reset(*it);
			detachedMainExporters.erase(it);
		}
	}
}


size_t createPreviewRenderer(ExporterSettings& settings) {

	auto exporter = new SceneExporter();
	{
		std::scoped_lock lock(previewExportersMutex);
		previewExporters.push_back(exporter);
	}

	initializeRenderer(*exporter, settings);
	return reinterpret_cast<size_t>(exporter);
}


void deletePreviewRenderer(const nb::object& renderer) {
	const auto* exporter = getExporter(renderer);    // calls into CPython, so before the GIL drop

	// ~SceneExporter joins the ZMQ poller thread and clears Python callbacks, so drop the GIL.
	nb::gil_scoped_release noGIL;

	ExporterUPtr owned; // Destroyed after the lock is released - see getMainRenderer().
	{
		std::scoped_lock lock(previewExportersMutex);

		if (const auto it = std::find(previewExporters.begin(), previewExporters.end(), exporter);
				it != previewExporters.end()) {
			owned.reset(*it);
			previewExporters.erase(it);
		}
		else {
			vassert(!"The exporter is expected to be present in the exporter list.");
			// A stale or duplicated delete must not corrupt the list, let alone crash Blender.
			Logger::warning("deletePreviewRenderer(): unknown preview renderer, ignoring");
		}
	}
}

void clearScene(const nb::object& renderer)
{
	auto* exporter = getExporter(renderer);
	exporter->clearScene();
}

void clearMainBitmapCache()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlClearBitmapCache{}));
}


void log(const std::string& message, int level, bool raw = false)
{
	Logger::get().log(static_cast<Logger::LogLevel>(level), raw, message);
}


void setLogLevel(int level, bool enableQtLogs)
{
	// ZmqServer logs in two ways : the logs from VRay are sent on the wire, ZMQ server own logs
	// are printed directly to the console used by Blender. The first kind gets its log level
	// from the VRay message and are filtered on the client. The level for the messages printed
	// to the console is set by the following call.

	Logger::get().setLogLevel(static_cast<Logger::LogLevel>(level));
	VRayForBlender::ZmqServer::get().sendMessage(proto::serializeMessage(proto::MsgControlSetLogLevel{level, enableQtLogs}));
}


void openCollaboration(const HostInfo& hi)
{
	proto::MsgControlOpenCollaboration msg;
	msg.hostInfo = {hi.vrayVersion, hi.buildVersion, hi.blenderVersion};
	ZmqServer::get().sendMessage(serializeMessage(msg), true);
}

/// Opens Cosmos Asset Browser
void openCosmos(int browserPage)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnOpenCosmos{browserPage}), true);
}

void calculateDownloadSize(const nb::object& packageIds, const nb::object& revisionIds, const nb::object& missingTextures)
{
	vray::AttrListString attrListPackageIds = toVector<std::string>(packageIds);
	vray::AttrListInt attrListRevisionIds = toVector<int>(revisionIds);
	vray::AttrListString attrListMissingTextures = vray::AttrListString(toVector<std::string>(missingTextures));

	ZmqServer::get().sendMessage(serializeMessage(
		proto::MsgControlOnCosmosCalculateDownloadSize{
			std::move(attrListPackageIds),
			std::move(attrListRevisionIds),
			std::move(attrListMissingTextures)
		}), false
	);
}

void downloadMissingAssets() {
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnCosmosDownloadAssets{}), false);
}

/// Import one or more Cosmos assets by name or package id. The result is delivered
/// asynchronously through the cosmos import callback (setCosmosImportCallback), the
/// same path used by the Cosmos browser.
/// 'instanceTokens', when given, must be positionally parallel to 'assetNames'. Each token is
/// echoed back on the corresponding CosmosAssetSettings as 'setInstanceToken'; used to import
/// the members of an Asset Set and match each result to its manifest instance. A tokened entry
/// is treated as a raw package id, skipping name resolution.
void importCosmosAsset(const nb::object& assetNames, bool applyTriplanarMapping, bool applyRealWorldScale,
	const nb::object& instanceTokens)
{
	vray::AttrListString attrAssetNames = vray::AttrListString(toVector<std::string>(assetNames));
	vray::AttrListString attrInstanceTokens = vray::AttrListString(toVector<std::string>(instanceTokens));

	// A short list would silently leave the trailing entries untokened, which imports them as
	// standalone assets and leaves their Asset Set waiting for replies that never identify it.
	if (!attrInstanceTokens.empty() && (attrInstanceTokens.getCount() != attrAssetNames.getCount())) {
		throw nb::value_error("instanceTokens must be empty or the same length as assetNames");
	}

	ZmqServer::get().sendMessage(serializeMessage(
		proto::MsgControlOnCosmosImportById{
			std::move(attrAssetNames),
			applyTriplanarMapping,
			applyRealWorldScale,
			std::move(attrInstanceTokens)
		}), false
	);
}

/// Triggered by the Blender FileHandler when a Cosmos stub file is dropped on
/// a Blender area. Forwards the package id and the world-space drop position
/// (pre-resolved in the drop operator) to the server so it can call
/// GalaxyClient::importPackage() and run the usual Cosmos import pipeline.
/// The normal MsgControlOnImportAsset will come back carrying the same
/// world position so Python-side importers can place the asset there.
void cosmosDropImport(
	const std::string& packageId,
	int revisionId,
	double worldX,
	double worldY,
	double worldZ,
	const std::string& dropTargetObject,
	int dropTargetSlot,
	bool hasHitNormal,
	double normalX,
	double normalY,
	double normalZ,
	bool applyTriplanar,
	bool applyRealWorldScale,
	bool forceNormalAlign)
{
	proto::MsgControlOnCosmosDropImport msg;
	msg.packageId             = packageId;
	msg.revisionId            = static_cast<uint32_t>(revisionId);
	msg.worldX                = worldX;
	msg.worldY                = worldY;
	msg.worldZ                = worldZ;
	msg.dropTargetObject      = dropTargetObject;
	msg.dropTargetSlot        = dropTargetSlot;
	msg.hasHitNormal          = hasHitNormal;
	msg.normalX               = normalX;
	msg.normalY               = normalY;
	msg.normalZ               = normalZ;
	msg.applyTriplanarMapping = applyTriplanar;
	msg.applyRealWorldScale   = applyRealWorldScale;
	msg.forceNormalAlign      = forceNormalAlign;
	ZmqServer::get().sendMessage(serializeMessage(msg), false);
}

// Opens VFB through control connection
void openVFB()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlShowVfb{true}), true);
}

// Closes VFB through control connection
void closeVFB()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlShowVfb{false}), true);
}


// Resets toggleable VFB toolbar buttons (Render Region and Track Mouse)
void resetVfbToolbar()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlResetVfbToolbar{}), true);
}

// Clears the VFB image. Used on scene load and at the start of IPR VFB / PROD
// renders without a render region so the previous frame is wiped before fresh
// pixels arrive.
void clearVfbImage()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlClearVfbImage{}), true);
}

// Re-applies the Lighting Analysis render element's display settings to the current render
// in the VFB without re-rendering. No-op on the server if no render is running.
void updateLightingAnalysis()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlUpdateLightingAnalysis{}), true);
}

// Opens Chaos Veras seeded with a captured 3D viewport image (the "Viewport Image to Veras"
// menu command). imagePath is an absolute path to a temp image file written by the addon.
void openVerasWithViewport(const std::string& imagePath)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOpenVerasViewport{imagePath}), true);
}

// Opens Chaos Veras seeded with the current VFB image (the "VFB Image to Veras" menu
// command). Pressing the VFB toolbar's own Veras button is handled by the AppSDK directly
// and does not go through this call.
void openVerasWithVfbImage()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOpenVerasVfb{}), true);
}

// Sets the VFB Render Region rectangle, the VFB image size and toolbar button state.
// When 'enabled' is false (or width/height are non-positive), the render region is
// cleared (renders the whole image) and the VFB Render Region toolbar button is
// turned off.
// imgWidth/imgHeight set the VFB image size at the same time so the region coords
// are interpreted against a known canvas; pass <= 0 to leave the image size as-is.
void setVfbRenderRegion(int x, int y, int width, int height, int imgWidth, int imgHeight, bool enabled)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlSetVfbRenderRegion{x, y, width, height, imgWidth, imgHeight, enabled}), true);
}

void setVisualDebuggerEnabled(bool enable)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlSetVisualDebugger{enable}));
}

// Sets VFB alwaysOnTop state
void setVfbOnTop(bool alwaysOnTop)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlSetVfbOnTop{alwaysOnTop}));
}

// Show a dialog to the user.
// @param json - the type and configuration of the dilaog
void showUserDialog(const std::string& json)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlShowUserDialog{json}), true);
}

// Sets telemetry state
void setTelemetryState(bool anonymousState, bool personalyzedState)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlSetTelemetryState{anonymousState, personalyzedState}), false);
}

// Function for opening VFB through RendererController connection
void openVFBWithRenderer(const nb::object& renderer)
{
	auto *exporter = getExporter(renderer);
	exporter->openVFB();
}

// Function for setting VFB "on top" through RendererController connection
void setVfbOnTopWithRenderer(const nb::object& renderer, bool alwaysOnTop)
{
	auto *exporter = getExporter(renderer);
	exporter->setVfbAlwaysOnTop(alwaysOnTop);
}

void pluginCreate(const nb::object& renderer, std::string name, std::string pluginType, bool allowTypeChanges)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginCreate(std::move(name), std::move(pluginType), allowTypeChanges);
}

void pluginRemove(const nb::object& renderer, std::string name)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginRemove(std::move(name));
}


/// Common helper for all plugin property updates. Moves name/attrName through to the exporter.
template<typename T>
void pluginUpdateAttr(const nb::object& renderer, std::string name, std::string attrName, T value, bool animatable = true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(std::move(name), std::move(attrName),
	                                            vray::AttrValue(std::move(value)), animatable);
}

// Multi-arg wrappers: construct the typed value from separate Python args, then forward.
void pluginUpdateColor(const nb::object& renderer, std::string name, std::string attrName, float r, float g, float b, bool animatable=true)
{
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrColor(r, g, b), animatable);
}

void pluginUpdateAColor(const nb::object& renderer, std::string name, std::string attrName, float r, float g, float b, float a, bool animatable=true)
{
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrAColor(vray::AttrColor(r, g, b), a), animatable);
}

void pluginUpdateIntVector(const nb::object& renderer, std::string name, std::string attrName, int x, int y, int z, bool animatable=true)
{
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrList<int>{x, y, z}, animatable);
}

void pluginUpdateVector(const nb::object& renderer, std::string name, std::string attrName, float x, float y, float z, bool animatable=true)
{
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrVector{x, y, z}, animatable);
}

// List wrappers: convert Python list, then forward.
void pluginUpdateStringList(const nb::object& renderer, std::string name, std::string attrName, const nb::object& list)
{
	std::vector<std::string> vec = toVector<std::string>(list);
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrList<std::string>(std::move(vec)), false);
}

void pluginUpdatePluginList(const nb::object& renderer, std::string name, std::string attrName, const nb::object& list, bool animatable=true)
{
	vray::AttrListPlugin pluginList;

	if (nb::hasattr(list, "__len__")) {
		pluginList.reserve(static_cast<int>(nb::len(list)));
	}

	for (const nb::handle& item : list) {
		pluginList.append(nb::cast<vray::AttrPlugin>(item));
	}
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), pluginList, animatable);
}

void pluginUpdateList(const nb::object& renderer, std::string name, std::string attrName, const nb::list& list, std::string listElemTypes, bool animatable=true)
{
	vray::AttrListValue attrList;
	auto listElemTypesIt = listElemTypes.begin();
	pyListToAttrList(attrList, listElemTypesIt, list);
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), attrList, animatable);
}

void pluginResetValue(const nb::object& renderer, std::string name, std::string attrName)
{
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrPlugin(), true);
}

void pluginUpdateIntList(const nb::object& renderer, std::string name, std::string attrName, const nb::object& list, bool animatable=true)
{
	std::vector<int> vec = toVector<int>(list);
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrList<int>(std::move(vec)), animatable);
}

void pluginUpdateFloatList(const nb::object& renderer, std::string name, std::string attrName, const nb::object& list, bool animatable=true)
{
	std::vector<float> vec = toVector<float>(list);
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrList<float>(std::move(vec)), animatable);
}

/// Set a VECTOR_LIST property from a flat float sequence of length 3*N (x,y,z per vector).
/// A general-purpose list setter (like pluginUpdateFloatList); GeomStaticMesh.vertices and the
/// scatter spline/area vertex lists need a true VECTOR_LIST, not a generic value list.
void pluginUpdateVectorList(const nb::object& renderer, std::string name, std::string attrName, const nb::object& floats, bool animatable=true)
{
	const std::vector<float> flat = toVector<float>(floats);
	const int count = static_cast<int>(flat.size() / 3);
	vray::AttrList<vray::AttrVector> vecList(count);
	if (count > 0) {
		std::memcpy(vecList.getData()->data(), flat.data(), static_cast<size_t>(count) * sizeof(vray::AttrVector));
	}
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), std::move(vecList), animatable);
}

// Matrix/Transform wrappers: decode Python matrix, then forward.
void pluginUpdateMatrix(const nb::object& renderer, std::string name, std::string attrName, const nb::object& mat, bool animatable=true)
{
	const auto& vec = fromMat<3>(mat);
	typedef float Matrix3[3][3];
	const Matrix3* m = reinterpret_cast<const Matrix3*>(vec.data());
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrMatrix(*m), animatable);
}

void pluginUpdateTransform(const nb::object& renderer, std::string name, std::string attrName, const nb::object& mat, bool animatable=true)
{
	const auto& vec = fromMat<4>(mat);
	typedef float Matrix4[4][4];
	const Matrix4* m = reinterpret_cast<const Matrix4*>(vec.data());
	pluginUpdateAttr(renderer, std::move(name), std::move(attrName), vray::AttrTransform(*m), animatable);
}

// Special cases that need forceUpdate/recreate flags.
void pluginUpdatePluginDesc(const nb::object& renderer, std::string name, std::string attrName, const vray::AttrPlugin& valuePlugin, bool animatable=true, bool forceUpdate=false)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(std::move(name), std::move(attrName), valuePlugin, animatable, forceUpdate);
}

void pluginReCreateAttr(const nb::object& renderer, std::string name, std::string attrName, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(std::move(name), std::move(attrName), vray::AttrPlugin(), animatable, false, true);
}



//////////////////  Object exporters  ///////////////////////////////

void exportGeometry(const nb::object& renderer, const nb::object& meshData, bool asyncExport)
{
	auto* exporter = getExporter(renderer);

	MeshDataPtr mesh = std::make_shared<MeshData>(meshData);
	exporter->exportMesh(mesh, asyncExport);
}


void exportSmoke(const nb::object& renderer, const nb::object& smokeData)
{
	auto *exporter = getExporter(renderer);

	SmokeDataPtr smoke = std::make_shared<SmokeData>(smokeData);
	exporter->exportSmoke(smoke);
}


void exportHair(const nb::object& renderer, const nb::object& hairData)
{
	auto* exporter = getExporter(renderer);

	HairDataPtr hair = std::make_shared<HairData>(hairData);
	exporter->exportHair(hair);
}


void exportPointCloud(const nb::object& renderer, const nb::object& pcData, bool asyncExport)
{
	auto* exporter = getExporter(renderer);

	PointCloudDataPtr pc = std::make_shared<PointCloudData>(pcData);
	exporter->exportPointCloud(pc, asyncExport);
}


void exportInstancer(const nb::object& renderer, const nb::object& instancerData)
{
	auto* exporter = getExporter(renderer);

	InstancerDataPtr inst = std::make_shared<InstancerData>(instancerData);
	exporter->exportInstancer(inst);
}

// Clear all data in V-Ray up to the specified frame. Use this to implement a sliding
// export window when exporting animations frame by frame.
void clearFrameData(const nb::object& renderer, float upToTime)
{
	auto* exporter = getExporter(renderer);
	exporter->clearFrameData(upToTime);
}


void finishExport(const nb::object& renderer, bool interactive)
{
	auto* exporter = getExporter(renderer);
	exporter->finishExport(interactive);

}

void startExport(const nb::object& renderer, int threadCount)
{
	auto* exporter = getExporter(renderer);
	exporter->startExport(threadCount);

}

// Start collection of timing stats for export tasks
void startStatsCollection(const nb::object& renderer)
{
	auto* exporter = getExporter(renderer);
	exporter->startStatsCollection();
}

// Finish collecting timing stats for export tasks and optionally print the collected stats
void endStatsCollection(const nb::object& renderer, bool printStats, const std::string& title)
{
	auto* exporter = getExporter(renderer);
	exporter->endStatsCollection(printStats, title);
}

// Currently, render sizes cannot be set reliably through the plugin system.
// This method results in a call to VRayRenderer->setSenderSizes()
void setRenderSizes(const nb::object& renderer, const nb::object& sizeData)
{
	auto* exporter = getExporter(renderer);
	exporter->setRenderSizes(fromRenderSizes(sizeData));
}


// This method results in a call to VRayRenderer->setCameraName()
// cameraName - the scene_name property of the active camera plugin
void setCameraName(const nb::object& renderer, const nb::object& cameraName)
{
	auto* exporter = getExporter(renderer);
	exporter->setCameraName(nb::cast<std::string>(cameraName));
}


// Configure resumable rendering before a production render starts.
// outputFileName - pass "" to let V-Ray derive the path from SettingsOutput.img_file
// autosaveSeconds - interval for saving intermediate .vrprog files (0 = only at end)
void setResumableRendering(const nb::object& renderer, bool enabled,
	const std::string& outputFileName, int autosaveSeconds, bool deleteOnSuccess)
{
	auto* exporter = getExporter(renderer);
	exporter->setResumableRendering(enabled, outputFileName, autosaveSeconds, deleteOnSuccess);
}


void syncViewSettings(const nb::object& renderer, const ViewSettings& viewSettings)
{
	auto* exporter = getExporter(renderer);
	exporter->syncView(viewSettings);
}


// Export a .vrscene file through AppSDK
int writeVrscene(const nb::object& renderer, const ExportSceneSettings& exportSettings)
{
	auto* exporter = getExporter(renderer);
	return exporter->writeVrscene(exportSettings);
}

std::pair<bool, std::string> exportProxyFile(const nb::object& renderer, const ProxyExportSettings& proxySettings)
{
	auto* exporter = getExporter(renderer);
	nb::gil_scoped_release noGIL;
	return exporter->exportProxy(proxySettings);
}


nb::list getOslScriptParameters([[maybe_unused]] const std::string& script)
{
#ifndef WITH_OSL
	return nb::list();
#else
	OSL::OSLQuery query;
	nb::list paramList;

	if (getOslQuery(query, script)) {
		for (int c = 0; c < query.nparams(); c++) {
			// Working around linking issues of the OSLQuery::getparam() function
			// TODO: Fix linking so that getparam() could be used
			const OSL::OSLQuery::Parameter *param = query.getparam(static_cast<size_t>(c));

			PyOSLParam pyParam;
			if (pyParam.init(param)) {
				paramList.append(pyParam);
			}
		}
	}
	return paramList;
#endif
}


nb::object getImageImpl(const nb::object& renderer, const std::string& renderPassName = "")
{
	auto* exporter = getExporter(renderer);
	RenderImage image = renderPassName.empty() ? exporter->getImage() : exporter->getRenderPassImage(renderPassName);

	if (image) {
		if (image.w <= 0 || image.h <= 0 || image.channels != 4) {
			Logger::error("Wrong image format %1%x%2%x%3%", image.w, image.h, image.channels);
			return nb::none();
		}

		const size_t shape[3] = { (size_t)image.w, (size_t)image.h, (size_t)image.channels };

		const float* pixels = image.pixels;
		nb::capsule owner(new RenderImage(std::move(image)), [](void* p) noexcept {
			delete static_cast<RenderImage*>(p);
		});

		return nb::ndarray<nb::numpy, const float, nb::c_contig>(
			pixels,
			3,
			shape,
			owner
		).cast();
	}

	return nb::none();
}


// Get image of the composited render channel
nb::object getImage(const nb::object& renderer)
{
	return getImageImpl(renderer);
}


// Get the image for a specific render pass
nb::object getRenderPassImage(const nb::object& renderer, const std::string& passName)
{
	return getImageImpl(renderer, passName);
}


// Gets current status update message from the rendering engine
std::string getEngineUpdateMessage(const nb::object& renderer)
{
	auto *exporter = getExporter(renderer);

	return exporter->getEngineUpdateMessage();
}

// Returns true if the final image has been sent
bool isRenderReady(const nb::object& renderer)
{
	auto *exporter = getExporter(renderer);

	return exporter->isRenderReady();
}


// Checks if there is an updated image for drawing
bool imageWasUpdated(const nb::object& renderer)
{
	auto *exporter = getExporter(renderer);

	return exporter->imageWasUpdated();
}

/// Sets python callback for cosmos assets importing
void setCosmosImportCallback(nb::callable assetImportCallback)
{
	ZmqServer::get().setPythonCallback("assetImport", std::move(assetImportCallback));
}

/// Sets python callback for cosmos assets importing
void setCosmosDownloadSize(nb::callable downloadSizeCallback)
{
	ZmqServer::get().setPythonCallback("setCosmosDownloadSize", std::move(downloadSizeCallback));
}

/// Sets python callback for cosmos assets importing
void setCosmosDownloadAssets(nb::callable downloadAssetsCallback)
{
	ZmqServer::get().setPythonCallback("setCosmosDownloadAssets", std::move(downloadAssetsCallback));
}

/// Start a .vrscene import session on the server. Returns the session id.
/// typeFilter: comma-separated plugin-type prefixes (e.g. "Mtl"); empty imports everything.
int importVrsceneStart(const std::string& filePath, bool skipDefaults, double frameStart, double frameEnd,
                       const std::string& typeFilter)
{
	return SceneImport::startImport(filePath, skipDefaults, frameStart, frameEnd, typeFilter);
}

/// Ask the server to abort a running .vrscene import.
void importVrsceneCancel(int importId)
{
	SceneImport::cancelImport(importId);
}

/// Get the imported plugin data as [(name, type, attributes, animatedAttrNames)].
/// Valid after the import finished callback has reported success.
nb::list getImportedVrscene(int importId, const nb::dict& largeAttrs)
{
	return SceneImport::getImportedScene(importId, largeAttrs);
}

/// Free an import session's native buffers. Numpy arrays already handed out stay valid.
void importVrsceneRelease(int importId)
{
	// The release joins the session's connection thread which may be executing
	// a Python callback; keeping the GIL here would deadlock.
	nb::gil_scoped_release noGIL;
	SceneImport::releaseImport(importId);
}

/// Sets the python callback for .vrscene import progress reports
void setVrsceneImportProgressCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("vrsceneImportProgress", std::move(callback));
}

/// Sets the python callback invoked when a .vrscene import completes
void setVrsceneImportFinishedCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("vrsceneImportFinished", std::move(callback));
}

// ---------------------------------------------------------------------------
// Chaos Scatter preview
//
// The preview session is an ordinary SceneExporter of type SCATTER_PREVIEW, so the whole
// vray.pluginCreate / pluginUpdate* / plugin_utils.updateValue path builds the target meshes,
// model stand-ins, density textures and the GeomScatter plugin on the server's private
// headless renderer with no scatter-specific send code. Only the compute trigger and its
// result are bespoke.
// ---------------------------------------------------------------------------

/// Open (or reuse) the scatter preview session and return its renderer handle, or 0 if the
/// ZmqServer is not up yet.
size_t scatterPreviewStart()
{
	static int zmqProcessID = 0;

	const int zmqCurrentProcessID = ZmqServer::get().getProcessID();
	if (zmqCurrentProcessID == 0) {
		return 0;   // server not started / restarting
	}

	// isStopped() never trips on a server crash (this connection has no ping), so the pid is the
	// only restart signal - see the same guard in getMainRenderer().
	const bool zmqServerRestarted = zmqCurrentProcessID != zmqProcessID;

	if (!scatterExporter || zmqServerRestarted || scatterExporter->getPluginExporter()->isStopped()) {
		scatterExporter.reset(new SceneExporter());
		zmqProcessID = zmqCurrentProcessID;

		ExporterSettings settings;
		settings.exporterType = static_cast<int>(proto::ExporterType::SCATTER_PREVIEW);
		initializeRenderer(*scatterExporter, settings);

		scatterExporter->getPluginExporter()->set_callback_on_scatter_result(
			[](const proto::MsgScatterPreviewResult& result) {
				ScatterPreview::onResult(result);
			});

		scatterExporter->getPluginExporter()->set_callback_on_scatter_preset_result(
			[](const proto::MsgScatterPresetResult& result) {
				ScatterPreview::onPresetResult(result);
			});
	}

	return reinterpret_cast<size_t>(scatterExporter.get());
}


/// Close the scatter preview session and drop stored results.
void scatterPreviewStop()
{
	nb::gil_scoped_release noGIL;
	scatterExporter.reset();
	ScatterPreview::clear();
}


/// Ask the server to compute the preview transforms of an already-built GeomScatter plugin.
void requestScatterPreview(const nb::object& renderer, int requestId, const std::string& scatterPluginName, double time)
{
	auto* exporter = getExporter(renderer);
	proto::MsgScatterPreviewRequest request;
	request.requestId = requestId;
	request.scatterPluginName = scatterPluginName;
	request.time = time;
	exporter->getPluginExporter()->sendPluginMsg(serializeMessage(request));
}


/// Drop a queued preview request (a running computation still finishes but is discarded).
void cancelScatterPreview(const nb::object& renderer, int requestId)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->sendPluginMsg(serializeMessage(proto::MsgScatterPreviewCancel{requestId}));
}


/// Register the Python callback invoked when a preview result arrives
/// (requestId, status, errorText, instanceCount).
void setScatterPreviewCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("scatterPreviewResult", std::move(callback));
}


/// Fetch a completed result as (transforms (N,12) float32, topo (N,) int32) zero-copy ndarrays.
nb::tuple scatterPreviewGetResult(int requestId)
{
	return ScatterPreview::getResult(requestId);
}


/// Free a stored result; arrays already handed to Python stay valid.
void scatterPreviewReleaseResult(int requestId)
{
	ScatterPreview::releaseResult(requestId);
}


/// Ask the server to read a Chaos Scatter preset config (.mbc) and reply with the parameters of
/// the GeomScatter plugin it fills. unitRescale converts preset units (metres) to scene units.
void requestScatterPreset(const nb::object& renderer, int requestId, const std::string& filePath, double unitRescale)
{
	auto* exporter = getExporter(renderer);
	proto::MsgScatterPresetRequest request;
	request.requestId = requestId;
	request.filePath = filePath;
	request.unitRescale = unitRescale;
	exporter->getPluginExporter()->sendPluginMsg(serializeMessage(request));
}


/// Register the Python callback invoked when a preset read completes (requestId, status, errorText).
void setScatterPresetCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("scatterPresetResult", std::move(callback));
}


/// Fetch a completed preset read as ([(pluginName, pluginType, attrs), ...], [assetId, ...]).
nb::tuple scatterPresetGetResult(int requestId)
{
	return ScatterPreview::getPresetResult(requestId);
}


/// Free a stored preset result.
void scatterPresetReleaseResult(int requestId)
{
	ScatterPreview::releasePresetResult(requestId);
}


/// Updates the V-Ray scene path after a scene change
void updateScenePath(const std::string& scenePath) {
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnUpdateScenePath{scenePath}));
}

void checkScannedLicense() {
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnScannedLicenseCheck{}));
}

void setScannedLicenseCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("scannedLicense", std::move(callback));
}

void setScannedParamBlockCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("scannedParamBlock", std::move(callback));
}

void encodeScannedParameters(int materialId, const std::string& nodeName, const std::string& paramsJson)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnScannedEncodeParameters{materialId, nodeName, paramsJson}));
}

/// Sets callaback executed when rendering gets stopped or aborted
void setRenderStoppedCallback(const nb::object& renderer, nb::callable renderStoppedCallback)
{
	auto *exporter = getExporter(renderer);

	return exporter->setRenderStoppedCallback(std::move(renderStoppedCallback));
}

/// Sets callback executed when rendering is started
void setRenderStartCallback(nb::callable renderStartCallback)
{
	ZmqServer::get().setPythonCallback("renderStart", std::move(renderStartCallback));
}


/// Sets callback executed when rendering is aborted because the connection to ZmqServer
/// has been lost.
void setZmqServerAbortCallback(nb::callable zmqServerAbortCallback)
{
	ZmqServer::get().setPythonCallback("zmqServerAbort", std::move(zmqServerAbortCallback));
}

/// Sets callback executed when rendering procedure is reporting progress
float getRenderProgress(const nb::object& renderer)
{
	return getExporter(renderer)->getRenderProgress();
}

/// (stage title, progress in [0, 1]) for the render stage V-Ray is working on.
std::pair<std::string, float> getRenderStage()
{
	return ZmqServer::get().getRenderStage();
}

/// Sets callback executed when VFB is updated
void setVfbSettingsUpdateCallback(nb::callable vfbSettingsUpdateCallback)
{
	ZmqServer::get().setPythonCallback("vfbSettingsUpdate", std::move(vfbSettingsUpdateCallback));
}

/// Sets callback executed when VFB layers are updated
void setVfbLayersUpdateCallback(nb::callable vfbLayersUpdateCallback)
{
	ZmqServer::get().setPythonCallback("vfbLayersUpdate", std::move(vfbLayersUpdateCallback));
}

/// Sets callback executed when "Transfer to Scene" is clicked in VFB Light Mix
void setLightMixTransferToSceneCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("lightMixTransferToScene", std::move(callback));
}

/// Sets callback executed when a VFB context menu action is selected
void setVfbMenuCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("vfbMenu", std::move(callback));
}

/// Sets callback executed when VFB "Add Render Element to Scene" button is clicked
void setAddRenderElementToSceneCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("addRenderElementToScene", std::move(callback));
}

/// Sets callback executed when VFB "Show Messages Window" button is clicked
void setVfbShowMessagesWindowCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("vfbShowMessagesWindow", std::move(callback));
}

/// Sets callback executed when the VFB render region changes
void setVfbRenderRegionChangedCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("vfbRenderRegionChanged", std::move(callback));
}

/// Updating VFB layers
void setVfbLayers(const std::string& vfbLayers)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlUpdateVfbLayers{vfbLayers}));
}

/// Show message in the VFB log
void logVfbMessage(const int level, const std::string& message)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlLogVfbMessage{static_cast<proto::VfbMessageLevel>(level), message}));
}


void requestRenderChannel(const nb::object& renderer, int channelType,
                          const std::string& pluginInstanceName, int subIndex)
{
	getExporter(renderer)->requestRenderChannel(channelType, pluginInstanceName, subIndex);
}


void setElementPasses(const nb::object& renderer, const nb::list& passes)
{
	getExporter(renderer)->setElementPasses(passes);
}


std::string getMetadata(const nb::object& renderer, const std::string& key)
{
	return getExporter(renderer)->getMetadata(key);
}


/// The plugin property values V-Ray wrote during the last finished frame, as a list of
/// (pluginName, propertyName, value) tuples. Empty until a frame has completed.
nb::list getPluginPropertyValues(const nb::object& renderer)
{
	return Interop::pluginPropertyValuesToPython(getExporter(renderer)->getPluginPropertyValues());
}


// Start production rendering session. imageToBlender=false suppresses pixel transfer
// for the whole render - pass renderResultPtr=0 and onImageUpdated=None alongside it.
void renderStart(const nb::object& renderer, size_t renderResultPtr, nb::object onImageUpdated, bool imageToBlender)
{
	auto* exporter = getExporter(renderer);

	auto cbImageUpdated = onImageUpdated.is_none() ? nb::callable() : nb::cast<nb::callable>(onImageUpdated);

	exporter->renderStart(reinterpret_cast<RenderPass *>(renderResultPtr), std::move(cbImageUpdated), imageToBlender);
}


// End production rendering session
void renderEnd(const nb::object& renderer)
{
	getExporter(renderer)->renderEnd();
}


/// Send request for rendering the current frame to the server.
void renderFrame(const nb::object& renderer)
{
	getExporter(renderer)->renderFrame();
}


// Set curent frame in VRay renderer
void setRenderFrame(const nb::object& renderer, float frame)
{
	auto* exporter = getExporter(renderer);
	exporter->setRenderFrame(frame);
}

// Starting an render sequence
void renderSequenceStart(const nb::object& renderer, const nb::object& sequence)
{
	auto *exporter = getExporter(renderer);
	std::vector<int> vec = toVector<int>(sequence);

	exporter->renderSequence(vray::AttrList<int>(std::move(vec)));
}


// Checks if there is an active render job
bool renderJobIsRunning(const nb::object& renderer)
{
	return getExporter(renderer)->isRendering();
}


// Checks if there is an active vrscene export job
bool exportJobIsRunning(const nb::object& renderer)
{
	return getExporter(renderer)->vrsceneExportRunning();
}

// Returns the number of the last rendered frame
int getLastRenderedFrame(const nb::object& renderer)
{
	return getExporter(renderer)->lastRenderedFrame();
}

#ifdef WITH_PROFILING
uint64_t getReceivedImagesCount(const nb::object& renderer)
{
	return getExporter(renderer)->getReceivedImagesCount();
}
#endif

void continueRenderSequence(const nb::object& renderer)
{
	getExporter(renderer)->continueRenderSequence();
}

// Aborts the rendering
void abortRender(const nb::object& renderer)
{
	getExporter(renderer)->abortRender();
}

void requestComputeDevices()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlGetComputeDevices{}));
}

void setUpdateComputeDevicesCallback(nb::callable computeUpdateDevicesCallback)
{
	ZmqServer::get().setPythonCallback("updateComputeDevices", std::move(computeUpdateDevicesCallback));
}

void setComputeDevices(const nb::list& computeDeviceIds, int computeDeviceType)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlSetComputeDevices{
		toAttrList<int>(computeDeviceIds),
		static_cast<proto::ComputeDeviceType>(computeDeviceType)
	}));
}

// Set curent frame in VRay renderer
void setUpdateAvailable(bool hasUpdate)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlSetUpdateAvailable{hasUpdate}));
}

void setAutoUpdateChangedCallback(nb::callable autoUpdateCheckCallback)
{
	ZmqServer::get().setPythonCallback("autoUpdateCheckChanged", std::move(autoUpdateCheckCallback));
}

void setAppUpdateRequestedCallback(nb::callable appUpdateRequestedCallback)
{
	ZmqServer::get().setPythonCallback("appUpdateRequested", std::move(appUpdateRequestedCallback));
}

void setSwitchLicenseToCommunityCallback(nb::callable callback)
{
	ZmqServer::get().setPythonCallback("switchLicenseToCommunity", std::move(callback));
}

NB_MODULE(VRayBlenderLib, m)
{
	m.def(FUN(init),                    nb::arg("logFile"));
	m.def(FUN(start),                   nb::arg("zmqServerArgs"));
	m.def(FUN(exit));
	m.def(FUN(stop));
	m.def(FUN(isInitialized));
	m.def(FUN(isRunning));
	m.def(FUN(hasLicense));

	m.def(FUN(getMainRenderer),         nb::arg("settings"));
	m.def(FUN(releaseMainRenderer),     nb::arg("renderer"));
	m.def(FUN(createPreviewRenderer),   nb::arg("settings"));
	m.def(FUN(deletePreviewRenderer),   nb::arg("renderer"));
	m.def(FUN(clearScene),              nb::arg("renderer"));
	m.def(FUN(clearMainBitmapCache));
	m.def(FUN(log),                     nb::arg("message"), nb::arg("level"), nb::arg("raw") = false);
	m.def(FUN(setLogLevel),             nb::arg("level"), nb::arg("enableQtLogs"));
	m.def(FUN(openCollaboration),       nb::arg("hostInfo"));

	m.def(FUN(openCosmos));
	m.def(FUN(setCosmosImportCallback), nb::arg("assetImportCallback"));
	m.def(FUN(setCosmosDownloadSize),   nb::arg("setCosmosDownloadSize"));
	m.def(FUN(setCosmosDownloadAssets), nb::arg("setCosmosDownloadAssets"));
	m.def(FUN(calculateDownloadSize),   nb::arg("packageId"), nb::arg("revisionId"), nb::arg("missingTextures"));
	m.def(FUN(downloadMissingAssets));
	m.def(FUN(importCosmosAsset), nb::arg("assetNames"), nb::arg("applyTriplanarMapping") = false, nb::arg("applyRealWorldScale") = false,
	                              nb::arg("instanceTokens") = nb::list());
	m.def(FUN(cosmosDropImport),
		nb::arg("packageId"), nb::arg("revisionId"),
		nb::arg("worldX"), nb::arg("worldY"), nb::arg("worldZ"),
		nb::arg("dropTargetObject"),
		nb::arg("dropTargetSlot"),
		nb::arg("hasHitNormal"),
		nb::arg("normalX"), nb::arg("normalY"), nb::arg("normalZ"),
		nb::arg("applyTriplanar"), nb::arg("applyRealWorldScale"),
		nb::arg("forceNormalAlign"));
	m.def(FUN(updateScenePath),   nb::arg("scenePath"));

	m.def(FUN(importVrsceneStart),      nb::arg("filePath"), nb::arg("skipDefaults") = true,
	                                    nb::arg("frameStart") = 0.0, nb::arg("frameEnd") = 0.0,
	                                    nb::arg("typeFilter") = "");
	m.def(FUN(importVrsceneCancel),     nb::arg("importId"));
	m.def(FUN(getImportedVrscene),      nb::arg("importId"), nb::arg("largeAttrs") = nb::dict());
	m.def(FUN(importVrsceneRelease),    nb::arg("importId"));
	m.def(FUN(setVrsceneImportProgressCallback), nb::arg("progressCallback"));
	m.def(FUN(setVrsceneImportFinishedCallback), nb::arg("finishedCallback"));

	m.def(FUN(scatterPreviewStart));
	m.def(FUN(scatterPreviewStop));
	m.def(FUN(requestScatterPreview),   nb::arg("renderer"), nb::arg("requestId"),
	                                    nb::arg("scatterPluginName"), nb::arg("time") = 0.0);
	m.def(FUN(cancelScatterPreview),    nb::arg("renderer"), nb::arg("requestId"));
	m.def(FUN(setScatterPreviewCallback), nb::arg("callback"));
	m.def(FUN(scatterPreviewGetResult), nb::arg("requestId"));
	m.def(FUN(scatterPreviewReleaseResult), nb::arg("requestId"));
	m.def(FUN(requestScatterPreset),    nb::arg("renderer"), nb::arg("requestId"),
	                                    nb::arg("filePath"), nb::arg("unitRescale") = 1.0);
	m.def(FUN(setScatterPresetCallback), nb::arg("callback"));
	m.def(FUN(scatterPresetGetResult),  nb::arg("requestId"));
	m.def(FUN(scatterPresetReleaseResult), nb::arg("requestId"));

	m.def(FUN(checkScannedLicense));
	m.def(FUN(setScannedLicenseCallback),    nb::arg("scannedLicenseCallback"));
	m.def(FUN(setScannedParamBlockCallback), nb::arg("scannedParamBlock"));
	m.def(FUN(encodeScannedParameters),      nb::arg("materialId"), nb::arg("nodeName"), nb::arg("paramsJson"));

	m.def(FUN(openVFB));
	m.def(FUN(closeVFB));
	m.def(FUN(resetVfbToolbar));
	m.def(FUN(clearVfbImage));
	m.def(FUN(updateLightingAnalysis));
	m.def(FUN(openVerasWithViewport), nb::arg("imagePath"));
	m.def(FUN(openVerasWithVfbImage));
	m.def(FUN(setVfbRenderRegion), nb::arg("x"), nb::arg("y"), nb::arg("width"), nb::arg("height"), nb::arg("imgWidth"), nb::arg("imgHeight"), nb::arg("enabled"));
	m.def(FUN(setVisualDebuggerEnabled),     nb::arg("enable"));
	m.def(FUN(setVfbOnTop),                  nb::arg("alwaysOnTop"));
	m.def(FUN(showUserDialog),               nb::arg("json"));
	m.def(FUN(setTelemetryState),            nb::arg("anonymousState_a"), nb::arg("personalizedState"));
	m.def(FUN(openVFBWithRenderer),          nb::arg("renderer"));

	m.def(FUN(setVfbOnTopWithRenderer),      nb::arg("renderer"), nb::arg("alwaysOnTop"));
	m.def(FUN(setRenderStoppedCallback),     nb::arg("renderer"), nb::arg("renderStoppedCallback"));
	m.def(FUN(setRenderStartCallback),       nb::arg("startRenderCallback"));
	m.def(FUN(setZmqServerAbortCallback),    nb::arg("zmqServerAbortCallback"));
	m.def(FUN(getRenderProgress),            nb::arg("renderer"));
	m.def(FUN(getRenderStage));
	m.def(FUN(setVfbSettingsUpdateCallback), nb::arg("vfbSettingsUpdateCallback"));
	m.def(FUN(setAutoUpdateChangedCallback), nb::arg("autoUpdateChangedCallback"));
	m.def(FUN(setAppUpdateRequestedCallback), nb::arg("appUpdateRequestedCallback"));
	m.def(FUN(setSwitchLicenseToCommunityCallback), nb::arg("switchLicenseToCommunityCallback"));

	m.def(FUN(setVfbLayersUpdateCallback),   nb::arg("vfbLayersUpdateCallback"));
	m.def(FUN(setLightMixTransferToSceneCallback), nb::arg("lightMixTransferToSceneCallback"));
	m.def(FUN(setVfbMenuCallback),           nb::arg("vfbMenuCallback"));
	m.def(FUN(setAddRenderElementToSceneCallback), nb::arg("addRenderElementCallback"));
	m.def(FUN(setVfbShowMessagesWindowCallback), nb::arg("vfbShowMessagesWindowCallback"));
	m.def(FUN(setVfbRenderRegionChangedCallback), nb::arg("vfbRenderRegionChangedCallback"));
	m.def(FUN(setVfbLayers),                 nb::arg("vfbLayers"));
	m.def(FUN(logVfbMessage),                nb::arg("level"), nb::arg("message"));

	m.def(FUN(pluginCreate),           nb::arg("renderer"), nb::arg("pluginName"), nb::arg("pluginType"), nb::arg("allowTypeChanges") = false);
	m.def(FUN(pluginRemove),           nb::arg("renderer"), nb::arg("pluginName"));
	m.def("pluginUpdateInt",           &pluginUpdateAttr<int>,         nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("attrValue"), nb::arg("animatable") = true);
	m.def("pluginUpdateFloat",         &pluginUpdateAttr<float>,       nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("attrValue"), nb::arg("animatable") = true);
	m.def("pluginUpdateString",        &pluginUpdateAttr<std::string>, nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("attrValue"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateColor),      nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("r"), nb::arg("g"), nb::arg("b"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateAColor),     nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("r"), nb::arg("g"), nb::arg("b"), nb::arg("a"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateIntVector),  nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("x"), nb::arg("y"), nb::arg("z"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateVector),     nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("x"), nb::arg("y"), nb::arg("z"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateStringList), nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("list"));
	m.def(FUN(pluginUpdateIntList),    nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("list"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateFloatList),  nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("list"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateVectorList), nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("floats"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdatePluginList), nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("list"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateMatrix),     nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("mat"),  nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateTransform));
	m.def(FUN(pluginUpdatePluginDesc), nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("pluginValue"), nb::arg("animatable") = true, nb::arg("forceUpdate") = false);
	m.def(FUN(pluginUpdateList),       nb::arg("renderer"), nb::arg("name"), nb::arg("attrName"), nb::arg("list"), nb::arg("elemTypes"), nb::arg("animatable") = true);
	m.def(FUN(pluginReCreateAttr),     nb::arg("renderer"), nb::arg("name"), nb::arg("attrName"), nb::arg("animatable") = true);
	m.def(FUN(pluginResetValue),       nb::arg("renderer"), nb::arg("name"), nb::arg("attrName"));

	m.def(FUN(exportGeometry),         nb::arg("renderer"), nb::arg("meshData"), nb::arg("asyncExport"));
	m.def(FUN(exportHair),             nb::arg("renderer"), nb::arg("hairData"));
	m.def(FUN(exportPointCloud),       nb::arg("renderer"), nb::arg("pcData"), nb::arg("asyncExport"));
	m.def(FUN(exportSmoke),            nb::arg("renderer"), nb::arg("smokeData"));
	m.def(FUN(exportInstancer),        nb::arg("renderer"), nb::arg("instancerData"));
	m.def(FUN(clearFrameData),         nb::arg("renderer"), nb::arg("upToTime"));


	m.def(FUN(startExport),            nb::arg("renderer"), nb::arg("threadCount"));
	m.def(FUN(finishExport),           nb::arg("renderer"), nb::arg("interactive"));
	m.def(FUN(writeVrscene),           nb::arg("renderer"), nb::arg("exportSettings"));
	m.def(FUN(exportProxyFile),		nb::arg("renderer"), nb::arg("proxySettings"));
	m.def(FUN(startStatsCollection),   nb::arg("renderer"));
	m.def(FUN(endStatsCollection),     nb::arg("renderer"), nb::arg("printStats"), nb::arg("title"));
	m.def(FUN(setRenderSizes),         nb::arg("renderer"), nb::arg("sizeData"));
	m.def(FUN(setCameraName),          nb::arg("renderer"), nb::arg("cameraName"));
	m.def(FUN(setResumableRendering),  nb::arg("renderer"), nb::arg("enabled"),
	                                   nb::arg("outputFileName") = "", nb::arg("autosaveSeconds") = 0,
	                                   nb::arg("deleteOnSuccess") = false);
	m.def(FUN(syncViewSettings),       nb::arg("renderer"), nb::arg("viewSettings"));

	m.def(FUN(getOslScriptParameters), nb::arg("script"));

	m.def(FUN(getImage),               nb::arg("renderer"));
	m.def(FUN(getRenderPassImage),     nb::arg("renderer"), nb::arg("passName"));
	m.def(FUN(getEngineUpdateMessage), nb::arg("renderer"));
	m.def(FUN(isRenderReady),          nb::arg("renderer"));
	m.def(FUN(imageWasUpdated),        nb::arg("renderer"));

	m.def(FUN(requestRenderChannel),   nb::arg("renderer"), nb::arg("channelType"),
	                                   nb::arg("pluginInstanceName") = std::string(),
	                                   nb::arg("subIndex") = 0);
	m.def(FUN(setElementPasses),       nb::arg("renderer"), nb::arg("passes"));
	m.def(FUN(getMetadata),            nb::arg("renderer"), nb::arg("key"));
	m.def(FUN(getPluginPropertyValues), nb::arg("renderer"));
	m.def(FUN(renderStart),            nb::arg("renderer"), nb::arg("renderResult"), nb::arg("onImageUpdated").none(), nb::arg("imageToBlender") = true);
	m.def(FUN(renderEnd),              nb::arg("renderer"));
	m.def(FUN(renderFrame),            nb::arg("renderer"));
	m.def(FUN(setRenderFrame),         nb::arg("renderer"), nb::arg("frame"));
	m.def(FUN(renderSequenceStart),    nb::arg("renderer"), nb::arg("sequence"));
	m.def(FUN(renderJobIsRunning),     nb::arg("renderer"));
	m.def(FUN(exportJobIsRunning),     nb::arg("renderer"));
	m.def(FUN(getLastRenderedFrame),   nb::arg("renderer"));

#ifdef WITH_PROFILING
	m.def(FUN(getReceivedImagesCount), nb::arg("renderer"));
#endif

	m.def(FUN(continueRenderSequence), nb::arg("renderer"));
	m.def(FUN(abortRender),            nb::arg("renderer"));
	m.def(FUN(requestComputeDevices));
	m.def(FUN(setUpdateComputeDevicesCallback), nb::arg("updateComputeDevicesCallback"));
	m.def(FUN(setComputeDevices),      nb::arg("computeDeviceIds"), nb::arg("computeDeviceType"));
	m.def(FUN(setUpdateAvailable),     nb::arg("hasUpdate"));

#ifdef WITH_OSL
	nb::class_<PyOSLParam>(m, "OSLParam")
		.def_ro("name", &PyOSLParam::name)
		.def_ro("socketType", &PyOSLParam::socketType)
		.def_ro("socketDefaultValue", &PyOSLParam::socketDefaultValue)
		.def_ro("isOutputSocket", &PyOSLParam::isOutputSocket);
#endif

	nb::class_<ExporterSettings>(m, "ExporterSettings")
		.def(nb::init<>())
		.def("setDRHosts", &ExporterSettings::setDRHosts, nb::arg("hosts"))
		.ADD_RW_PROPERTY(ExporterSettings, closeVfbOnStop)
		.ADD_RW_PROPERTY(ExporterSettings, exporterType)
		.ADD_RW_PROPERTY(ExporterSettings, drUse)
		.ADD_RW_PROPERTY(ExporterSettings, drRenderOnlyOnHosts)
		.ADD_RW_PROPERTY(ExporterSettings, remoteDispatcher)
		.ADD_RW_PROPERTY(ExporterSettings, separateFiles)
		.ADD_RW_PROPERTY(ExporterSettings, previewDir)
		.ADD_RW_PROPERTY(ExporterSettings, profilerMode)
		.ADD_RW_PROPERTY(ExporterSettings, profilerMaxDepth)
		.ADD_RW_PROPERTY(ExporterSettings, profilerOutputDirectory)
		.ADD_RW_PROPERTY(ExporterSettings, profilerSceneName)
		.ADD_RW_PROPERTY(ExporterSettings, drHosts)
		.ADD_RW_PROPERTY(ExporterSettings, renderThreads);


	nb::class_<ViewSettings>(m, "ViewSettings")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(ViewSettings, renderMode)
		.ADD_RW_PROPERTY(ViewSettings, vfbFlags)
		.ADD_RW_PROPERTY(ViewSettings, viewportImageQuality)
		.ADD_RW_PROPERTY(ViewSettings, viewportImageType);


	nb::class_<MeshExportOptions>(m, "MeshExportOptions")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(MeshExportOptions, mergeChannelVerts)
		.ADD_RW_PROPERTY(MeshExportOptions, forceDynamicGeometry)
		.ADD_RW_PROPERTY(MeshExportOptions, useSubsurfToOSD)
		.ADD_RW_PROPERTY(MeshExportOptions, exportEdgeVisibility);

	nb::class_<Subdiv>(m, "Subdiv")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(Subdiv, level)
		.ADD_RW_PROPERTY(Subdiv, type)
		.ADD_RW_PROPERTY(Subdiv, enabled)
		.ADD_RW_PROPERTY(Subdiv, useCreases);

	nb::class_<ZmqServerArgs>(m, "ZmqServerArgs")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(ZmqServerArgs, exePath)
		.ADD_RW_PROPERTY(ZmqServerArgs, port)
		.ADD_RW_PROPERTY(ZmqServerArgs, logLevel)
		.ADD_RW_PROPERTY(ZmqServerArgs, enableQtLogs)
		.ADD_RW_PROPERTY(ZmqServerArgs, headlessMode)
		.ADD_RW_PROPERTY(ZmqServerArgs, noHeartbeat)
		.ADD_RW_PROPERTY(ZmqServerArgs, blenderPID)
		.ADD_RW_PROPERTY(ZmqServerArgs, dumpLogFile)
		.ADD_RW_PROPERTY(ZmqServerArgs, vfbSettingsFile)
		.ADD_RW_PROPERTY(ZmqServerArgs, vrayLibPath)
		.ADD_RW_PROPERTY(ZmqServerArgs, appSDKPath)
		.ADD_RW_PROPERTY(ZmqServerArgs, pluginVersion)
		.ADD_RW_PROPERTY(ZmqServerArgs, blenderVersion)
		.ADD_RW_PROPERTY(ZmqServerArgs, licenseType);

	nb::class_<ExportSceneSettings>(m, "ExportSceneSettings")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(ExportSceneSettings, compressed)
		.ADD_RW_PROPERTY(ExportSceneSettings, hexArrays)
		.ADD_RW_PROPERTY(ExportSceneSettings, hexTransforms)
		.ADD_RW_PROPERTY(ExportSceneSettings, separateFiles)
		.ADD_RW_PROPERTY(ExportSceneSettings, cloudExport)
		.ADD_RW_PROPERTY(ExportSceneSettings, pluginTypes)
		.ADD_RW_PROPERTY(ExportSceneSettings, hostAppString)
		.ADD_RW_PROPERTY(ExportSceneSettings, filePath);

	nb::class_<ProxyExportSettings>(m, "ProxyExportSettings")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(ProxyExportSettings, filePath)
		.ADD_RW_PROPERTY(ProxyExportSettings, elementsPerVoxel)
		.ADD_RW_PROPERTY(ProxyExportSettings, previewFaces)
		.ADD_RW_PROPERTY(ProxyExportSettings, previewType)
		.ADD_RW_PROPERTY(ProxyExportSettings, animOn)
		.ADD_RW_PROPERTY(ProxyExportSettings, startFrame)
		.ADD_RW_PROPERTY(ProxyExportSettings, endFrame);

	nb::class_<HostInfo>(m, "HostInfo")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(HostInfo, vrayVersion)
		.ADD_RW_PROPERTY(HostInfo, buildVersion)
		.ADD_RW_PROPERTY(HostInfo, blenderVersion);

	nb::class_<vray::AttrPlugin>(m, "AttrPlugin")
		.def(nb::init<std::string, std::string>());

	nb::class_<CosmosAssetSettings>(m, "CosmosAssetSettings")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(CosmosAssetSettings, assetType)
		.ADD_RW_PROPERTY(CosmosAssetSettings, matFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, objFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, lightFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, luminaireFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, settingsFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, setInstanceToken)
		.ADD_RW_PROPERTY(CosmosAssetSettings, packageId)
		.ADD_RW_PROPERTY(CosmosAssetSettings, revisionId)
		.ADD_RW_PROPERTY(CosmosAssetSettings, assetName)
		.ADD_RW_PROPERTY(CosmosAssetSettings, isAnimated)
		.ADD_RW_PROPERTY(CosmosAssetSettings, locationsMap)
		.ADD_RW_PROPERTY(CosmosAssetSettings, planeWidth)
		.ADD_RW_PROPERTY(CosmosAssetSettings, planeHeight)
		.ADD_RW_PROPERTY(CosmosAssetSettings, applyTriplanarMapping)
		.ADD_RW_PROPERTY(CosmosAssetSettings, texRealWorldWidth)
		.ADD_RW_PROPERTY(CosmosAssetSettings, texRealWorldHeight)
		.ADD_RW_PROPERTY(CosmosAssetSettings, hasDropCoords)
		.ADD_RW_PROPERTY(CosmosAssetSettings, worldX)
		.ADD_RW_PROPERTY(CosmosAssetSettings, worldY)
		.ADD_RW_PROPERTY(CosmosAssetSettings, worldZ)
		.ADD_RW_PROPERTY(CosmosAssetSettings, dropTargetObject)
		.ADD_RW_PROPERTY(CosmosAssetSettings, dropTargetSlot)
		.ADD_RW_PROPERTY(CosmosAssetSettings, hasHitNormal)
		.ADD_RW_PROPERTY(CosmosAssetSettings, normalX)
		.ADD_RW_PROPERTY(CosmosAssetSettings, normalY)
		.ADD_RW_PROPERTY(CosmosAssetSettings, normalZ)
		.ADD_RW_PROPERTY(CosmosAssetSettings, surfaceAttachment)
		.ADD_RW_PROPERTY(CosmosAssetSettings, forceNormalAlign);

	nb::class_<VrsceneImportResult>(m, "VrsceneImportResult")
		.def(nb::init<>())
		.ADD_RW_PROPERTY(VrsceneImportResult, status)
		.ADD_RW_PROPERTY(VrsceneImportResult, errorText)
		.ADD_RW_PROPERTY(VrsceneImportResult, errorFile)
		.ADD_RW_PROPERTY(VrsceneImportResult, errorLine)
		.ADD_RW_PROPERTY(VrsceneImportResult, pluginCount)
		.ADD_RW_PROPERTY(VrsceneImportResult, paramCount)
		.ADD_RW_PROPERTY(VrsceneImportResult, sceneBaseDir);
#ifdef WITH_DR2
    m.attr("withDR2") = true;
#else
    m.attr("withDR2") = false;
#endif

#ifdef WITH_PROFILING
    m.attr("withProfiling") = true;
#else
    m.attr("withProfiling") = false;
#endif
}


} // VRayForBlender
