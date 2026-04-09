// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

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

#include <zmq_message.hpp>
#include "interop/types.h"
#include "interop/conversion.hpp"
#include "interop/utils.hpp"
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

using ExporterPtr = std::unique_ptr<SceneExporter>;
ExporterPtr mainExporter;
std::list<ExporterPtr> previewExporters;


// Indicates that this is a community edition build VRayBlenderLib.
bool isCommunityEdition()
{
#ifdef VRAY_BLENDER_COMMUNITY_EDITION
	return true;
#else
	return false;
#endif 
}


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
	if (args.headlessMode && isCommunityEdition()) {
		return {false, "Community Edition of V-Ray for Blender cannot be run in headless mode"};
	}

	VRayForBlender::ZmqServer::get().start(args);
	return {true, ""};
}


void stopImpl(bool stopLogging) {
	// Unlock the GIL otherwize we will deadlock on any currently
	// executing Python callback.
	nb::gil_scoped_release noGIL;

	deleteMainRenderer();
	previewExporters.clear();

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


void initializeRenderer(SceneExporter& exporter, ExporterSettings& settings) {
	const bool isInteractive =
		settings.getExporterType() == proto::ExporterType::IPR_VIEWPORT ||
		settings.getExporterType() == proto::ExporterType::IPR_VFB ||
		settings.getExporterType() == proto::ExporterType::VANTAGE_LIVE_LINK;

	ExporterBase* renderPolicy = isInteractive ?
			static_cast<ExporterBase*>(new InteractiveExporter(settings)) :
			static_cast<ExporterBase*>(new ProductionExporter(settings));

	exporter.init(renderPolicy, settings);
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

	if (!mainExporter || zmqServerRestarted || mainExporter->getPluginExporter()->isStopped()) {
		mainExporter.reset(new SceneExporter());
		zmqProcessID = zmqCurrentProcessID;
	}

	initializeRenderer(*mainExporter, settings);
	return reinterpret_cast<size_t>(mainExporter.get());
}


void deleteMainRenderer()  {
	mainExporter.reset();
}


size_t createPreviewRenderer(ExporterSettings& settings) {

	auto exporter = new SceneExporter();
	previewExporters.emplace_back(std::unique_ptr<SceneExporter>(exporter));

	initializeRenderer(*exporter, settings);
	return reinterpret_cast<size_t>(exporter);
}


void deletePreviewRenderer(const nb::object& renderer) {
	nb::gil_scoped_release noGIL;
	auto* exporter = getExporter(renderer);
	auto itExporter = std::find_if(previewExporters.begin(), previewExporters.end(), [exporter](ExporterPtr& e) {
		return e.get() == exporter;
	});

	vassert( itExporter != previewExporters.end());

	previewExporters.erase(itExporter);
}

void clearScene(const nb::object& renderer)
{
	auto* exporter = getExporter(renderer);
	exporter->clearScene();
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

void pluginCreate(const nb::object& renderer, const std::string& name, const std::string& pluginType, bool allowTypeChanges)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginCreate(name, pluginType, allowTypeChanges);
}

void pluginRemove(const nb::object& renderer, const std::string& name)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginRemove(name);
}


void pluginUpdateInt(const nb::object& renderer, const std::string& name, const std::string& attrName, int value, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrValue(value), animatable);
}


void pluginUpdateFloat(const nb::object& renderer, const std::string& name, const std::string& attrName, float value, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrValue(value), animatable);
}


void pluginUpdateString(const nb::object& renderer, const std::string& name, const std::string& attrName, const std::string& value, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrValue(value), animatable);
}


void pluginUpdateColor(const nb::object& renderer, const std::string& name, const std::string& attrName, float r, float g, float b, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrColor(r, g, b), animatable);
}


void pluginUpdateAColor(const nb::object& renderer, const std::string& name, const std::string& attrName, float r, float g, float b, float a, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrAColor(vray::AttrColor(r, g, b), a), animatable);
}

void pluginUpdateIntVector(const nb::object& renderer, const std::string& name, const std::string& attrName, int x, int y, int z, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<int>{x, y, z}, animatable);
}


void pluginUpdateVector(const nb::object& renderer, const std::string& name, const std::string& attrName, float x, float y, float z, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrVector{x, y, z}, animatable);
}


void pluginUpdateStringList(const nb::object& renderer, const std::string& name, const std::string& attrName, const nb::object& list)
{
	auto* exporter = getExporter(renderer);
	std::vector<std::string> vec = toVector<std::string>(list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<std::string>(std::move(vec)), false);
}

void pluginUpdatePluginList(const nb::object& renderer, const std::string& name, const std::string& attrName, const nb::object& list, bool animatable=true)
{
	auto *exporter = getExporter(renderer);
	vray::AttrListPlugin pluginList;

	for (const nb::handle& item : list) {
		pluginList.append(nb::cast<vray::AttrPlugin>(item));
	}

	exporter->getPluginExporter()->pluginUpdate(name, attrName, pluginList, animatable);
}


