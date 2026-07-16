// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once


#include "api/interop/types.h"
#include "render_image.h"
#include "utils/platform.h"

#include <ipc.h>
#include <zmq_common.hpp>
#include <zmq_message.hpp>
#include <zmq_agent.h>

#include <tsl/robin_map.h>
#include <tsl/robin_set.h>
#include <functional>
#include <atomic>
#include <map>
#include <memory>
#include <string>


// Forward declarations
struct PluginDesc;

namespace VRayForBlender {

using namespace Interop;
namespace proto = VrayZmqWrapper;
namespace vray = VRayBaseTypes;

/// ZmqExporter acts as a proxy between the VRayForBlender addon and ZmqServer.
/// It implements the ZmqServer protocol defined in the ZmqWrapper project.
/// An instance of this class is created for each instance of bpy.types.RenderEngine
/// which needs to export to and render using VRay.
class ZmqExporter{

	using ZmqAgentPtr       = std::unique_ptr<VrayZmqWrapper::ZmqAgent>;
	using ImageType         = VRayBaseTypes::AttrImage::ImageType;
	using RenderChannelType = VRayBaseTypes::RenderChannelType;
	using AttrPlugin        = VRayBaseTypes::AttrPlugin;
	using ImgReader         = VrayZmqWrapper::ImageReader;
	using ImgReaderPtr      = std::unique_ptr<ImgReader>;

	using UpdateMessageCb  = std::function<void(const std::string&)>;
	using BucketReadyCb    = std::function<void(const VRayBaseTypes::AttrImage&)>;
	using ExporterCallback = std::function<void(void)>;
	using RenderStoppedCallback = std::function<void(bool)>;
	using AsyncOpCompleteCb = std::function<void(proto::RendererAsyncOp, bool, const std::string&)>;

	struct AttrStats {
		int64_t num  = 0;
		int64_t size = 0;
	};

	struct ZmqRenderImage: public RenderImage {
		void update(const VRayBaseTypes::AttrImage &img, ZmqExporter *exp);
	};

	using ImageMap = tsl::robin_map<RenderChannelType, ZmqRenderImage, std::hash<int>>;

public:
	/// Routing key shared with the server (defined in zmq_common.hpp).
	using PerInstanceKey = VrayZmqWrapper::PerInstanceKey;

	/// Non-owning Blender pass-buffer destination, registered via setElementDestinations().
	struct ElementDestination {
		float* buffer = nullptr;
		int    width    = 0;
		int    height   = 0;
		int    channels = 0;
	};

private:
	using PerInstanceKeyHash = VrayZmqWrapper::PerInstanceKeyHash;
	using ElementDestinationMap = tsl::robin_map<PerInstanceKey, ElementDestination, PerInstanceKeyHash>;

	// Cache values set directly through the VRayRenderer interface
	// (not through the plugin system), so that we could skip updates
	// if the value has not changed
	struct ValueCache {
		proto::RenderSizes renderSizes;
		std::string      activeCamera;
		ViewSettings     viewSettings;
	};

	ZmqExporter(ZmqExporter&) = delete;
	ZmqExporter& operator=(ZmqExporter&) = delete;

public:
	explicit ZmqExporter(proto::ExporterType exporterType);
	~ZmqExporter();

public:
	void        init(const ExporterSettings & settings);
	void        start();
	void        stop();
	void        detach();

	/// Toggles whether the server emits the Combined image and per-element data to this
	/// client for the next render. Mirrors scene.vray.Exporter.image_to_blender. The
	/// value is read by start() when serializing MsgRendererStart.
	void        setImageToBlender(bool enabled) { m_imageToBlender = enabled; }
	bool        getImageToBlender() const { return m_imageToBlender; }

	void        renderSequence(const vray::AttrList<int>& sequences);
	void        continueRenderSequence();
	void        stopRendering();
	void        freeRenderer();
	bool        isStopped() const;
	bool        isRendering() const { return m_isRendering; }

	int         exportVrscene(const ExportSceneSettings& exportSettings);
	int         exportProxy(const ProxyExportSettings& proxySettings);
	void        clearFrameData(float upTo);
	void        clearScene();
	void        abortRender();
	void        syncView(const ViewSettings& viewSettings);
	void        showVFB(); // sends VfbFlags::Show in a SetVfbOptions message
	void        setVfbAlwaysOnTop(bool alwaysOnTop); // sends VfbFlags::AlwaysOnTop in a SetVfbOptions message
	float       getRenderProgress() const;

	// Export API
	void        pluginCreate(std::string pluginName, std::string pluginType, bool allowTypeChanges);
	void        pluginRemove(std::string pluginName);
	void        pluginUpdate(std::string pluginName, std::string attrName, const VRayBaseTypes::AttrValue& value, bool animatable, bool forceUpdate = false, bool recreate = false);
	void        sendPluginMsg(zmq::message_t&& message);

	RenderImage getImage        ();
	RenderImage getPass         (const std::string& name);
	RenderImage getRenderChannelImage(RenderChannelType channelType);

	/// Register pass-buffer write targets for incoming MsgRendererOnElementReady messages.
	/// Entries with a null buffer are silently dropped (lazy Blender allocation).
	void setElementDestinations(std::vector<std::pair<PerInstanceKey, ElementDestination>> destinations);
	void clearElementDestinations();
	void        requestRenderChannel(int channelType, const std::string& pluginInstanceName = "", int subIndex = 0);
	std::string getMetadata(const std::string& key) const;

