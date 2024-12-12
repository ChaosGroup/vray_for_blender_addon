#include "zmq_exporter.h"
#include "zmq_server.h"
#include "utils/assert.h"
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
	

	std::string formatTime(){
		using namespace std::chrono;

		auto now = system_clock::now();
		// Get duration in milliseconds
		auto ms = duration_cast<milliseconds>(now.time_since_epoch()).count() % 1000;

		const time_t timeNow = system_clock::to_time_t(now);

		// get printable result:
		tm tmNow = {};
		gmtime_s(&tmNow, &timeNow);
		
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
		Logger::error("Exception in {}: {}", #exp, exc.what());\
	}\
	catch (...) {\
		Logger::error("Unknown exception in {}", #exp);\
	}

///////////////// ZMQ RENDER IMAGE ////////////////////

void ZmqExporter::ZmqRenderImage::update(const VRayBaseTypes::AttrImage &img, ZmqExporter * exp, bool fixImage) {
	// conversions here should match Blender's render pass channel requirements
	if (img.imageType == VRayBaseTypes::AttrImage::ImageType::RGBA_REAL && img.isBucket()) {
		// merge in the bucket

		if (!pixels) {
			w = exp->m_cachedValues.renderSizes.imgWidth;
			h = exp->m_cachedValues.renderSizes.imgHeight;
			channels = 4;

			pixels = new float[w * h * channels];
			memset(pixels, 0, w * h * channels * sizeof(float));

			resetUpdated();
		}

		fixImage = false;
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
			Logger::warning("MISSING IMAGE FORMAT CONVERTION FOR {}", static_cast<int>(img.imageType));
		}

		if (change_pointer) {
			this->channels = clrChannels;
			this->w = img.width;
			this->h = img.height;
			delete[] pixels;
			this->pixels = myImage;
		}
	}

	if (!img.isBucket()) {
		flip();
	}

	if (fixImage) {
		resetAlpha();
		clamp(1.0f, 1.0f);
	}
}



///////////////// ZMQ EXPORTER ////////////////////
ZmqExporter::ZmqExporter(const ExporterSettings & settings)
    : m_settings(settings)
{
	Logger::info("Connect ZmqExporter");
	
	m_client = std::make_unique<ZmqAgent>(ZmqServer::get().context(), generateRoutingId(), settings.getExporterType(), true);

	m_client->setMsgCallback([this](zmq::message_t&& payload) {
		SAFE_CALL(handleMsg(VRayMessage::fromZmqMessage(payload)))
	});

	m_client->setErrorCallback([this, clientPtr = m_client.get()](const std::string& err) {
		SAFE_CALL(handleError(err))
		clientPtr->stop();
		// If the connection has been broken, the stop() will not trigger a state change. 
		// Transition to the 'aborted' state immediately.  
		m_isAborted = true;
	});

	m_client->setTraceCallback([this, id=shorten(m_client->routingId())](const std::string& msg) {
		Logger::debug("Blender agent {}: {}", id, msg);
	});

	ZmqTimeouts timeouts;
	timeouts.handshake = ZmqServer::HANDSHAKE_TIMEOUT;
	timeouts.ping = NO_PING; // Until a way to disable this in settings is implemented. Should be RENDERER_PING_INTERVAL;
	timeouts.inactivity = ZmqServer::RENDERER_INACTIVITY_INTERVAL;   

	m_client->run(ZmqServer::get().getEndpoint(), timeouts);
}


ZmqExporter::~ZmqExporter()
{
	free();

	m_client->stop(true);
	
	std::scoped_lock lock(m_imgMutex);
	m_layerImages.clear();
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


void ZmqExporter::handleMsg(const VRayMessage & message) {
	const auto msgType = message.getType();
	
	switch(msgType) {
	case VRayMessage::Type::VRayLog: {
		if (callback_on_message_update) {
			std::string msg = *message.getValue<AttrString>();

			// Leave only the first row of the message as it will be printed in a status line.
			const auto firstNewLinePos = msg.find_first_of("\n\r");
			if (firstNewLinePos != std::string::npos) {
				msg.resize(firstNewLinePos);
			}

			callback_on_message_update(msg);
		}
	}
	break;

	case VRayMessage::Type::Image: {
		auto * set = message.getValue<VRayBaseTypes::AttrImageSet>();
		bool ready = set->sourceType == VRayBaseTypes::ImageSourceType::ImageReady;
		bool updateHostImage = false;

		if (m_settings.getExporterType() == ExporterType::IPR_VIEWPORT) {
			if (readViewportImage()) {
				updateHostImage = true;
			}	
		}
		else
		{
			{
				std::unique_lock<std::mutex> lock(m_imgMutex);

				for (const auto &img : set->images) {
					m_layerImages[img.first].update(
						img.second,
						this,
						(m_settings.getExporterType() != ExporterType::IPR_VIEWPORT));
				}
			}

			for (const auto &img : set->images) {
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
	break;

	case VRayMessage::Type::ChangeRenderer: {
		if (message.getRendererAction() == VRayMessage::RendererAction::SetRendererState) {
			switch (message.getRendererState()) {
			case VRayMessage::RendererState::Abort:
				Logger::warning("ABORT");
				m_isAborted = true;
				break;
			case VRayMessage::RendererState::Stopped:
				Logger::warning("STOP");
				// 'Stopped' state is entered instead of 'Done' when a preview job is rendered 
				// using an accelerator device. 
				// TODO: Figure out why 
				m_isAborted = (m_settings.getExporterType() != ExporterType::PREVIEW);
				break;
			case VRayMessage::RendererState::Progress:
				renderProgress = *message.getValue<VRayBaseTypes::AttrSimpleType<float>>();
				break;
			case VRayMessage::RendererState::ProgressMessage:
				progressMessage = *message.getValue<VRayBaseTypes::AttrSimpleType<std::string>>();
				break;
			case VRayMessage::RendererState::Continue:
				this->m_lastRenderedFrame = *message.getValue<VRayBaseTypes::AttrSimpleType<int>>();
				break;
			case VRayMessage::RendererState::Done:
				// This is called when the whole rendering job is done
				break;

			default:
				VRAY_ASSERT(!"Receieved unexpected RendererState message from renderer.");
			}
		}
	}
	break;


	case VRayMessage::Type::VfbLayers: {
		if (callback_on_vfb_layers_updated) {
			std::string layersJson = *message.getValue<AttrString>();
			callback_on_vfb_layers_updated(layersJson);
		}
	} break;

	default:
		VRAY_ASSERT(!"Invalid message type");
	}

	if (m_isAborted && this->callback_on_render_stopped) {
		this->callback_on_render_stopped();
	}
}


void ZmqExporter::handleError(const std::string& err) {
	Logger::error("Blender: {}", err);
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
	
	const auto blenderPID = std::to_string(ZmqServer::get().getArgs().blenderPID);

	if (!m_imgIdReader) {
		// Create a reader for the image transfer buffer ID shared region. The ID buffer will live as long
		// as the renderer on the server is alive so we store a reference to it.
		m_imgIdReader = std::make_unique<ImgIdReader>(blenderPID, SHARED_IMG_ID_MAPPING_ID);
		
		if (!m_imgIdReader->open(SHARED_ACCESS_WAIT)) {
			// ZmqServer may have crashed
			Logger::error("Failed to open image ID buffer, error {}.", m_imgIdReader->getLastError());
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
	auto imgReader = std::make_unique<ImgReader>(blenderPID, imgBufferID);
	
	if (!imgReader->open(SHARED_ACCESS_WAIT)) {
		Logger::error("Failed to open image transfer buffer with ID {}, error: {}", imgBufferID, imgReader->getLastError());
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
			assert(layer.w > 0 && layer.h > 0);

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


void ZmqExporter::free()
{
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::Free));
}


void ZmqExporter::clearFrameData(float upTo)
{
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::ClearFrameValues, upTo));
	m_dirty = true;
}


void ZmqExporter::clearScene()
{
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::Reset));
}

void VRayForBlender::ZmqExporter::abortRender()
{
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::Abort));
}

void ZmqExporter::pluginCreate(const std::string& pluginName, const std::string& pluginType)
{
	m_client->send(VRayMessage::msgPluginCreate(pluginName, pluginType));
	
	m_dirty = true;
}


void ZmqExporter::pluginRemove(const std::string& pluginName)
{
	m_client->send(VRayMessage::msgPluginRemove(pluginName));
	m_dirty = true;
}


void ZmqExporter::pluginUpdate(const std::string& pluginName, const std::string& attrName, const AttrValue& value, bool forceUpdate /*=false*/)
{
	sendPluginMsg(pluginName, VRayMessage::msgPluginSetProperty(pluginName, attrName, value, forceUpdate));
	m_dirty = true;
}


void ZmqExporter::sendPluginMsg(const std::string& pluginName, zmq::message_t && msg)
{
	{
		std::lock_guard<std::mutex> lock(m_statsMutex);
		auto& stat = m_exportStats[pluginName];
		++stat.num;
		stat.size += msg.size();
	}

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

	CHECK_UPDATE(vfbFlags, m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetVfbOptions, viewSettings.vfbFlags)));
	CHECK_UPDATE(viewportImageQuality, m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetQuality, viewSettings.viewportImageQuality)));
	CHECK_UPDATE(viewportImageType, m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetViewportImageFormat, viewSettings.viewportImageType)));
	CHECK_UPDATE(renderMode, m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetRenderMode, viewSettings.renderMode)));