void pluginUpdateList(const nb::object& renderer, const std::string& name, const std::string& attrName, const nb::list& list, std::string listElemTypes, bool animatable=true)
{
	auto *exporter = getExporter(renderer);
	vray::AttrListValue attrList;

	auto listElemTypesIt = listElemTypes.begin();
	pyListToAttrList(attrList, listElemTypesIt, list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, attrList, animatable);
}


void pluginResetValue(const nb::object& renderer, const std::string& name, const std::string& attrName)
{
	auto *exporter = getExporter(renderer);
	// Somewhat unclear what to use for animatable here... We use this for way too many things...
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrPlugin(), true);
}


void pluginUpdateIntList(const nb::object& renderer, const std::string& name, const std::string& attrName, const nb::object& list, bool animatable=true)
{
	auto *exporter = getExporter(renderer);
	std::vector<int> vec = toVector<int>(list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<int>(std::move(vec)), animatable);
}


void pluginUpdateFloatList(const nb::object& renderer, const std::string& name, const std::string& attrName, const nb::object& list, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	std::vector<float> vec = toVector<float>(list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<float>(std::move(vec)), animatable);
}


void pluginUpdateMatrix(const nb::object& renderer, const std::string& name, const std::string& attrName, const nb::object& mat, bool animatable=true)
{
	auto* exporter = getExporter(renderer);

	const auto& vec = fromMat<3>(mat);

	typedef float Matrix3[3][3];
	const Matrix3* m = reinterpret_cast<const Matrix3*>(vec.data());
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrMatrix(*m), animatable);
}


void pluginUpdateTransform(const nb::object& renderer, const std::string& name, const std::string& attrName, const nb::object& mat, bool animatable=true)
{
	auto* exporter = getExporter(renderer);

	const auto& vec = fromMat<4>(mat);

	typedef float Matrix4[4][4];
	const Matrix4* m = reinterpret_cast<const Matrix4*>(vec.data());
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrTransform(*m), animatable);
}


void pluginUpdatePluginDesc(const nb::object& renderer, const std::string& name, const std::string& attrName, const vray::AttrPlugin& valuePlugin, bool animatable=true, bool forceUpdate=false)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, valuePlugin, animatable, forceUpdate);
}

