// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "zmq_exporter.h"
#include "zmq_server.h"
#include "vassert.h"
#include "utils/logger.hpp"
#include "plugin_desc.hpp"

#include <filesystem>
#include <ranges>
#include <random>

#include <zmq_common.hpp>
#include <zmq_message.hpp>

#include <boost/algorithm/string.hpp>

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

		if (!*this) {
			const auto& renderSizes = exp->m_cachedValues.renderSizes;

			vassert((renderSizes.imgWidth != 0) && (renderSizes.imgHeight != 0)
				&& "Invalid render size. Call ZmqExporter::setRenderSize() first.");

			w = renderSizes.imgWidth;
			h = renderSizes.imgHeight;
			channels = 4;

			setPixels(std::shared_ptr<float[]>(new float[w * h * channels]));
			memset(writablePixels(), 0, w * h * channels * sizeof(float));

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
		setPixels(std::shared_ptr<float[]>(imgData));
	}
	else if (img.imageType == VRayBaseTypes::AttrImage::ImageType::RGBA_REAL) {
		// Server-side sendImages only emits RGBA_REAL or JPG for the AttrImage fallback
		// path; RGB_REAL / BW_REAL are reserved for SHM-only element delivery and never
		// reach this handler. AColor data is always 4 floats per pixel.
		const float* imgData = reinterpret_cast<const float*>(img.data.get());
		if (this->w == img.width && this->h == img.height && this->channels == 4) {
			memcpy(writablePixels(), imgData, img.width * img.height * 4 * sizeof(float));
		} else {
			auto myImage = std::shared_ptr<float[]>(new float[img.width * img.height * 4]);
			memcpy(myImage.get(), imgData, img.width * img.height * 4 * sizeof(float));
			this->channels = 4;
			this->w = img.width;
			this->h = img.height;
			setPixels(std::move(myImage));
		}
	}
}



///////////////// ZMQ EXPORTER ////////////////////
ZmqExporter::ZmqExporter(ExporterType exporterType)
	: m_zmqServerPID(std::to_string(ZmqServer::get().getProcessID()))
{
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

	for (const std::string& shm : m_sharedMemoryObjects) {
		SharedMemoryWriter::remove(m_zmqServerPID, shm);
	}

	Logger::debug("ZmqExporter deleted");
}


RenderImage ZmqExporter::getRenderChannelImage(RenderChannelType channelType) {

	std::scoped_lock lock(m_imgMutex);

	auto imgIter = m_layerImages.find(channelType);

	if (imgIter != m_layerImages.end()) {
		RenderImage &storedImage = imgIter.value();
		if (storedImage.pixels) {
			return storedImage; // Shallow copy
		}
	}
	return RenderImage();
}


void ZmqExporter::setElementDestinations(std::vector<std::pair<PerInstanceKey, ElementDestination>> destinations) {
	std::scoped_lock lock(m_imgMutex);
	m_elementDestinations.clear();
	// Insert all entries, including those with a null buffer (lazy Blender allocation).
	// processRendererOnElementReady distinguishes "no destination registered" (genuine
	// routing-key mismatch) from "destination registered but buffer not yet allocated"
	// (expected during lazy alloc); collapsing the two via a filter here loses that signal.
	for (auto& entry : destinations) {
		m_elementDestinations.insert(std::move(entry));
	}
}


void ZmqExporter::clearElementDestinations() {
	std::scoped_lock lock(m_imgMutex);
	m_elementDestinations.clear();
	// Drop the SHM reader so the next render reopens after any server-side recreate
	// (resolution-change rebuild). The element SHM name is stable; the underlying
	// segment may have been replaced.
	m_elementReader.reset();
}


