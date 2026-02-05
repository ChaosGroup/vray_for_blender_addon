#include "zmq_exporter.h"
#include "zmq_server.h"
#include "vassert.h"
#include "utils/logger.hpp"
#include "plugin_desc.hpp"

#include <filesystem>
#include <fstream>
#include <limits>
#include <random>

#include <zmq_common.hpp>
#include <zmq_message.hpp>

#include <boost/algorithm/string.hpp>
#include <boost/python/dict.hpp>

namespace fs = std::filesystem;


using namespace VRayForBlender;
using namespace VrayZmqWrapper;
using namespace VRayBaseTypes;


namespace
{
	RoutingId generateRoutingId() {
		// Reserve some ids for well-known singleton services, e.g. heartbeat connection
		static const uint64_t RESERVED_RANGE = 1000;

		std::random_device device;
		std::mt19937_64 generator(device());

		uint64_t id = generator();

		while (id <= RESERVED_RANGE){
			id = generator();
		}

		return id;
	}

	std::string formatTime() {
		using namespace std::chrono;

		auto now = system_clock::now();
		// Get duration in milliseconds
		auto ms = duration_cast<milliseconds>(now.time_since_epoch()).count() % 1000;

		const time_t timeNow = system_clock::to_time_t(now);

		// get printable result:
		tm tmNow = {};
		platform::gmtime(timeNow, tmNow);

		std::stringstream ss;
		ss << std::put_time(&tmNow, "%d-%m-%Y %X:") << ms;
		return ss.str();
	}

} // end namespace



