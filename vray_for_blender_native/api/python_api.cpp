#include <Python.h>
#include <weakrefobject.h>
#include <boost/python.hpp>
#include <OSL/oslquery.h>

#include <zmq_message.hpp>

#include "interop/types.h"
#include "interop/osl.h"
#include "interop/conversion.hpp"
#include "interop/utils.hpp"
#include "export/zmq_server.h"
#include "export/scene_exporter.h"
#include "export/scene_exporter_pro.h"
#include "export/scene_exporter_rt.h"
#include "utils/logger.hpp"
#include "utils/platform.h"
#include "vassert.h"


namespace py = boost::python;
namespace np = boost::python::numpy;
using ndarray = boost::python::numpy::ndarray;
namespace proto = VrayZmqWrapper;

namespace VRayForBlender
{

using namespace Interop;

// Forward declarations
void deleteMainRenderer();

using ExporterPtr = std::unique_ptr<SceneExporter>;
ExporterPtr mainExporter;
std::list<ExporterPtr> previewExporters;


/// Check renderer parameter and return the exporter object 
inline VRayForBlender::SceneExporter* getExporter(const py::object& renderer)
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



void start(const ZmqServerArgs& args) {
	VRayForBlender::ZmqServer::get().start(args);
}


void stopImpl(bool stopLogging) {
	// Unlock the GIL otherwize we will deadlock on any currently
	// executing Python callback.
	WithNoGIL noGIL;

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

	if (!mainExporter || zmqServerRestarted || !mainExporter->getPluginExporter()->isStopped()) {
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


void deletePreviewRenderer(py::object renderer) {
	WithNoGIL noGIL;
	auto* exporter = getExporter(renderer);
	auto itExporter = std::find_if(previewExporters.begin(), previewExporters.end(), [exporter](ExporterPtr& e) {
		return e.get() == exporter;
	});

	vassert( itExporter != previewExporters.end());

	previewExporters.erase(itExporter);
}

void clearScene(py::object renderer)
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

void calculateDownloadSize(py::object packageIds, py::object revisionIds, py::object missingTextures)
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
void openVFBWithRenderer(py::object renderer)
{
	auto *exporter = getExporter(renderer);
	exporter->openVFB();
}

// Function for setting VFB "on top" through RendererController connection
void setVfbOnTopWithRenderer(py::object renderer, bool alwaysOnTop)
{
	auto *exporter = getExporter(renderer);
	exporter->setVfbAlwaysOnTop(alwaysOnTop);
}

void pluginCreate(py::object renderer, std::string name, std::string pluginType, bool allowTypeChanges)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginCreate(name, pluginType, allowTypeChanges);
}

void pluginRemove(py::object renderer, std::string name)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginRemove(name);
}


void pluginUpdateInt(py::object renderer, std::string name, std::string attrName, int value, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrValue(value), animatable);
}


void pluginUpdateFloat(py::object renderer, std::string name, std::string attrName, float value, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrValue(value), animatable);
}


void pluginUpdateString(py::object renderer, std::string name, std::string attrName, std::string value, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrValue(value), animatable);
}


void pluginUpdateColor(py::object renderer, std::string name, std::string attrName, float r, float g, float b, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrColor(r, g, b), animatable);
}


void pluginUpdateAColor(py::object renderer, std::string name, std::string attrName, float r, float g, float b, float a, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrAColor(vray::AttrColor(r, g, b), a), animatable);
}

void pluginUpdateIntVector(py::object renderer, std::string name, std::string attrName, int x, int y, int z, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<int>{x, y, z}, animatable);
}


void pluginUpdateVector(py::object renderer, std::string name, std::string attrName, float x, float y, float z, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrVector{x, y, z}, animatable);
}


void pluginUpdateStringList(py::object renderer, std::string name, std::string attrName, py::object list)
{
	auto* exporter = getExporter(renderer);
	std::vector<std::string> vec = toVector<std::string>(list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<std::string>(std::move(vec)), false);
}

void pluginUpdatePluginList(py::object renderer, std::string name, std::string attrName, py::object list, bool animatable=true)
{
	auto *exporter = getExporter(renderer);
	vray::AttrListPlugin plugin_list;

	for (py::stl_input_iterator<vray::AttrPlugin> it(list); it != py::stl_input_iterator<vray::AttrPlugin>(); it++) {
		plugin_list.append(*it);
	}

	exporter->getPluginExporter()->pluginUpdate(name, attrName, plugin_list, animatable);
}