void ZmqExporter::setRenderBuffer(float* buffer, int width, int height, int channels) {
	std::scoped_lock lock(m_imgMutex);
	auto& layer = m_layerImages[RenderChannelType::RenderChannelTypeNone];
	if (buffer) {
		// Non-owning: Blender owns this buffer. update() will write directly here.
		layer.setPixelsNonOwning(buffer);
		layer.w        = width;
		layer.h        = height;
		layer.channels = channels;
	} else {
		layer.reset();
	}
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
	case MsgType::RendererOnElementReady: {
		const auto& message = deserializeMessage<MsgRendererOnElementReady>(stream);
		processRendererOnElementReady(message);
		break;
	}
	case MsgType::RendererOnPluginPropertyValues: {
		const auto& message = deserializeMessage<MsgRendererOnPluginPropertyValues>(stream);
		processRendererOnPluginPropertyValues(message);
		break;
	}

	case MsgType::ScatterPreviewResult: {
		const auto message = deserializeMessage<MsgScatterPreviewResult>(stream);
		ScatterResultCb cb;
		{
			std::scoped_lock l(m_callbacksMutex);
			cb = callback_on_scatter_result;
		}
		if (cb) {
			cb(message);
		}
		break;
	}

	case MsgType::ScatterPresetResult: {
		const auto message = deserializeMessage<MsgScatterPresetResult>(stream);
		ScatterPresetResultCb cb;
		{
			std::scoped_lock l(m_callbacksMutex);
			cb = callback_on_scatter_preset_result;
		}
		if (cb) {
			cb(message);
		}
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

	vassert (callback_on_message_update);

	const auto& message = deserializeMessage<MsgControlOnLogMessage>(stream);

	std::string logMsg = message.logMessage;

	// Leave only the first row of the message as it will be printed in a status line.
	const auto firstNewLinePos = logMsg.find_first_of("\n\r");
	if (firstNewLinePos != std::string::npos) {
		logMsg.resize(firstNewLinePos);
	}

	// Respect the configured verbosity so the status line doesn't show filtered-out messages.
	if (Logger::get().shouldLog(static_cast<LogLevel>(message.logLevel))) {
		callback_on_message_update(logMsg);
	}
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

		// Respect the configured verbosity so the status line doesn't show filtered-out messages.
		if (Logger::get().shouldLog(static_cast<LogLevel>(message.logLevel))) {
			callback_on_message_update(msg);
		}
	}
}