#undef CHECK_UPDATE

	m_cachedValues.viewSettings = viewSettings;
}


void ZmqExporter::showVFB()
{
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetVfbOptions, static_cast<int>(VfbFlags::Show)));
}


void ZmqExporter::setVfbAlwaysOnTop(bool alwaysOnTop)
{
	int vfbFlags = static_cast<int>((alwaysOnTop ? VfbFlags::AlwaysOnTop : VfbFlags::None));
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetVfbOptions, vfbFlags));
}


float ZmqExporter::getRenderProgress() const
{
	return renderProgress;
}


void ZmqExporter::setCurrentFrame(float frame)
{
	m_currentSceneFrame = frame;
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetCurrentFrame, frame));
	m_dirty = true;
}


float ZmqExporter::getCurrentFrame() const
{
	return m_currentSceneFrame;
}


void ZmqExporter::setRenderSize(const VRayMessage::RenderSizes& renderSizes)
{
	std::unique_lock<std::mutex> lock(m_imgMutex);
	if (renderSizes != m_cachedValues.renderSizes) {
		m_cachedValues.renderSizes = renderSizes;
		m_client->send(VRayMessage::msgRendererResize(renderSizes));
		m_dirty = true;
	}
}


void ZmqExporter::setCameraPlugin(const std::string &pluginName)
{
	if (m_cachedValues.activeCamera != pluginName) {
		m_cachedValues.activeCamera = pluginName;
		m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetCurrentCamera, pluginName));
		m_dirty = true;
	}
}