void pluginUpdateList(py::object renderer, std::string name, std::string attrName, py::list list, std::string listElemTypes, bool animatable=true)
{
	auto *exporter = getExporter(renderer);
	vray::AttrListValue attrList;

	auto listElemTypesIt = listElemTypes.begin();
	pyListToAttrList(attrList, listElemTypesIt, list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, attrList, animatable);
}


void pluginResetValue(py::object renderer, std::string name, std::string attrName)
{
	auto *exporter = getExporter(renderer);
	// Somewhat unclear what to use for animatable here... We use this for way too many things...
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrPlugin(), true);
}


void pluginUpdateIntList(py::object renderer, std::string name, std::string attrName, py::object list, bool animatable=true)
{
	auto *exporter = getExporter(renderer);
	std::vector<int> vec = toVector<int>(list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<int>(std::move(vec)), animatable);
}


void pluginUpdateFloatList(py::object renderer, std::string name, std::string attrName, py::object list, bool animatable=true)
{
	auto* exporter = getExporter(renderer);
	std::vector<float> vec = toVector<float>(list);

	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrList<float>(std::move(vec)), animatable);
}


void pluginUpdateMatrix(py::object renderer, std::string name, std::string attrName, py::object mat, bool animatable=true)
{
	auto* exporter = getExporter(renderer);

	const auto& vec = fromMat<3>(mat);

	typedef float Matrix3[3][3];
	const Matrix3* m = reinterpret_cast<const Matrix3*>(vec.data());
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrMatrix(*m), animatable);
}


void pluginUpdateTransform(py::object renderer, std::string name, std::string attrName, py::object mat, bool animatable=true)
{
	auto* exporter = getExporter(renderer);

	const auto& vec = fromMat<4>(mat);

	typedef float Matrix4[4][4];
	const Matrix4* m = reinterpret_cast<const Matrix4*>(vec.data());
	exporter->getPluginExporter()->pluginUpdate(name, attrName, vray::AttrTransform(*m), animatable);
}


void pluginUpdatePluginDesc(py::object renderer, std::string name, std::string attrName, const vray::AttrPlugin& valuePlugin, bool animatable=true, bool forceUpdate=false)
{
	auto* exporter = getExporter(renderer);
	exporter->getPluginExporter()->pluginUpdate(name, attrName, valuePlugin, animatable, forceUpdate);
}




//////////////////  Object exporters  ///////////////////////////////

void exportGeometry(py::object renderer, py::object meshData, bool asyncExport)
{
	auto* exporter = getExporter(renderer);

	MeshDataPtr mesh = std::make_shared<MeshData>(meshData);
	exporter->exportMesh(mesh, asyncExport);
}


void exportSmoke(const py::object& renderer, const py::object& smokeData)
{
	auto *exporter = getExporter(renderer);

	SmokeDataPtr smoke = std::make_shared<SmokeData>(smokeData);
	exporter->exportSmoke(smoke);
}


void exportHair(const py::object& renderer, py::object hairData)
{
	auto* exporter = getExporter(renderer);

	HairDataPtr hair = std::make_shared<HairData>(hairData);
	exporter->exportHair(hair);
}


void exportPointCloud(const py::object& renderer, py::object pcData)
{
	auto* exporter = getExporter(renderer);

	PointCloudDataPtr pc = std::make_shared<PointCloudData>(pcData);
	exporter->exportPointCloud(pc);
}


void exportInstancer(const py::object& renderer, py::object instancerData)
{
	auto* exporter = getExporter(renderer);

	InstancerDataPtr inst = std::make_shared<InstancerData>(instancerData);
	exporter->exportInstancer(inst);
}

// Clear all data in V-Ray up to the specified frame. Use this to implement a sliding
// export window when exporting animations frame by frame.
void clearFrameData(const py::object& renderer, float upToTime)
{
	auto* exporter = getExporter(renderer);
	exporter->clearFrameData(upToTime);
}


void finishExport(py::object renderer, bool interactive)
{
	auto* exporter = getExporter(renderer);
	exporter->finishExport(interactive);
	
}

void startExport(py::object renderer, int threadCount)
{
	auto* exporter = getExporter(renderer);
	exporter->startExport(threadCount);
	
}

