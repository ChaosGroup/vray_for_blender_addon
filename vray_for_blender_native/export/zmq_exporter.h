#pragma once


#include "api/interop/types.h"
#include "render_image.h"
#include "utils/platform.h"

#include <ipc.h>
#include <zmq_common.hpp>
#include <zmq_message.hpp>
#include <zmq_agent.h>

#include <functional>
#include <map>
#include <memory>
#include <string>


// Forward declarations
struct PluginDesc;

namespace VRayForBlender {

using namespace Interop;
namespace proto = VrayZmqWrapper;

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
	using ImgIdReader       = VrayZmqWrapper::SharedMemoryReader;
	using ImgIdReaderPtr    = std::unique_ptr<ImgIdReader>;

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

	using ImageMap = std::unordered_map<RenderChannelType, ZmqRenderImage, std::hash<int>>;

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

	void        renderSequence(int start, int end, int step);
	void        continueRenderSequence();
	void        stopRendering();
	void        freeRenderer();
	bool        isStopped() const;
	bool        isRendering() const { return m_isRendering; }

	int         exportVrscene(const ExportSceneSettings& exportSettings);
	void        clearFrameData(float upTo);
	void        clearScene();
	void        abortRender();
	void        syncView(const ViewSettings& viewSettings);
	void        showVFB(); // sends VfbFlags::Show in a SetVfbOptions message
	void        setVfbAlwaysOnTop(bool alwaysOnTop); // sends VfbFlags::AlwaysOnTop in a SetVfbOptions message
	float       getRenderProgress() const;

	// Export API
	void        pluginCreate(const std::string& pluginName, const std::string& pluginType, bool allowTypeChanges);
	void        pluginRemove(const std::string& pluginName);
	void        pluginUpdate(const std::string& pluginName, const std::string& attrName, const VRayBaseTypes::AttrValue& value, bool animatable, bool forceUpdate = false, bool recreate = false);
	void        sendPluginMsg(zmq::message_t&& message);

	RenderImage getImage        ();
	RenderImage getPass         (const std::string& name);
	RenderImage getRenderChannelImage(RenderChannelType channelType);
	void        setRenderSize   (const proto::RenderSizes &sizes);
	void        setCameraName   (const std::string &cameraSceneName);
	void        commitChanges   ();

	void        setCurrentFrame(float frame);
	float       getCurrentFrame() const;
	void        setLastRenderedFrame(int frame) { m_lastRenderedFrame = frame; }
	int	        getLastRenderedFrame() const { return m_lastRenderedFrame; }

	AttrPlugin  exportPlugin(const PluginDesc &pluginDesc);

	void        set_callback_on_image_ready(ExporterCallback cb)        { std::scoped_lock l(m_callbacksMutex); callback_on_image_ready = cb; }
	void        set_callback_on_rt_image_updated(ExporterCallback cb)   { std::scoped_lock l(m_callbacksMutex); callback_on_rt_image_updated = cb; }
	void        set_callback_on_message_updated(UpdateMessageCb cb)     { std::scoped_lock l(m_callbacksMutex); callback_on_message_update = cb; }
	void        set_callback_on_bucket_ready(BucketReadyCb cb)          { std::scoped_lock l(m_callbacksMutex); callback_on_bucket_ready = cb; }
	void		set_callback_on_vfb_layers_updated(UpdateMessageCb cb)  { std::scoped_lock l(m_callbacksMutex); callback_on_vfb_layers_updated = cb; }
	void		set_callback_on_render_stopped(RenderStoppedCallback cb){ std::scoped_lock l(m_callbacksMutex); callback_on_render_stopped = cb; }
	void		set_callback_on_async_op_complete(AsyncOpCompleteCb cb) { std::scoped_lock l(m_callbacksMutex); callback_on_async_op_complete = cb; }

private:
	bool readViewportImage  ();

	void handleMsg(const zmq::message_t& msg);
	void handleError(const std::string& err);

	void processControlOnLogMessage(proto::DeserializerStream& stream);
	void processControlOnUpdateVfbLayers(proto::DeserializerStream& stream);
	void processRendererOnVRayLog(const proto::MsgRendererOnVRayLog& message);
	void processRendererOnImage(const proto::MsgRendererOnImage& message);
	void processRendererOnChangeState(const proto::MsgRendererOnChangeState& message);
	void processRendererOnAsyncOpComplete(const proto::MsgRendererOnAsyncOpComplete& message);
	void processRendererOnProgress(const proto::MsgRendererOnProgress& message);

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
	std::atomic<int>  m_exportedCount = 0;  // Number of exported plugins

	std::mutex        m_imgMutex;        // Ensures the image is not changed while it is read
	std::mutex        m_zmqClientMutex;
	std::mutex        m_callbacksMutex;  // Guards (de)registering of callbacks

	ImageMap          m_layerImages;
	ImgIdReaderPtr    m_imgIdReader;

	float             m_currentSceneFrame = 0;
	float             m_renderProgress = 0.0;     // Fraction of job done in [0, 1]
	std::atomic<int>  m_lastRenderedFrame = 0;

	std::map<std::string, AttrStats> m_exportStats;
	std::mutex m_statsMutex;

	ValueCache m_cachedValues;
};

} // namespace VRayForBlender