void ZmqExporter::commitChanges()     
{
	if (m_dirty){
		m_client->send(VRayMessage::msgRendererAction(
							VRayMessage::RendererAction::SetCommitAction, 
							static_cast<int>(CommitAction::CommitNow)
						));

		if ( m_exportedAttributes > 0 ){
			Logger::info("ZMQ Exporter: Total exported attributes: {}, Size: {} bytes", m_exportedAttributes, m_exportedSize);
		
			std::ofstream f;
			f.open(L"c:/tmp/export_stats.log");
			
			for(const auto& item: m_exportStats){
				f << item.first << ": " << item.second.num << ", " << item.second.size << " bytes" << std::endl; 
			}
		}

		m_dirty = false;
		m_exportedAttributes = 0;
		m_exportedSize = 0;
		m_exportStats.clear();

	}
}


/// Initialize a VRay rendering session
void ZmqExporter::init()
{
	try {
		VRayMessage::RendererType type = VRayMessage::RendererType::None;
		switch(m_settings.getExporterType()){
		case ExporterType::PREVIEW:
			type = VRayMessage::RendererType::Preview;
			break;
		case ExporterType::IPR_VIEWPORT:
		case ExporterType::IPR_VFB:
			type = VRayMessage::RendererType::RT;
			break;
		case ExporterType::ANIMATION:
			type = VRayMessage::RendererType::Animation;
			break;
		default:
			type = VRayMessage::RendererType::SingleFrame;
		}

		VRayMessage::DRFlags drflags = VRayMessage::DRFlags::None;
		if (m_settings.drUse) {
			drflags = VRayMessage::DRFlags::EnableDr;
			if (m_settings.drRenderOnlyOnHosts) {
				drflags = static_cast<VRayMessage::DRFlags>(static_cast<int>(VRayMessage::DRFlags::RenderOnlyOnHosts) | static_cast<int>(drflags));
			}
		}

		m_client->send(VRayMessage::msgRendererActionInit(type, drflags));
		m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetCommitAction, static_cast<int>(CommitAction::CommitAutoOff)));
		m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::GetImage, static_cast<int>(RenderChannelType::RenderChannelTypeNone)));
		
		if (m_settings.drUse) {
			const std::vector<std::string>& hostItems = m_settings.drHosts;
			std::string hostsStr;
			hostsStr.reserve(hostItems.size() * 24); // 24 chars per host is enough - e.g 123.123.123.123:12345;
			for (const std::string& host : hostItems) {
				hostsStr += host;
				hostsStr.push_back(';');
			}
			hostsStr.pop_back(); // remove last delimiter - ;
			m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::ResetsHosts, hostsStr));
		}

		m_cachedValues.renderSizes = VRayMessage::RenderSizes();
	}
	catch (zmq::error_t& e) {
		Logger::error("Failed to initialize ZMQ client\n{}", e.what());
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
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetVfbOptions, viewSettings.vfbFlags));
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetQuality, viewSettings.viewportImageQuality));
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetViewportImageFormat, viewSettings.viewportImageType));
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::SetRenderMode, viewSettings.renderMode));

	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::Start));

	// Production rendering could be aborted and started again.
	// For that reason this flag should be cleared.
	m_isAborted = false;
}