// Start collection of timing stats for export tasks
void startStatsCollection(py::object renderer)
{
	auto* exporter = getExporter(renderer);
	exporter->startStatsCollection();
}

// Finish collecting timing stats for export tasks and optionally print the collected stats
void endStatsCollection(py::object renderer, bool printStats, const std::string& title)
{
	auto* exporter = getExporter(renderer);
	exporter->endStatsCollection(printStats, title);
}

// Currently, render sizes cannot be set reliably through the plugin system.
// This method results in a call to VRayRenderer->setSenderSizes()
void setRenderSizes(py::object renderer, py::object sizeData)
{
	auto* exporter = getExporter(renderer);
	exporter->setRenderSizes(fromRenderSizes(sizeData));
}


// This method results in a call to VRayRenderer->setCameraName()
// cameraName - the scene_name property of the active camera plugin
void setCameraName(py::object renderer, py::object cameraName)
{
	auto* exporter = getExporter(renderer);
	exporter->setCameraName(py::extract<std::string>(cameraName));
}


void syncViewSettings(py::object renderer, const ViewSettings& viewSettings)
{
	auto* exporter = getExporter(renderer);
	exporter->syncView(viewSettings);
}


// Export a .vrscene file through AppSDK
int writeVrscene(py::object renderer, const ExportSceneSettings& exportSettings)
{
	auto* exporter = getExporter(renderer);
	return exporter->writeVrscene(exportSettings);
}


py::list getOslScriptParameters(const std::string& script)
{
	OSL::OSLQuery query;
	py::list paramList;

	if (getOslQuery(query, script)) {
		for (int c = 0; c < query.nparams(); c++) {
			// Working around linking issues of the OSLQuery::getparam() function
			// TODO: Fix linking so that getparam() could be used
			const OSL::OSLQuery::Parameter *param = query.getparam(static_cast<size_t>(c));

			PyOSLParam pyParam;
			if (pyParam.init(param)) {
				paramList.append<PyOSLParam>(pyParam);
			}
		}
	}
	return paramList;
}



py::object getImageImpl(py::object renderer, const std::string& renderPassName = "")
{
	auto* exporter = getExporter(renderer);

	RenderImage image = renderPassName.empty() ? exporter->getImage() : exporter->getRenderPassImage(renderPassName);

	if (image) {
		// The capsule is an owner for an opaque value (the data pointer). It is used to allow the newly created 
		// Python array object to mange the lifetime of the memory allocated on the C++ side
		PyObject* capsule = ::PyCapsule_New((void*)image.pixels, NULL, (PyCapsule_Destructor)&deleteNativeArray<float>);
		boost::python::handle<> hCapsule(capsule);
		boost::python::object ownerCapsule(hCapsule);

		if (image.w <= 0 || image.h <= 0 || image.channels != 4) {
			Logger::error("Wrong image format %1%x%2%x%3%", image.w, image.h, image.channels);
		}
		auto shape = py::make_tuple(image.w, image.h, image.channels);
		// Strides, one per dimension
		auto strides = py::make_tuple(image.h * image.channels * sizeof(float), image.channels * sizeof(float), sizeof(float));

		return np::from_data(
			image.release(),
			np::dtype::get_builtin<float>(),
			shape, 
			strides,
			ownerCapsule 
		);
	}
	else {
		// Return None 
		return py::object();
	}
}


// Get image of the composited render channel
py::object getImage(py::object renderer)
{
	return getImageImpl(renderer);
}


// Get the image for a specific render pass
py::object getRenderPassImage(py::object renderer, const std::string& passName)
{
	return getImageImpl(renderer, passName);
}


// Gets current status update message from the rendering engine
std::string getEngineUpdateMessage(py::object renderer)
{
	auto *exporter = getExporter(renderer);

	return exporter->getEngineUpdateMessage();
}

// Returns true if the final image has been sent
bool isRenderReady(py::object renderer)
{
	auto *exporter = getExporter(renderer);

	return exporter->isRenderReady();
}


// Checks if there is an updated image for drawing
bool imageWasUpdated(py::object renderer)
{
	auto *exporter = getExporter(renderer);

	return exporter->imageWasUpdated();
}


py::object weakRefFromObj(py::object obj){
	auto weakRef = PyWeakref_NewRef(obj.ptr(), nullptr);

	// Increase refcount on the weakRef object and obtain a handle 
	// which could be converted to a py::object
	auto handle = py::handle<>(py::borrowed(weakRef));

	return py::object(handle);
}