void pluginReCreateAttr(const nb::object& renderer, const std::string& name, const std::string& attrName, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrPlugin(), animatable, false, true);
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

		// Note that we do not pass a stride on purpose... I couldn't get nanobind to make c_contiguious array otherwise...
		float* pixels = image.release();
		nb::capsule owner(pixels, [](void* p) noexcept {
			delete[] static_cast<float*>(p);
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

/// Updates the Cosmos import info after a scene change
void updateCosmosSceneName(const std::string& sceneName) {
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnCosmosUpdateSceneName{sceneName}));
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


// Start production rendering session
void renderStart(const nb::object& renderer, size_t renderResultPtr, nb::object onImageUpdated)
{
	auto* exporter = getExporter(renderer);

	auto cbImageUpdated = onImageUpdated.is_none() ? nb::callable() : nb::cast<nb::callable>(onImageUpdated);

	exporter->renderStart(reinterpret_cast<RenderPass *>(renderResultPtr), std::move(cbImageUpdated));
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

NB_MODULE(VRayBlenderLib, m)
{
	m.def(FUN(init),                    nb::arg("logFile"));
	m.def(FUN(start),                   nb::arg("zmqServerArgs"));
	m.def(FUN(exit));
	m.def(FUN(stop));
	m.def(FUN(isInitialized));
	m.def(FUN(isRunning));
	m.def(FUN(hasLicense));
	m.def(FUN(isCommunityEdition));

	m.def(FUN(getMainRenderer),         nb::arg("settings"));
	m.def(FUN(createPreviewRenderer),   nb::arg("settings"));
	m.def(FUN(deletePreviewRenderer),   nb::arg("renderer"));
	m.def(FUN(clearScene),              nb::arg("renderer"));
	m.def(FUN(log),                     nb::arg("message"), nb::arg("level"), nb::arg("raw") = false);
	m.def(FUN(setLogLevel),             nb::arg("level"), nb::arg("enableQtLogs"));
	m.def(FUN(openCollaboration),       nb::arg("hostInfo"));

	m.def(FUN(openCosmos));
	m.def(FUN(setCosmosImportCallback), nb::arg("assetImportCallback"));
	m.def(FUN(setCosmosDownloadSize),   nb::arg("setCosmosDownloadSize"));
	m.def(FUN(setCosmosDownloadAssets), nb::arg("setCosmosDownloadAssets"));
	m.def(FUN(calculateDownloadSize),   nb::arg("packageId"), nb::arg("revisionId"), nb::arg("missingTextures"));
	m.def(FUN(downloadMissingAssets));
	m.def(FUN(updateCosmosSceneName),   nb::arg("sceneName"));

	m.def(FUN(checkScannedLicense));
	m.def(FUN(setScannedLicenseCallback),    nb::arg("scannedLicenseCallback"));
	m.def(FUN(setScannedParamBlockCallback), nb::arg("scannedParamBlock"));
	m.def(FUN(encodeScannedParameters),      nb::arg("materialId"), nb::arg("nodeName"), nb::arg("paramsJson"));

	m.def(FUN(openVFB));
	m.def(FUN(closeVFB));
	m.def(FUN(resetVfbToolbar));
	m.def(FUN(setVfbOnTop),                  nb::arg("alwaysOnTop"));
	m.def(FUN(showUserDialog),               nb::arg("json"));
	m.def(FUN(setTelemetryState),            nb::arg("anonymousState_a"), nb::arg("personalizedState"));
	m.def(FUN(openVFBWithRenderer),          nb::arg("renderer"));

	m.def(FUN(setVfbOnTopWithRenderer),      nb::arg("renderer"), nb::arg("alwaysOnTop"));
	m.def(FUN(setRenderStoppedCallback),     nb::arg("renderer"), nb::arg("renderStoppedCallback"));
	m.def(FUN(setRenderStartCallback),       nb::arg("startRenderCallback"));
	m.def(FUN(setZmqServerAbortCallback),    nb::arg("zmqServerAbortCallback"));
	m.def(FUN(getRenderProgress),            nb::arg("renderer"));
	m.def(FUN(setVfbSettingsUpdateCallback), nb::arg("vfbSettingsUpdateCallback"));
	m.def(FUN(setAutoUpdateChangedCallback), nb::arg("autoUpdateChangedCallback"));
	m.def(FUN(setAppUpdateRequestedCallback), nb::arg("appUpdateRequestedCallback"));

	m.def(FUN(setVfbLayersUpdateCallback),   nb::arg("vfbLayersUpdateCallback"));
	m.def(FUN(setVfbLayers),                 nb::arg("vfbLayers"));
	m.def(FUN(logVfbMessage),                nb::arg("level"), nb::arg("message"));

	m.def(FUN(pluginCreate),           nb::arg("renderer"), nb::arg("pluginName"), nb::arg("pluginType"), nb::arg("allowTypeChanges") = false);
	m.def(FUN(pluginRemove),           nb::arg("renderer"), nb::arg("pluginName"));
	m.def(FUN(pluginUpdateInt),        nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("attrValue"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateFloat),      nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("attrValue"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateString),     nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("attrValue"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateColor),      nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("r"), nb::arg("g"), nb::arg("b"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateAColor),     nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("r"), nb::arg("g"), nb::arg("b"), nb::arg("a"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateIntVector),  nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("x"), nb::arg("y"), nb::arg("z"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateVector),     nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("x"), nb::arg("y"), nb::arg("z"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateStringList), nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("list"));
	m.def(FUN(pluginUpdateIntList),    nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("list"), nb::arg("animatable") = true);
	m.def(FUN(pluginUpdateFloatList),  nb::arg("renderer"), nb::arg("pluginName"), nb::arg("attrName"), nb::arg("list"), nb::arg("animatable") = true);
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
	m.def(FUN(startStatsCollection),   nb::arg("renderer"));
	m.def(FUN(endStatsCollection),     nb::arg("renderer"), nb::arg("printStats"), nb::arg("title"));
	m.def(FUN(setRenderSizes),         nb::arg("renderer"), nb::arg("sizeData"));
	m.def(FUN(setCameraName),          nb::arg("renderer"), nb::arg("cameraName"));
	m.def(FUN(syncViewSettings),       nb::arg("renderer"), nb::arg("viewSettings"));

	m.def(FUN(getOslScriptParameters), nb::arg("script"));

	m.def(FUN(getImage),               nb::arg("renderer"));
	m.def(FUN(getRenderPassImage),     nb::arg("renderer"), nb::arg("passName"));
	m.def(FUN(getEngineUpdateMessage), nb::arg("renderer"));
	m.def(FUN(isRenderReady),          nb::arg("renderer"));
	m.def(FUN(imageWasUpdated),        nb::arg("renderer"));

	m.def(FUN(renderStart),            nb::arg("renderer"), nb::arg("renderResult"), nb::arg("onImageUpdated").none());
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
		.ADD_RW_PROPERTY(ZmqServerArgs, blenderVersion);

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
		.ADD_RW_PROPERTY(CosmosAssetSettings, packageId)
		.ADD_RW_PROPERTY(CosmosAssetSettings, revisionId)
		.ADD_RW_PROPERTY(CosmosAssetSettings, isAnimated)
		.ADD_RW_PROPERTY(CosmosAssetSettings, locationsMap);
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