	/// Point the main render layer at an externally-owned pixel buffer (e.g. Blender's RenderPass
	/// ibuf) so that ZmqRenderImage::update() writes directly into it with no extra copy.
	/// Call with buffer=nullptr to release the reference (e.g. on renderEnd).
	void        setRenderBuffer (float* buffer, int width, int height, int channels);

	void        setRenderSize          (const proto::RenderSizes &sizes);
	void        setCameraName          (const std::string &cameraSceneName);
	void        setResumableRendering  (bool enabled, const std::string &outputFileName, int autosaveSeconds, bool deleteOnSuccess = false);
	void        commitChanges          ();

	void        setCurrentFrame(float frame);
	float       getCurrentFrame() const;
	void        setLastRenderedFrame(int frame) { m_lastRenderedFrame = frame; }
	int	        getLastRenderedFrame() const { return m_lastRenderedFrame; }

#ifdef WITH_PROFILING
	uint64_t    getReceivedImagesCount() const { return m_receivedImagesCount; }
#endif

	AttrPlugin  exportPlugin(const PluginDesc &pluginDesc);

	void        set_callback_on_image_ready(ExporterCallback cb)        { std::scoped_lock l(m_callbacksMutex); callback_on_image_ready = cb; }
	void        set_callback_on_rt_image_updated(ExporterCallback cb)   { std::scoped_lock l(m_callbacksMutex); callback_on_rt_image_updated = cb; }
	void        set_callback_on_message_updated(UpdateMessageCb cb)     { std::scoped_lock l(m_callbacksMutex); callback_on_message_update = cb; }
	void        set_callback_on_bucket_ready(BucketReadyCb cb)          { std::scoped_lock l(m_callbacksMutex); callback_on_bucket_ready = cb; }
	void		set_callback_on_vfb_layers_updated(UpdateMessageCb cb)  { std::scoped_lock l(m_callbacksMutex); callback_on_vfb_layers_updated = cb; }
	void		set_callback_on_render_stopped(RenderStoppedCallback cb){ std::scoped_lock l(m_callbacksMutex); callback_on_render_stopped = cb; }
	void		set_callback_on_async_op_complete(AsyncOpCompleteCb cb) { std::scoped_lock l(m_callbacksMutex); callback_on_async_op_complete = cb; }

private:
	bool readViewportImage  (int imgID, int bufferIndex);

	void handleMsg(const zmq::message_t& msg);
	void handleError(const std::string& err);

	void processControlOnLogMessage(proto::DeserializerStream& stream);
	void processControlOnUpdateVfbLayers(proto::DeserializerStream& stream);
	void processRendererOnVRayLog(const proto::MsgRendererOnVRayLog& message);
	void processRendererOnImage(const proto::MsgRendererOnImage& message);
	void processRendererOnChangeState(const proto::MsgRendererOnChangeState& message);
	void processRendererOnAsyncOpComplete(const proto::MsgRendererOnAsyncOpComplete& message);
	void processRendererOnProgress(const proto::MsgRendererOnProgress& message);
	void processRendererOnElementReady(const proto::MsgRendererOnElementReady& message);

	void fireStopEvent(bool isAborted);

private:
	ExporterSettings m_settings;
	ZmqAgentPtr      m_client;

	ExporterCallback      callback_on_image_ready;
	ExporterCallback      callback_on_rt_image_updated;
	UpdateMessageCb       callback_on_message_update;
	BucketReadyCb         callback_on_bucket_ready;
	UpdateMessageCb       callback_on_vfb_layers_updated;
	RenderStoppedCallback callback_on_render_stopped;
	AsyncOpCompleteCb     callback_on_async_op_complete;

	bool              m_dirty = true;  // Set to true if scene has to be re-rendered
	std::atomic<bool> m_isRendering = false;
	bool              m_imageToBlender = true; ///< Mirror of scene.vray.Exporter.image_to_blender for the next start().
	std::atomic<int>  m_exportedCount = 0;  // Number of exported plugins

	mutable std::mutex m_imgMutex;       // Ensures the image is not changed while it is read
	std::mutex        m_callbacksMutex;  // Guards (de)registering of callbacks

	ImageMap          m_layerImages;
	ElementDestinationMap m_elementDestinations;   ///< Pass-buffer write targets for the SHM element path. Guarded by m_imgMutex.
	std::unordered_map<std::string, std::string> m_metadata; ///< Metadata from render elements (e.g. Cryptomatte)
	int               m_imgId = -1;
	ImgReaderPtr      m_imgReaders[2];  ///< One reader per double-buffer slot
	ImgReaderPtr      m_elementReader;  ///< Reader for the per-element SHM region. Lazy-opened, dropped on renderEnd.

	float             m_currentSceneFrame = 0;
	float             m_renderProgress = 0.0;     // Fraction of job done in [0, 1]
	std::atomic<int>  m_lastRenderedFrame = 0;

	std::map<std::string, AttrStats> m_exportStats;
	std::mutex m_statsMutex;

	ValueCache m_cachedValues;
	std::string m_zmqServerPID;  // Cached PID string, set once in constructor
	tsl::robin_set<std::string> m_sharedMemoryObjects;

#ifdef WITH_PROFILING
	std::atomic<uint64_t> m_receivedImagesCount = 0;
#endif
};

} // namespace VRayForBlender