/// Sets python callback for cosmos assets importing
void setCosmosImportCallback(py::object assetImportCallback)
{
	ZmqServer::get().setPythonCallback("assetImport", weakRefFromObj(assetImportCallback));
}

/// Sets python callback for cosmos assets importing
void setCosmosDownloadSize(py::object downloadSizeCallback)
{
	ZmqServer::get().setPythonCallback("setCosmosDownloadSize", weakRefFromObj(downloadSizeCallback));
}

/// Sets python callback for cosmos assets importing
void setCosmosDownloadAssets(py::object downloadAssetsCallback)
{
	ZmqServer::get().setPythonCallback("setCosmosDownloadAssets", weakRefFromObj(downloadAssetsCallback));
}

/// Updates the Cosmos import info after a scene change
void updateCosmosSceneName(std::string sceneName) {
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnCosmosUpdateSceneName{sceneName}));
}

void checkScannedLicense() {
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnScannedLicenseCheck{}));
}

void setScannedLicenseCallback(py::object callback)
{
	ZmqServer::get().setPythonCallback("scannedLicense", weakRefFromObj(callback));
}

void setScannedParamBlockCallback(py::object callback)
{
	ZmqServer::get().setPythonCallback("scannedParamBlock", weakRefFromObj(callback));
}

void encodeScannedParameters(int materialId, const std::string& nodeName, const std::string& paramsJson)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlOnScannedEncodeParameters{materialId, nodeName, paramsJson}));
}

/// Sets callaback executed when rendering gets stopped or aborted
void setRenderStoppedCallback(py::object renderer, py::object renderStoppedCallback)
{
	auto *exporter = getExporter(renderer);

	return exporter->setRenderStoppedCallback(weakRefFromObj(renderStoppedCallback));
}

/// Sets callback executed when rendering is started
void setRenderStartCallback(py::object renderStartCallback)
{
	ZmqServer::get().setPythonCallback("renderStart", weakRefFromObj(renderStartCallback));
}


/// Sets callback executed when rendering is aborted because the connection to ZmqServer
/// has been lost.
void setZmqServerAbortCallback(py::object zmqServerAbortCallback)
{
	ZmqServer::get().setPythonCallback("zmqServerAbort", weakRefFromObj(zmqServerAbortCallback));
}

/// Sets callback executed when rendering procedure is reporting progress
float getRenderProgress(py::object renderer)
{
	return getExporter(renderer)->getRenderProgress();
}

/// Sets callback executed when VFB is updated
void setVfbSettingsUpdateCallback(py::object vfbSettingsUpdateCallback)
{
	ZmqServer::get().setPythonCallback("vfbSettingsUpdate", weakRefFromObj(vfbSettingsUpdateCallback));
}

/// Sets callback executed when VFB layers are updated
void setVfbLayersUpdateCallback(py::object vfbLayersUpdateCallback)
{
	ZmqServer::get().setPythonCallback("vfbLayersUpdate", weakRefFromObj(vfbLayersUpdateCallback));
}

/// Updating VFB layers
void setVfbLayers(std::string vfbLayers)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlUpdateVfbLayers{vfbLayers}));
}


// Start production rendering session
void renderStart(py::object renderer, 
				size_t renderResultPtr, 
				py::object onImageUpdated)
{
	auto* exporter = getExporter(renderer);

	auto cbImageUpdated = onImageUpdated.is_none() ? py::object() : weakRefFromObj(onImageUpdated);

	exporter->renderStart(reinterpret_cast<RenderPass *>(renderResultPtr), cbImageUpdated);
}


// End production rendering session
void renderEnd(py::object renderer)
{
	getExporter(renderer)->renderEnd();
}


/// Send request for rendering the current frame to the server.
void renderFrame(py::object renderer)
{
	getExporter(renderer)->renderFrame();
}


// Set curent frame in VRay renderer
void setRenderFrame(py::object renderer, float frame)
{
	auto* exporter = getExporter(renderer);
	exporter->setRenderFrame(frame);
}

// Starting an render sequence
void renderSequenceStart(py::object renderer, int start, int end, int step)
{
	getExporter(renderer)->renderSequence(start, end, step);
}

// Checks if there is an active render job
bool renderJobIsRunning(py::object renderer)
{
	return getExporter(renderer)->isRendering();
}