#define SAFE_CALL(exp) \
	try{\
		exp;\
	}\
	catch (const std::exception& exc) {\
		Logger::error("Exception in %1%: %2%", #exp, exc.what());\
	}\
	catch (...) {\
		Logger::error("Unknown exception in %1%", #exp);\
	}

///////////////// ZMQ RENDER IMAGE ////////////////////

void ZmqExporter::ZmqRenderImage::update(const VRayBaseTypes::AttrImage &img, ZmqExporter *exp) {
	// Conversions here should match Blender's render pass channel requirements
	if (img.imageType == VRayBaseTypes::AttrImage::ImageType::RGBA_REAL && img.isBucket()) {
		// Merge in the bucket

		if (!pixels) {
			const auto& renderSizes = exp->m_cachedValues.renderSizes;
			
			vassert((renderSizes.imgWidth != 0) && (renderSizes.imgHeight != 0) 
				&& "Invalid render size. Call ZmqExporter::setRenderSize() first.");

			w = renderSizes.imgWidth;
			h = renderSizes.imgHeight;
			channels = 4;

			pixels = new float[w * h * channels];
			memset(pixels, 0, w * h * channels * sizeof(float));

			resetUpdated();
		}

		const float * sourceImage = reinterpret_cast<const float *>(img.data.get());

		updateRegion(sourceImage, {img.x, img.y, img.width, img.height});
	}
	else if (img.imageType == VRayBaseTypes::AttrImage::ImageType::JPG) {
		int clrChannels = 0;
		float * imgData = jpegToPixelData(reinterpret_cast<unsigned char*>(img.data.get()), static_cast<int>(img.size), clrChannels);

		this->channels = clrChannels;
		this->w = img.width;
		this->h = img.height;
		delete[] this->pixels;
		this->pixels = imgData;
	}
	else if (img.imageType == VRayBaseTypes::AttrImage::ImageType::RGBA_REAL ||
		       img.imageType == VRayBaseTypes::AttrImage::ImageType::RGB_REAL ||
		       img.imageType == VRayBaseTypes::AttrImage::ImageType::BW_REAL) {

		bool change_pointer = true;

		const float * imgData = reinterpret_cast<const float *>(img.data.get());
		float * myImage = nullptr;
		int clrChannels = 0;

		switch (img.imageType) {
		case VRayBaseTypes::AttrImage::ImageType::RGBA_REAL:
			if (this->w == img.width && this->h == img.height && this->channels == 4) {
				memcpy(this->pixels, imgData, img.width * img.height * 4 * sizeof(float));
				change_pointer = false;
			}
			else {

				clrChannels = 4;
				myImage = new float[img.width * img.height * clrChannels];
				memcpy(myImage, imgData, img.width * img.height * clrChannels * sizeof(float));
			}

			break;
		case VRayBaseTypes::AttrImage::ImageType::RGB_REAL:
			clrChannels = 3;
			myImage = new float[img.width * img.height * clrChannels];

			for (int c = 0; c < img.width * img.height; ++c) {
				const float * source = imgData + (c * 4);
				float * dest = myImage + (c * channels);

				dest[0] = source[0];
				dest[1] = source[1];
				dest[2] = source[2];
			}

			break;
		case VRayBaseTypes::AttrImage::ImageType::BW_REAL:
			clrChannels = 1;
			myImage = new float[img.width * img.height * clrChannels];

			for (int c = 0; c < img.width * img.height; ++c) {
				const float * source = imgData + (c * 4);
				float * dest = myImage + (c * clrChannels);

				dest[0] = source[0];
			}

			break;
		default:
			Logger::warning("MISSING IMAGE FORMAT CONVERTION FOR %1%", static_cast<int>(img.imageType));
		}

		if (change_pointer) {
			this->channels = clrChannels;
			this->w = img.width;
			this->h = img.height;
			delete[] pixels;
			this->pixels = myImage;
		}
	}
}



///////////////// ZMQ EXPORTER ////////////////////
ZmqExporter::ZmqExporter(ExporterType exporterType) {
	Logger::info("Connect ZmqExporter");

	m_client = std::make_unique<ZmqAgent>(ZmqServer::get().context(), generateRoutingId(), exporterType, true);

	m_client->setMsgCallback([this](zmq::message_t&& payload) {
		SAFE_CALL(handleMsg(payload))
	});

	m_client->setErrorCallback([this, clientPtr = m_client.get()](const std::string& err) {
		SAFE_CALL(handleError(err))
		clientPtr->stop();
		// If the connection has been broken, the stop() will not trigger a state change.
		// Transition to the 'aborted' state immediately.
		m_isRendering = false;
		fireStopEvent(true);
	});

	m_client->setTraceCallback([this, id=shorten(m_client->routingId())](const std::string& msg) {
		Logger::debug("Blender agent %1%: %2%", id, msg);
	});

	ZmqTimeouts timeouts;
	timeouts.handshake = ZmqServer::HANDSHAKE_TIMEOUT;
	timeouts.ping = NO_PING; // Until a way to disable this in settings is implemented. Should be RENDERER_PING_INTERVAL;
	timeouts.inactivity = ZmqServer::RENDERER_INACTIVITY_INTERVAL;

	m_client->run(ZmqServer::get().getEndpoint(), timeouts);
}


ZmqExporter::~ZmqExporter()
{
	freeRenderer();
	m_client->stop(true);
	detach();

	std::scoped_lock lock(m_imgMutex);
	m_layerImages.clear();

	Logger::debug("ZmqExporter deleted");
}


RenderImage ZmqExporter::getRenderChannelImage(RenderChannelType channelType) {

	RenderImage img;

	std::scoped_lock lock(m_imgMutex);

	auto imgIter = m_layerImages.find(channelType);

	if (imgIter != m_layerImages.end()) {
		imgIter = m_layerImages.find(channelType);

		if (imgIter != m_layerImages.end()) {
			RenderImage &storedImage = imgIter->second;
			if (storedImage.pixels) {
				img = std::move(RenderImage::deepCopy(storedImage));
			}
		}
	}
	return img;
}


void ZmqExporter::handleMsg(const zmq::message_t& msg) {

	DeserializerStream stream(reinterpret_cast<const char*>(msg.data()), msg.size());

	MsgType msgType = MsgType::None;
	stream >> msgType;

	switch(msgType) {
	case MsgType::ControlOnLogMessage:
		processControlOnLogMessage(stream);
		break;
	
	case MsgType::ControlOnUpdateVfbLayers:
		processControlOnUpdateVfbLayers(stream);
		break;

	case MsgType::RendererOnImage: {
		const auto& message = deserializeMessage<MsgRendererOnImage>(stream);
		processRendererOnImage(message);
		break;
	}
	case MsgType::RendererOnVRayLog: {
		const auto& message = deserializeMessage<MsgRendererOnVRayLog>(stream);
		processRendererOnVRayLog(message);
		break;
	}
	case MsgType::RendererOnChangeState: {
		const auto& message = deserializeMessage<MsgRendererOnChangeState>(stream);
		processRendererOnChangeState(message);
		break;
	}
	case MsgType::RendererOnAsyncOpComplete: {
		const auto& message = deserializeMessage<MsgRendererOnAsyncOpComplete>(stream);
		processRendererOnAsyncOpComplete(message);
		break;
	}
	case MsgType::RendererOnProgress: {
		const auto& message = deserializeMessage<MsgRendererOnProgress>(stream);
		processRendererOnProgress(message);
		break;
	}
	
	default:
		Logger::error("Invalid message type: %1%", static_cast<int>(msgType));
		vassert(!"Invalid message type");
	}
}


void ZmqExporter::handleError(const std::string& err) {
	Logger::error("Blender: %1%", err);
}


void ZmqExporter::processControlOnLogMessage(DeserializerStream& stream) {
	
	std::scoped_lock l(m_callbacksMutex); 
	
	assert (callback_on_message_update);
	
	const auto& message = deserializeMessage<MsgControlOnLogMessage>(stream);

	std::string logMsg = message.logMessage;

	// Leave only the first row of the message as it will be printed in a status line.
	const auto firstNewLinePos = logMsg.find_first_of("\n\r");
	if (firstNewLinePos != std::string::npos) {
		logMsg.resize(firstNewLinePos);
	}

	callback_on_message_update(logMsg);
}


void ZmqExporter::processControlOnUpdateVfbLayers(DeserializerStream& stream) {

	std::scoped_lock l(m_callbacksMutex); 

	if (callback_on_vfb_layers_updated) {
		const auto& message = deserializeMessage<MsgControlOnUpdateVfbLayers>(stream);
		callback_on_vfb_layers_updated(message.vfbLayersJson);
	}
}


void ZmqExporter::processRendererOnVRayLog(const proto::MsgRendererOnVRayLog& message) {
	std::scoped_lock l(m_callbacksMutex);

	if (callback_on_message_update) {
		std::string msg = message.log;

		// Leave only the first row of the message as it will be printed in a status line.
		const auto firstNewLinePos = msg.find_first_of("\n\r");
		if (firstNewLinePos != std::string::npos) {
			msg.resize(firstNewLinePos);
		}

		callback_on_message_update(msg);
	}
}


void ZmqExporter::processRendererOnImage(const proto::MsgRendererOnImage& message) {
	std::scoped_lock lockCallbacks(m_callbacksMutex);

	bool ready = message.imageSet.sourceType == VRayBaseTypes::ImageSourceType::ImageReady;
	bool updateHostImage = false;

	if (m_settings.getExporterType() == ExporterType::IPR_VIEWPORT) {
		if (readViewportImage()) {
			updateHostImage = true;
		}
	} else {
		{
			std::scoped_lock lock(m_imgMutex);

			for (const auto &img : message.imageSet.images) {
				m_layerImages[img.first].update(
					img.second,
					this
				);
			}
		}

		for (const auto &img : message.imageSet.images) {
			// for result buckets use on bucket ready, otherwise rt image updated callback
			if (img.first == RenderChannelType::RenderChannelTypeNone && img.second.isBucket() && this->callback_on_bucket_ready) {
				this->callback_on_bucket_ready(img.second);
			}
			else {
				updateHostImage = true;
			}
		}
	}

	if (updateHostImage && this->callback_on_rt_image_updated) {
		// Update viewport image or render result image depending on the current rendering mode.
		this->callback_on_rt_image_updated();
	}

	if (ready && this->callback_on_image_ready) {
		this->callback_on_image_ready();
	}
}


void ZmqExporter::processRendererOnChangeState(const proto::MsgRendererOnChangeState& message) {
	switch (message.state) {
	case RendererState::Abort:
		m_isRendering = false;
		fireStopEvent(true);
		break;
	case RendererState::Stopped:
		m_isRendering = false;
		fireStopEvent(false);
		break;
	case RendererState::Continue:
		this->m_lastRenderedFrame = message.lastRenderedFrame;
		break;
	case RendererState::Done:
		// This is called when the whole rendering job is done
		break;

	default:
		vassert(!"Receieved unexpected RendererState message from renderer.");
	}
}


void ZmqExporter::processRendererOnAsyncOpComplete(const proto::MsgRendererOnAsyncOpComplete& message) {
	std::scoped_lock l(m_callbacksMutex);

	if (this->callback_on_async_op_complete) {
		this->callback_on_async_op_complete(message.operation, message.success, message.message);
	}
}


void ZmqExporter::processRendererOnProgress(const proto::MsgRendererOnProgress& message) {
	m_renderProgress = static_cast<float>(message.elements) / message.totalElements;
}


/// Read an image published by ZmqServer in a shared memory region.
/// @return true if the image was read successfully
bool ZmqExporter::readViewportImage() {
	using namespace std::chrono_literals;
	using ImageBuffer = ImageReader::ImageBuffer;

	static const auto SHARED_ACCESS_WAIT = 100ms;

	// The transfer machanism is using 2 types of shared buffers - an ID buffer and one or more data buffers.
	// The server will set the last rendered image data to a data buffer and publish the data buffer's name
	// to the ID buffer. The client will read the active data buffer name from the ID buffer and then read
	// the image data from the data buffer. This two step process is necessary because the data buffer has
	// to be recreated when the size of the image changes (i.e. when the viewport is resized). Both client
	// and server need to close the shared memory object before its name becomes available for reuse. It is
	// not practical to set up another coordination mechanism for this, and it would incur delays in the
	// processing.

	const auto zmqServerPID = std::to_string(ZmqServer::get().getProcessID());

	if (!m_imgIdReader || !m_imgIdReader->isValid()) {
		// Create a reader for the image transfer buffer ID shared region. The ID buffer will live as long
		// as the renderer on the server is alive so we store a reference to it.
		m_imgIdReader = std::make_unique<ImgIdReader>(zmqServerPID, SHARED_IMG_ID_MAPPING_ID);

		if (!m_imgIdReader->open(SHARED_ACCESS_WAIT)) {
			// ZmqServer may have crashed
			Logger::error("Failed to open image ID buffer, error %1%.", m_imgIdReader->getLastError());
			return false;
		}
	}

	// Read the active transfer buffer ID
	int imgID = 0;
	if (!m_imgIdReader->read(SHARED_ACCESS_WAIT, &imgID)) {
		Logger::error("Failed to read image transfer buffer ID, cannot acquire lock.");
		return false;
	}

	// Use the ID we just read to open the correct image transfer buffer. Do not keep references to the data
	// buffer after the data is read as the next image might be published to a different buffer.
	const auto imgBufferID = getImageBufferID(imgID);
	auto imgReader = std::make_unique<ImgReader>(zmqServerPID, imgBufferID);

	if (!imgReader->open(SHARED_ACCESS_WAIT)) {
		Logger::debug("Failed to open image transfer buffer with ID %1%, error: %2%", imgBufferID, imgReader->getLastError());
		return false;
	}

	{
		std::scoped_lock lock(m_imgMutex);

		// Read the image data
		auto& layer = m_layerImages[RenderChannelType::RenderChannelTypeNone];

		ImageBuffer buffer = imgReader->read(SHARED_ACCESS_WAIT, ImageBuffer{layer.w, layer.h, layer.pixels});

		if (!buffer.hasData()) {
			// The buffer has beed created by the server but no image has been copied to it yet.
			return false;
		}

		if (buffer.data != layer.pixels) {
			// A new buffer has been created for the data because the image format has changed.
			delete[] layer.pixels;
			layer.pixels = static_cast<float*>(buffer.data);
			layer.w = buffer.width;
			layer.h = buffer.height;
			layer.channels = 4;
		}
	}

	return true;
}


RenderImage ZmqExporter::getImage() {
	return getRenderChannelImage(RenderChannelType::RenderChannelTypeNone);
}


RenderImage ZmqExporter::getPass(const std::string& name)
{
	RenderImage image;

	if (name == "Combined") {
		image = getImage();
	}
	else if (name == "Depth") {
		image = getRenderChannelImage(RenderChannelTypeVfbZdepth);
	}

	return image;
}


void ZmqExporter::freeRenderer()
{
	m_client->send(serializeMessage(MsgRendererFree{}));
}


bool ZmqExporter::isStopped() const {
	return !m_client->isStopped();
}

void ZmqExporter::clearFrameData(float upTo)
{
	m_client->send(serializeMessage(MsgRendererClearFrameValues{upTo}));
	m_dirty = true;
}


void ZmqExporter::clearScene()
{
	m_cachedValues = ValueCache{}; // Resetting the cached values, becaues have to be set again after the scene is cleared.
	m_client->send(serializeMessage(MsgRendererReset{}));
}

void VRayForBlender::ZmqExporter::abortRender()
{
	m_client->send(serializeMessage(MsgRendererAbort{}));
}

void ZmqExporter::pluginCreate(const std::string& pluginName, const std::string& pluginType, bool allowTypeChanges)
{
	m_client->send(serializeMessage(MsgPluginCreate{pluginName, pluginType, allowTypeChanges}));
	m_dirty = true;
}


void ZmqExporter::pluginRemove(const std::string& pluginName)
{
	m_client->send(serializeMessage(MsgPluginRemove{pluginName}));
	m_dirty = true;
}


void ZmqExporter::pluginUpdate(const std::string& pluginName, const std::string& attrName, const AttrValue& value, bool animatable, bool forceUpdate, bool recreate)
{
	MsgPluginUpdate msg{
		pluginName,
		attrName,
		value
	};
	msg.setAnimatable(animatable);
	msg.setForceUpdate(forceUpdate);
	msg.setReCreateAttribute(recreate);

	sendPluginMsg(serializeMessage(msg));

	m_dirty = true;
}



void ZmqExporter::sendPluginMsg(zmq::message_t && msg)
{
	m_client->send(std::move(msg));
	m_dirty = true;
}



void ZmqExporter::syncView(const ViewSettings& viewSettings)
{

#define CHECK_UPDATE(name, upd)\
	if (m_cachedValues.viewSettings.name != viewSettings.name) {\
		upd;\
		m_cachedValues.viewSettings.name = viewSettings.name;\
	}

	CHECK_UPDATE(vfbFlags, m_client->send(serializeMessage(MsgRendererSetVfbOptions{viewSettings.vfbFlags})));
	CHECK_UPDATE(viewportImageQuality, m_client->send(serializeMessage(MsgRendererSetQuality{viewSettings.viewportImageQuality})));
	CHECK_UPDATE(viewportImageType, m_client->send(serializeMessage(MsgRendererSetViewportImageFormat{static_cast<AttrImage::ImageType>(viewSettings.viewportImageType)})));
	CHECK_UPDATE(renderMode, m_client->send(serializeMessage(MsgRendererSetRenderMode{viewSettings.renderMode})));
#undef CHECK_UPDATE

	m_cachedValues.viewSettings = viewSettings;
}


void ZmqExporter::showVFB()
{
	m_client->send(serializeMessage(MsgRendererSetVfbOptions{static_cast<int>(VfbFlags::Show)}));
}


void ZmqExporter::setVfbAlwaysOnTop(bool alwaysOnTop)
{
	int vfbFlags = static_cast<int>((alwaysOnTop ? VfbFlags::AlwaysOnTop : VfbFlags::None));
	m_client->send(serializeMessage(MsgRendererSetVfbOptions{vfbFlags}));
}


float ZmqExporter::getRenderProgress() const
{
	return m_renderProgress;
}


void ZmqExporter::setCurrentFrame(float frame)
{
	m_currentSceneFrame = frame;
	m_client->send(serializeMessage(MsgRendererSetCurrentFrame{frame}));
	m_dirty = true;
}


float ZmqExporter::getCurrentFrame() const
{
	return m_currentSceneFrame;
}


void ZmqExporter::setRenderSize(const RenderSizes& renderSizes)
{
	std::scoped_lock lock(m_imgMutex);
	if (renderSizes != m_cachedValues.renderSizes) {
		m_cachedValues.renderSizes = renderSizes;
		m_client->send(serializeMessage(MsgRendererResize{renderSizes}));
		m_dirty = true;
	}
}


void ZmqExporter::setCameraName(const std::string &cameraName)
{
	if (m_cachedValues.activeCamera != cameraName) {
		m_cachedValues.activeCamera = cameraName;
		m_client->send(serializeMessage(MsgRendererSetCurrentCamera{cameraName}));
		m_dirty = true;
	}
}


void ZmqExporter::commitChanges()
{
	if (m_dirty){
		m_client->send(serializeMessage(MsgRendererSetCommitAction{CommitAction::CommitNow}));
		m_dirty = false;
	}
}


/// Initialize a VRay rendering session
void ZmqExporter::init(const ExporterSettings & settings)
{
	m_settings = settings;

	try {
		RendererType type = RendererType::None;
		ExporterType exporterType = m_settings.getExporterType();
		
		switch(exporterType){
		case ExporterType::PREVIEW:
			type = RendererType::Preview;
			break;
		case ExporterType::IPR_VIEWPORT:
		case ExporterType::IPR_VFB:
		case ExporterType::VANTAGE_LIVE_LINK:
			type = RendererType::RT;
			break;
		case ExporterType::ANIMATION:
			type = RendererType::Animation;
			break;
		default:
			type = RendererType::SingleFrame;
		}

		m_client->send(serializeMessage(MsgRendererInit{type, m_settings.renderThreads, (int)exporterType}));
		m_client->send(serializeMessage(MsgRendererSetCommitAction{CommitAction::CommitAutoOff}));
		m_client->send(serializeMessage(MsgRendererGetImage{static_cast<int>(RenderChannelType::RenderChannelTypeNone)}));

		if (m_settings.drUse) {
			int drFlags = DRFlags::EnableDr;
			if (m_settings.drRenderOnlyOnHosts) {
				drFlags = DRFlags::RenderOnlyOnHosts | drFlags;
			}

			const std::vector<std::string>& hostItems = m_settings.drHosts;
			std::string hostsStr;
			hostsStr.reserve(hostItems.size() * 24); // 24 chars per host is enough - e.g 123.123.123.123:12345;
			for (const std::string& host : hostItems) {
				hostsStr += host;
				hostsStr.push_back(';');
			}
			if (!hostsStr.empty())
				hostsStr.pop_back(); // remove last delimiter - ;
			m_client->send(serializeMessage(MsgRendererEnableDistributedRendering{hostsStr, (DRFlags)drFlags, m_settings.remoteDispatcher}));
		}

		m_cachedValues.renderSizes = RenderSizes();
	}
	catch (zmq::error_t& e) {
		Logger::error("Failed to initialize ZMQ client\n%1%", e.what());
	}
}


/// Start rendering the scene
void ZmqExporter::start()
{
	m_layerImages.clear();

	// The view settings should not be set if not changed. Set them to their default values
	// to make sure that none is skipped later due to lack of changes.
	// Those settings should be set in start() rather than in init(), because the production renderer exporter
	// is not destroyed when rendering has has finished, but restarted again.
	// In that case init() isn't called, but the viewsettings could have been changed from Blender's UI.
	// The view settings are exported here
	const ViewSettings& viewSettings = m_cachedValues.viewSettings;
	m_client->send(serializeMessage(MsgRendererSetVfbOptions{viewSettings.vfbFlags}));
	m_client->send(serializeMessage(MsgRendererSetQuality{viewSettings.viewportImageQuality}));
	m_client->send(serializeMessage(MsgRendererSetViewportImageFormat{static_cast<AttrImage::ImageType>(viewSettings.viewportImageType)}));
	m_client->send(serializeMessage(MsgRendererSetRenderMode{viewSettings.renderMode}));

	m_client->send(serializeMessage(MsgRendererStart{}));

	// Production rendering could be aborted and started again.
	// For that reason this flag should be cleared.
	m_isRendering = true;
}


void ZmqExporter::stop() {
	m_client->stop(true);
}


void ZmqExporter::detach() {
	set_callback_on_image_ready(nullptr);      
	set_callback_on_rt_image_updated(nullptr); 
	set_callback_on_message_updated(nullptr);   
	set_callback_on_bucket_ready(nullptr);        
	set_callback_on_vfb_layers_updated(nullptr);
	set_callback_on_render_stopped(nullptr);	  
	set_callback_on_async_op_complete(nullptr);
}

void ZmqExporter::renderSequence(int start, int end, int step)
{
	m_layerImages.clear();

	m_lastRenderedFrame = start - 1;
	m_client->send(serializeMessage(MsgRendererRenderSequence{start, end, step}));


	// Production rendering could be aborted and started again.
	// For that reason this flag should be cleared.
	m_isRendering = true;
}

void ZmqExporter::continueRenderSequence()
{
	m_client->send(serializeMessage(MsgRendererContinueSequence{}));
}


void ZmqExporter::stopRendering()
{
	m_client->send(serializeMessage(MsgRendererStop{}));
}


int ZmqExporter::exportVrscene(const ExportSceneSettings& settings)
{
	if (m_settings.separateFiles) {
		Logger::warning("ZMQ will ignore option 'Separate Files' and export in one file!");
	}

	const fs::path filePath(settings.filePath);
	if (!filePath.has_filename() || filePath.extension().empty()) {
		Logger::error("Invalid vrscene file name.", settings.filePath);
		return false;
	}

	fs::path dirPath(filePath);
	dirPath.remove_filename();

	std::error_code code;
	if (!fs::exists(dirPath) && !fs::create_directories(dirPath, code) && code) {
		Logger::error("Failed to create directory '%1%': %2%", settings.filePath, code.message());
		return false;
	}

	ExportSettings exportSettings;

	exportSettings.compressed = settings.compressed;
	exportSettings.hexArrays = settings.hexArrays;
	exportSettings.hexTransforms = settings.hexTransforms;
	exportSettings.hostAppString = settings.hostAppString;
	exportSettings.filePath = settings.filePath;
	exportSettings.cloudExport = settings.cloudExport;

	std::vector<std::string> pluginTypes;
	boost::split(pluginTypes, settings.pluginTypes, boost::is_any_of(","));

	if (settings.separateFiles) {
		exportSettings.subFileInfo.resize(pluginTypes.size());

		for (size_t i = 0; i < pluginTypes.size(); ++i) {
			exportSettings.subFileInfo[i].pluginType     = pluginTypes[i];
			exportSettings.subFileInfo[i].fileNameSuffix = pluginTypes[i];
		}
	}

	m_client->send(serializeMessage(MsgRendererExportScene{exportSettings}));
	return true;
}


AttrPlugin ZmqExporter::exportPlugin(const PluginDesc& pluginDesc)
{
	if (pluginDesc.pluginID.empty()) {
		Logger::warning("[%1%] PluginDesc.pluginID is not set!", pluginDesc.pluginName);
		return AttrPlugin();
	}

	m_dirty = true;

	const std::string & name = pluginDesc.pluginName;
	AttrPlugin plugin(name);


	m_client->send(serializeMessage(MsgPluginCreate{name, pluginDesc.pluginID}));

	for (auto & attributePairs : pluginDesc.pluginAttrs) {
		const PluginAttr & attr = attributePairs.second;
		if (attr.attrValue.getType() != ValueTypeUnknown) {
			auto msg = serializeMessage(MsgPluginUpdate{
				name,
				attr.attrName,
				attr.attrValue,
				attr.flags
			});
			
			sendPluginMsg(std::move(msg));
		}
	}

	return plugin;
}


void ZmqExporter::fireStopEvent(bool isAborted) {
	RenderStoppedCallback cb;
	{
		std::scoped_lock l(m_callbacksMutex); 	
		cb = this->callback_on_render_stopped;
	}

	if (cb) {
		cb(isAborted);
	}
}