void ZmqExporter::renderSequence(int start, int end, int step)
{
	m_layerImages.clear();
	m_client->send(VRayMessage::msgRendererActionRenderSequence(start, end, step));

	// Production rendering could be aborted and started again.
	// For that reason this flag should be cleared.
	m_isAborted = false;
}


void ZmqExporter::stop()
{
	m_client->send(VRayMessage::msgRendererAction(VRayMessage::RendererAction::Stop));
}


void ZmqExporter::exportVrscene(const ExportSceneSettings& settings)
{
	if (m_settings.separateFiles) {
		Logger::warning("ZMQ will ignore option 'Separate Files' and export in one file!");
	}

	fs::path dirPath(settings.filePath);
	dirPath.remove_filename();

	std::error_code code;
	if (!fs::exists(dirPath) && !fs::create_directories(dirPath, code)) {
		Logger::error("Failed to create directory '{}': {}", settings.filePath, code.message());
		return;
	} 
	
	VRayMessage::ExportSettings exportSettings;

	exportSettings.compressed = settings.compressed;
	exportSettings.hexArrays = settings.hexArrays;
	exportSettings.hexTransforms = settings.hexTransforms;
	exportSettings.hostAppString = settings.hostAppString;
	exportSettings.filePath = settings.filePath;
	
	std::vector<std::string> pluginTypes;
	boost::split(pluginTypes, settings.pluginTypes, boost::is_any_of(","));

	if (settings.separateFiles) {
		exportSettings.subFileInfo.resize(pluginTypes.size());

		for (size_t i = 0; i < pluginTypes.size(); ++i) {
			exportSettings.subFileInfo[i].pluginType     = pluginTypes[i];
			exportSettings.subFileInfo[i].fileNameSuffix = pluginTypes[i];
		}
	}

	m_client->send(VRayMessage::msgRendererActionExportScene(exportSettings));
}


AttrPlugin ZmqExporter::exportPlugin(const PluginDesc& pluginDesc)
{
	if (pluginDesc.pluginID.empty()) {
		Logger::warning("[{}] PluginDesc.pluginID is not set!", pluginDesc.pluginName);
		return AttrPlugin();
	}

	m_dirty = true;
	++m_exportedCount;

	const std::string & name = pluginDesc.pluginName;
	AttrPlugin plugin(name);

	
	m_client->send(VRayMessage::msgPluginCreate(name, pluginDesc.pluginID));

	for (auto & attributePairs : pluginDesc.pluginAttrs) {
		const PluginAttr & attr = attributePairs.second;
		if (attr.attrValue.getType() != ValueTypeUnknown) {
			auto msg = VRayMessage::msgPluginSetProperty(name, attr.attrName, attr.attrValue, attr.forceUpdate);
			m_exportedSize += msg.size();
			++m_exportedAttributes;
			sendPluginMsg(pluginDesc.pluginName, std::move(msg));
		}
	}

	return plugin;
}


int ZmqExporter::getExportedPluginsCount() const
{
	return m_exportedCount;
}


void ZmqExporter::resetExportedPluginsCount()
{
	m_exportedCount = 0;
}