// Checks if there is an active vrscene export job
bool exportJobIsRunning(py::object renderer)
{
	return getExporter(renderer)->vrsceneExportRunning();
}

// Returns the number of the last rendered frame
int getLastRenderedFrame(py::object renderer)
{
	return getExporter(renderer)->lastRenderedFrame();
}

void continueRenderSequence(py::object renderer)
{
	getExporter(renderer)->continueRenderSequence();
}

// Aborts the rendering
void abortRender(py::object renderer)
{
	getExporter(renderer)->abortRender();
}

void requestComputeDevices()
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlGetComputeDevices{}));
}

void setUpdateComputeDevicesCallback(py::object computeUpdateDevicesCallback)
{
	ZmqServer::get().setPythonCallback("updateComputeDevices", weakRefFromObj(computeUpdateDevicesCallback));
}

void setComputeDevices(py::list computeDeviceIds, int computeDeviceType)
{
	ZmqServer::get().sendMessage(serializeMessage(proto::MsgControlSetComputeDevices{
		toAttrList<int>(computeDeviceIds),
		static_cast<proto::ComputeDeviceType>(computeDeviceType)
	}));
}


BOOST_PYTHON_MODULE(VRayBlenderLib)
{
	// Without this, numpy subsys will segfault
	py::numpy::initialize();

	py::def(FUN(init),						(py::args("logFile")));
	py::def(FUN(exit));
	py::def(FUN(start),						py::args("zmqServerArgs"));
	py::def(FUN(stop));
	py::def(FUN(isInitialized));
	py::def(FUN(isRunning));
	py::def(FUN(hasLicense));

	py::def(FUN(getMainRenderer),			(py::args("settings")));
	py::def(FUN(createPreviewRenderer),		(py::args("settings")));
	py::def(FUN(deletePreviewRenderer),		(py::args("renderer")));
	py::def(FUN(clearScene),				(py::args("renderer")));
	py::def(FUN(log),						(py::args("message", "level"), py::arg("raw") = false));
	py::def(FUN(setLogLevel),				(py::args("level", "enableQtLogs")));
	py::def(FUN(openCollaboration),			(py::args("hostInfo")));

	py::def(FUN(openCosmos));
	py::def(FUN(setCosmosImportCallback),	(py::args("assetImportCallback")));
	py::def(FUN(setCosmosDownloadSize),	    (py::args("setCosmosDownloadSize")));
	py::def(FUN(setCosmosDownloadAssets),	(py::args("setCosmosDownloadAssets")));
	py::def(FUN(calculateDownloadSize),		(py::args("packageId", "revisionId", "missingTextures")));
	py::def(FUN(downloadMissingAssets));
	py::def(FUN(updateCosmosSceneName),     (py::args("sceneName")));

	py::def(FUN(checkScannedLicense));
	py::def(FUN(setScannedLicenseCallback), (py::args("scannedLicenseCallback")));
	py::def(FUN(setScannedParamBlockCallback), (py::args("scannedParamBlock")));
	py::def(FUN(encodeScannedParameters),   (py::args("paramsJson")));

	py::def(FUN(openVFB));
	py::def(FUN(closeVFB));
	py::def(FUN(resetVfbToolbar));
	py::def(FUN(setVfbOnTop),				(py::args("alwaysOnTop")));
	py::def(FUN(showUserDialog),			(py::args("json")));
	py::def(FUN(setTelemetryState),			(py::args("anonymousState", "personalizedState")));
	py::def(FUN(openVFBWithRenderer),		(py::args("renderer")));

	py::def(FUN(setVfbOnTopWithRenderer),	(py::args("renderer", "alwaysOnTop")));
	py::def(FUN(setRenderStoppedCallback),  (py::args("renderer", "renderStoppedCallback")));
	py::def(FUN(setRenderStartCallback),	(py::args("startRenderCallback")));
	py::def(FUN(setZmqServerAbortCallback),	(py::args("zmqServerAbortCallback")));
	py::def(FUN(getRenderProgress),			(py::args("renderer")));
	py::def(FUN(setVfbSettingsUpdateCallback), (py::args("vfbSettingsUpdateCallback")));

	py::def(FUN(setVfbLayersUpdateCallback),(py::args("vfbLayersUpdateCallback")));
	py::def(FUN(setVfbLayers),				 (py::args("vfbLayers")));

	py::def(FUN(pluginCreate),				(py::arg("renderer"), py::arg("pluginName"), py::arg("pluginType"), py::arg("allowTypeChanges") = false));
	py::def(FUN(pluginRemove),				(py::args("renderer", "pluginName")));
	py::def(FUN(pluginUpdateInt),			(py::args("renderer", "pluginName", "attrName", "attrValue"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateFloat),			(py::args("renderer", "pluginName", "attrName", "attrValue"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateString),		(py::args("renderer", "pluginName", "attrName", "attrValue"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateColor),			(py::args("renderer", "pluginName", "attrName", "r", "g", "b"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateAColor),		(py::args("renderer", "pluginName", "attrName", "r", "g", "b", "a"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateIntVector),		(py::args("renderer", "pluginName", "attrName", "x", "y", "z"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateVector),	    (py::args("renderer", "pluginName", "attrName", "x", "y", "z"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateStringList),	(py::args("renderer", "pluginName", "attrName", "list")));
	py::def(FUN(pluginUpdateIntList),		(py::args("renderer", "pluginName", "attrName", "list"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateFloatList),		(py::args("renderer", "pluginName", "attrName", "list"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdatePluginList),	(py::args("renderer", "pluginName", "attrName", "list"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateMatrix),		(py::args("renderer", "pluginName", "attrName", "mat"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdateTransform),		(py::args("renderer", "pluginName", "attrName", "mat"), py::arg("animatable")=true));
	py::def(FUN(pluginUpdatePluginDesc),	(py::args("renderer", "pluginName", "attrName", "pluginValue"), py::arg("animatable")=true, py::arg("forceUpdate")=false));
	py::def(FUN(pluginUpdateList),			(py::args("renderer", "name", "attrName", "list", "elemTypes"), py::arg("animatable")=true));
	py::def(FUN(pluginResetValue),			(py::args("renderer", "name", "attrName")));

	py::def(FUN(exportGeometry),			(py::args("renderer", "meshData", "asyncExport")));
	py::def(FUN(exportHair),				(py::args("renderer", "hairData")));
	py::def(FUN(exportPointCloud),			(py::args("renderer", "pcData")));
	py::def(FUN(exportSmoke),				(py::args("renderer", "smokeData")));
	py::def(FUN(exportInstancer),			(py::args("renderer", "instancerData")));
	py::def(FUN(clearFrameData),			(py::args("renderer", "upToTime")));


	py::def(FUN(startExport),				(py::args("renderer", "threadCount")));
	py::def(FUN(finishExport),				(py::args("renderer", "interactive")));
	py::def(FUN(writeVrscene),				(py::args("renderer", "exportSettings")));
	py::def(FUN(startStatsCollection),		(py::args("renderer")));
	py::def(FUN(endStatsCollection),		(py::args("renderer", "printStats", "title")));
	py::def(FUN(setRenderSizes),			(py::args("renderer", "sizeData")));
	py::def(FUN(setCameraName),				(py::args("renderer", "cameraName")));
	py::def(FUN(syncViewSettings),			(py::args("renderer", "viewSettings")));

	py::def(FUN(getOslScriptParameters),	(py::args("script")));
	py::def(FUN(getImage),					(py::args("renderer")));
	py::def(FUN(getRenderPassImage),		(py::args("renderer", "passName")));
	py::def(FUN(getEngineUpdateMessage),	(py::args("renderer")));
	py::def(FUN(isRenderReady),				(py::args("renderer")));
	py::def(FUN(imageWasUpdated),			(py::args("renderer")));

	py::def(FUN(renderStart),				(py::args("renderer", "renderResult", "onImageUpdated")));
	py::def(FUN(renderEnd),					(py::args("renderer")));
	py::def(FUN(renderFrame),				(py::args("renderer")));
	py::def(FUN(setRenderFrame),			(py::args("renderer", "frame")));
	py::def(FUN(renderSequenceStart),		(py::args("renderer", "start", "end")));
	py::def(FUN(renderJobIsRunning),		(py::args("renderer")));
	py::def(FUN(exportJobIsRunning),		(py::args("renderer")));
	py::def(FUN(getLastRenderedFrame),		(py::args("renderer")));
	py::def(FUN(continueRenderSequence),			(py::args("renderer")));
	py::def(FUN(abortRender),				(py::args("renderer")));
	py::def(FUN(requestComputeDevices));
	py::def(FUN(setUpdateComputeDevicesCallback),	(py::args("updateComputeDevicesCallback")));
	py::def(FUN(setComputeDevices),			(py::args("computeDeviceIds", "computeDeviceType")));

	py::class_<PyOSLParam>("OSLParam")
		.add_property("name", &PyOSLParam::name)
		.add_property("socketType", &PyOSLParam::socketType)
		.add_property("socketDefaultValue", &PyOSLParam::socketDefaultValue)
		.add_property("isOutputSocket", &PyOSLParam::isOutputSocket);


	py::class_<ExporterSettings>("ExporterSettings")
		.ADD_RW_PROPERTY(ExporterSettings, closeVfbOnStop)
		.ADD_RW_PROPERTY(ExporterSettings, exporterType)
		.ADD_RW_PROPERTY(ExporterSettings, drUse)
		.ADD_RW_PROPERTY(ExporterSettings, drRenderOnlyOnHosts)
		.ADD_RW_PROPERTY(ExporterSettings, remoteDispatcher)
		.ADD_RW_PROPERTY(ExporterSettings, separateFiles)
		.ADD_RW_PROPERTY(ExporterSettings, previewDir)
		.ADD_RW_PROPERTY(ExporterSettings, drHosts)
		.ADD_RW_PROPERTY(ExporterSettings, renderThreads)
		.def("setDRHosts", &ExporterSettings::setDRHosts, py::args("hosts"));


	py::class_<ViewSettings>("ViewSettings")
		.ADD_RW_PROPERTY(ViewSettings, renderMode)
		.ADD_RW_PROPERTY(ViewSettings, vfbFlags)
		.ADD_RW_PROPERTY(ViewSettings, viewportImageQuality)
		.ADD_RW_PROPERTY(ViewSettings, viewportImageType);


	py::class_<MeshExportOptions>("MeshExportOptions")
		.ADD_RW_PROPERTY(MeshExportOptions, mergeChannelVerts)
		.ADD_RW_PROPERTY(MeshExportOptions, forceDynamicGeometry)
		.ADD_RW_PROPERTY(MeshExportOptions, useSubsurfToOSD);

	py::class_ <Subdiv>("Subdiv")
		.ADD_RW_PROPERTY(Subdiv, level)
		.ADD_RW_PROPERTY(Subdiv, type)
		.ADD_RW_PROPERTY(Subdiv, enabled)
		.ADD_RW_PROPERTY(Subdiv, useCreases);

	py::class_ <ZmqServerArgs>("ZmqServerArgs")
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

	py::class_<ExportSceneSettings>("ExportSceneSettings")
		.ADD_RW_PROPERTY(ExportSceneSettings, compressed)
		.ADD_RW_PROPERTY(ExportSceneSettings, hexArrays)
		.ADD_RW_PROPERTY(ExportSceneSettings, hexTransforms)
		.ADD_RW_PROPERTY(ExportSceneSettings, separateFiles)
		.ADD_RW_PROPERTY(ExportSceneSettings, cloudExport)
		.ADD_RW_PROPERTY(ExportSceneSettings, pluginTypes)
		.ADD_RW_PROPERTY(ExportSceneSettings, hostAppString)
		.ADD_RW_PROPERTY(ExportSceneSettings, filePath);

	py::class_<HostInfo>("HostInfo")
		.ADD_RW_PROPERTY(HostInfo, vrayVersion)
		.ADD_RW_PROPERTY(HostInfo, buildVersion)
		.ADD_RW_PROPERTY(HostInfo, blenderVersion);

	py::class_<vray::AttrPlugin>("AttrPlugin")
		.def(py::init<std::string, std::string>());

	py::class_<CosmosAssetSettings>("CosmosAssetSettings")
		.ADD_RW_PROPERTY(CosmosAssetSettings, assetType)
		.ADD_RW_PROPERTY(CosmosAssetSettings, matFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, objFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, lightFile)
		.ADD_RW_PROPERTY(CosmosAssetSettings, packageId)
		.ADD_RW_PROPERTY(CosmosAssetSettings, revisionId)
		.ADD_RW_PROPERTY(CosmosAssetSettings, isAnimated)
		.ADD_RW_PROPERTY(CosmosAssetSettings, locationsMap);
#ifdef WITH_DR2
    py::scope().attr("withDR2") = true;
#else
    py::scope().attr("withDR2") = false;
#endif
}


} // VRayForBlender