void ZmqExporter::processRendererOnImage(const proto::MsgRendererOnImage& message) {
	bool updateHostImage = false;

	// imgId >= 0 means the server sent a shared-memory notification (no serialized pixel data).
	// This is used for both viewport and production full-frame images.
	if (message.imgId >= 0 && message.imageSet.images.empty()) {
		if (readViewportImage(message.imgId, message.bufferIndex)) {
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

			// Store metadata (e.g. Cryptomatte manifest); per-instance manifests
			// arrive under key "cryptomatte.<instanceName>".
			for (const auto &kv : message.imageSet.metadata) {
				m_metadata[kv.first] = kv.second;
			}
		}

		for (const auto &img : message.imageSet.images) {
			// for result buckets use on bucket ready, otherwise rt image updated callback
			if (img.first == RenderChannelType::RenderChannelTypeNone && img.second.isBucket()) {
				std::scoped_lock lockCallbacks(m_callbacksMutex);
				if (this->callback_on_bucket_ready) {
					this->callback_on_bucket_ready(img.second);
				}
			}
			else {
				updateHostImage = true;
			}
		}

	}

#ifdef WITH_PROFILING
	m_receivedImagesCount++;
#endif

	if (updateHostImage) {
		std::scoped_lock lockCallbacks(m_callbacksMutex);
		if (this->callback_on_rt_image_updated) {
			this->callback_on_rt_image_updated();
		}
	}

	const bool ready = message.imageSet.sourceType == VRayBaseTypes::ImageSourceType::ImageReady;
	if (ready) {
		std::scoped_lock lockCallbacks(m_callbacksMutex);
		if (this->callback_on_image_ready) {
			this->callback_on_image_ready();
		}
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
		// When image_to_blender is off the server suppresses MsgRendererOnImage, so the
		// Python wait loop's m_renderFinished is never set via that path - drive it from
		// the state change instead. The OnImage path handles the streaming case.
		if (!m_imageToBlender) {
			std::scoped_lock lockCallbacks(m_callbacksMutex);
			if (this->callback_on_image_ready) {
				this->callback_on_image_ready();
			}
		}
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


void ZmqExporter::processRendererOnElementReady(const proto::MsgRendererOnElementReady& message) {
	using namespace std::chrono_literals;
	// Stay well under the server's 1000ms RendererElementDone timeout so the done message
	// still lands in time even if both the SHM-open and readInPlace waits run out back to back.
	static const auto SHARED_ACCESS_WAIT = 500ms;

	// The server blocks the next emit on MsgRendererElementDone. We MUST send it on every
	// path through this function - otherwise the server times out (1s), warns, and may
	// reuse the SHM region before we finish reading.
	struct ScopedDoneSender {
		ZmqExporter* exporter;
		int          subIndex;
		~ScopedDoneSender() {
			proto::MsgRendererElementDone done;
			done.subIndex = subIndex;
			if (exporter->m_client) {
				exporter->m_client->send(serializeMessage(done));
			}
		}
	} scopedDoneSender{this, message.subIndex};

	// Lazily open the element SHM region. The reader is dropped on renderEnd so the
	// next render reopens fresh; the server destroys + recreates under the same name
	// when the buffer needs to grow.
	if (!m_elementReader) {
		const auto bufferID = getElementBufferID();
		m_elementReader = std::make_unique<ImageReader>(m_zmqServerPID, bufferID);
		if (!m_elementReader->open(SHARED_ACCESS_WAIT)) {
			Logger::warning("Failed to open element SHM region %1%", bufferID);
			m_elementReader.reset();
			return;  // ScopedDoneSender still fires.
		}
	}

	// Look up the destination registered by ProductionExporter::setElementPasses.
	ElementDestination dest;
	bool haveDest = false;
	bool haveAnyDest = false;
	{
		std::scoped_lock lock(m_imgMutex);
		auto it = m_elementDestinations.find(PerInstanceKey{message.pluginInstanceName, message.subIndex});
		if (it != m_elementDestinations.end()) {
			dest = it->second;
			haveDest = true;
		}
		haveAnyDest = !m_elementDestinations.empty();

		if (!message.metadataKey.empty()) {
			m_metadata[message.metadataKey] = message.metadataValue;
		}
	}

	if (!haveDest) {
		// Every emitted element was requested by this client, so a key missing from a
		// populated map means we asked for the channel but registered no pass for it.
		// An empty map means renderEnd() cleared it - expected when an abort races the emit.
		if (haveAnyDest) {
			Logger::warning("Element ready for '%1%' subIndex %2%: no destination registered for this routing key",
				message.pluginInstanceName, message.subIndex);
		} else {
			Logger::debug("Element ready for '%1%' subIndex %2%: element routing already cleared (render ended)",
				message.pluginInstanceName, message.subIndex);
		}
	} else if (!dest.buffer) {
		// Expected during Blender's lazy pass-buffer allocation - the pass was registered
		// but ibuf->float_buffer.data wasn't materialized yet. Data is dropped this frame;
		// next render iteration usually has the buffer in place.
		Logger::debug("Element ready for '%1%' subIndex %2%: destination registered but pass ibuf not allocated yet",
			message.pluginInstanceName, message.subIndex);
	}

	const size_t expectedBytes = static_cast<size_t>(message.width)
	                           * static_cast<size_t>(message.height)
	                           * static_cast<size_t>(message.channels)
	                           * sizeof(float);

	m_elementReader->readInPlace(SHARED_ACCESS_WAIT, [&](const void* src, size_t capacity) {
		if (!haveDest || !dest.buffer) {
			return;  // No destination - just release the lock.
		}
		if (message.width != dest.width || message.height != dest.height || message.channels != dest.channels) {
			Logger::warning("Element '%1%' subIndex %2%: dimension mismatch (msg %3%x%4%x%5% vs dest %6%x%7%x%8%); skipping",
				message.pluginInstanceName, message.subIndex,
				message.width, message.height, message.channels,
				dest.width, dest.height, dest.channels);
			return;
		}
		const size_t bytes = std::min(expectedBytes, capacity);
		::memcpy(dest.buffer, src, bytes);
	});
}


void ZmqExporter::processRendererOnPluginPropertyValues(const MsgRendererOnPluginPropertyValues& message) {
	// Overwrite rather than append: the server sends the full watched set after every
	// frame, so the newest message is the complete picture.
	std::scoped_lock lock(m_imgMutex);
	m_pluginPropertyValues = message.values;
}


std::vector<PluginPropertyValueData> ZmqExporter::getPluginPropertyValues() const {
	std::scoped_lock lock(m_imgMutex);
	return m_pluginPropertyValues;
}


/// Read an image published by ZmqServer from one of the two double-buffered shared memory regions.
/// @param imgID       Generation counter from the server - changes on every viewport resize.
/// @param bufferIndex Which half of the double-buffer the server just finished writing (0 or 1).
/// @return true if the image was read successfully.
bool ZmqExporter::readViewportImage(int imgID, int bufferIndex) {
	using namespace std::chrono_literals;
	using ImageBuffer = ImageReader::ImageBuffer;

	static const auto SHARED_ACCESS_WAIT = 100ms;

	if (imgID != m_imgId) {
		// The server has recreated the shared memory buffers (image was resized).
		// Viewport uses double buffering (2 slots); production uses a single buffer.
		m_imgId = imgID;

		const bool doubleBuffered = (m_settings.getExporterType() == ExporterType::IPR_VIEWPORT);
		const int bufferCount = doubleBuffered ? 2 : 1;

		for (int i = 0; i < 2; ++i) {
			if (i < bufferCount) {
				// Each generation reserves 2 buffer ID slots (for double buffering),
				// so generation N uses IDs [N*2, N*2+1]. This keeps IDs unique across
				// generations even if only one slot is used (production).
				const auto bufferID = getImageBufferID(imgID * 2 + i);
				m_imgReaders[i] = std::make_unique<ImgReader>(m_zmqServerPID, bufferID);

				if (!m_imgReaders[i]->open(SHARED_ACCESS_WAIT)) {
					Logger::debug("Failed to open image transfer buffer %1%, error: %2%", bufferID, m_imgReaders[i]->getLastError());
					m_imgReaders[i].reset();
				}
				m_sharedMemoryObjects.insert(bufferID);
			} else {
				m_imgReaders[i].reset();
			}
		}
	}

	auto& reader = m_imgReaders[bufferIndex];
	if (!reader) {
		return false;
	}

	// Access the layer without m_imgMutex. This is safe because:
	//  - For viewport: only this thread (ZMQ agent) ever writes to the viewport layer.
	//  - For production: setRenderBuffer() writes at renderStart (before any image
	//    callbacks fire) and renderEnd (after rendering stops), so it is always
	//    lifecycle-sequenced before or after this code, never concurrent.
	// The lock is taken only when dimensions have changed and the pointer must be
	// updated, so that getRenderChannelImage() on the Python thread sees a consistent state.
	// This avoids holding m_imgMutex during the shared memory read (which acquires its
	// own interprocess lock), eliminating double-lock contention in the steady state.
	auto& layer = m_layerImages[RenderChannelType::RenderChannelTypeNone];

	// With double buffering the server writes to the OTHER slot while we read this one,
	// so there is no write/read race on the shared memory itself.
	ImageBuffer buffer = reader->read(SHARED_ACCESS_WAIT, ImageBuffer{layer.w, layer.h, layer.writablePixels()});

	if (!buffer.hasData()) {
		// Buffer exists but no frame has been written to it yet.
		return false;
	}

	if (buffer.data != layer.writablePixels()) {
		// Image dimensions changed - a new buffer was allocated by the IPC reader.
		// Lock m_imgMutex to update the layer's pointer and dimensions atomically
		// with respect to getRenderChannelImage() on the Python thread.
		std::scoped_lock lock(m_imgMutex);
		layer.setPixels(std::shared_ptr<float[]>(static_cast<float*>(buffer.data)));
		layer.w = buffer.width;
		layer.h = buffer.height;
		layer.channels = 4;
	}

	return true;
}


RenderImage ZmqExporter::getImage() {
	return getRenderChannelImage(RenderChannelType::RenderChannelTypeNone);
}


RenderImage ZmqExporter::getPass(const std::string& name)
{
	if (name == "Combined") {
		return getImage();
	}
	if (name == "Depth") {
		return getRenderChannelImage(RenderChannelTypeVfbZdepth);
	}
	return RenderImage();
}


void ZmqExporter::requestRenderChannel(int channelType, const std::string& pluginInstanceName, int subIndex) {
	m_client->send(serializeMessage(MsgRendererGetImage{channelType, pluginInstanceName, subIndex}));
}


std::string ZmqExporter::getMetadata(const std::string& key) const {
	std::scoped_lock lock(m_imgMutex);
	auto it = m_metadata.find(key);
	return it != m_metadata.end() ? it->second : "";
}


void ZmqExporter::freeRenderer()
{
	m_client->send(serializeMessage(MsgRendererFree{}));
}


bool ZmqExporter::isStopped() const {
	return m_client->isStopped();
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

void ZmqExporter::pluginCreate(std::string pluginName, std::string pluginType, bool allowTypeChanges)
{
	m_client->send(serializeMessage(MsgPluginCreate{std::move(pluginName), std::move(pluginType), allowTypeChanges}));
	m_dirty = true;
}


void ZmqExporter::pluginRemove(std::string pluginName)
{
	m_client->send(serializeMessage(MsgPluginRemove{std::move(pluginName)}));
	m_dirty = true;
}


void ZmqExporter::pluginUpdate(std::string pluginName, std::string attrName, const AttrValue& value, bool animatable, bool forceUpdate, bool recreate)
{
	MsgPluginUpdate msg{
		std::move(pluginName),
		std::move(attrName),
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


void ZmqExporter::setResumableRendering(bool enabled, const std::string &outputFileName, int autosaveSeconds, bool deleteOnSuccess)
{
	m_client->send(serializeMessage(MsgRendererSetResumableRendering{enabled, outputFileName, autosaveSeconds, deleteOnSuccess}));
	m_dirty = true;
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

	// Server-wide state - drop the stage left over from the previous render.
	ZmqServer::get().clearRenderStage();

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

		if (m_settings.profilerMode != 0) {
			m_client->send(serializeMessage(MsgRendererSetVRayProfiler{
				m_settings.profilerMode,
				m_settings.profilerMaxDepth,
				m_settings.profilerOutputDirectory,
				m_settings.profilerSceneName}));
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
	{
		std::scoped_lock lock(m_imgMutex);
		m_layerImages.clear();
		m_metadata.clear();
		m_pluginPropertyValues.clear();
	}

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

	m_client->send(serializeMessage(MsgRendererStart{m_imageToBlender}));

	// Production rendering could be aborted and started again.
	// For that reason this flag should be cleared.
	m_isRendering = true;
}


void ZmqExporter::stop() {
	m_client->stop(true);
}


void ZmqExporter::detach() {
	vassert(m_client->isStopped());
	nb::gil_scoped_acquire gil;

	set_callback_on_image_ready(nullptr);
	set_callback_on_rt_image_updated(nullptr);
	set_callback_on_message_updated(nullptr);
	set_callback_on_bucket_ready(nullptr);
	set_callback_on_vfb_layers_updated(nullptr);
	set_callback_on_render_stopped(nullptr);
	set_callback_on_async_op_complete(nullptr);
}


void ZmqExporter::renderSequence(const vray::AttrList<int>& sequences)
{
	vassert(sequences.getCount() != 0);

	{
		std::scoped_lock lock(m_imgMutex);
		m_layerImages.clear();
		m_metadata.clear();
		m_pluginPropertyValues.clear();
	}

	m_lastRenderedFrame = (*sequences)[0] - 1;
	m_client->send(serializeMessage(MsgRendererRenderSequence{sequences, m_imageToBlender}));


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

int ZmqExporter::exportProxy(const ProxyExportSettings& settings)
{
	MsgRendererExportProxy message;
	message.filePath = settings.filePath;
	message.elementsPerVoxel = settings.elementsPerVoxel;
	message.previewFaces = settings.previewFaces;
	message.previewType = settings.previewType;
	message.animOn = settings.animOn ? 1 : 0;
	message.startFrame = settings.startFrame;
	message.endFrame = settings.endFrame;

	m_client->send(serializeMessage(message));
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
	std::scoped_lock l(m_callbacksMutex);
	if (this->callback_on_render_stopped) {
		this->callback_on_render_stopped(isAborted);
	}
}
