#pragma once


#include "api/interop/types.h"
#include "render_image.h"
#include "utils/platform.h"

#include "ipc.h"
#include "zmq_common.hpp"
#include "zmq_message.hpp"
#include "zmq_agent.h"

#include <functional>
#include <map>
#include <memory>
#include <string>


// Forward declarations
struct PluginDesc;


namespace VRayForBlender {

using namespace Interop;

/// ZmqExporter acts as a proxy between the VRayForBlender addon and ZmqServer. 
/// It implements the ZmqServer protocol defined in the ZmqWrapper project.
/// An instance of this class is created for each instance of bpy.types.RenderEngine
/// which needs to export to and render using VRay.
class ZmqExporter{

	using ZmqAgentPtr       = std::unique_ptr<VrayZmqWrapper::ZmqAgent>;
	using ImageType         = VRayBaseTypes::AttrImage::ImageType;
	using RenderChannelType = VRayBaseTypes::RenderChannelType;
	using AttrPlugin        = VRayBaseTypes::AttrPlugin;
	using ImgReader			= VrayZmqWrapper::ImageReader;
	using ImgIdReader		= VrayZmqWrapper::SharedMemoryReader;
	using ImgIdReaderPtr	= std::unique_ptr<ImgIdReader>;

	using UpdateMessageCb  = std::function<void(const std::string&)>;
	using BucketReadyCb    = std::function<void(const VRayBaseTypes::AttrImage&)>;
	using ExporterCallback = std::function<void(void)>;

	struct AttrStats {
		int64_t num  = 0;
		int64_t size = 0;
	};

	struct ZmqRenderImage: public RenderImage {
		void update(const VRayBaseTypes::AttrImage &img, ZmqExporter * exp, bool fixImage);
		// void updateViewport(const VrayZmqWrapper::VRayMessage::ImageBuffer& buffer);
	};

	using ImageMap = std::unordered_map<RenderChannelType, ZmqRenderImage, std::hash<int>>;

	// Cache values set directly through the VRayRenderer interface
	// (not through the plugin system), so that we could skip updates
	// if the value has not changed
	struct ValueCache {
		VrayZmqWrapper::VRayMessage::RenderSizes renderSizes;
		std::string      activeCamera;
		ViewSettings	 viewSettings;
	};


public:
	explicit  ZmqExporter(const ExporterSettings & settings);
	~ZmqExporter();

public:
	void        init();
	void        start();
	void		renderSequence(int start, int end, int step);
	void        stop();
	void        free();

	void        exportVrscene(const ExportSceneSettings& exportSettings);
	void        clearFrameData(float upTo);
	void		clearScene();
	void		abortRender();
	void        syncView(const ViewSettings& viewSettings);
	void		showVFB(); // sends VfbFlags::Show in a SetVfbOptions message 
	void		setVfbAlwaysOnTop(bool alwaysOnTop); // sends VfbFlags::AlwaysOnTop in a SetVfbOptions message 
	float		getRenderProgress() const;

	// Export API
	void        pluginCreate(const std::string& pluginName, const std::string& pluginType);
	void        pluginRemove(const std::string& pluginName);
	void        pluginUpdate(const std::string& pluginName, const std::string& attrName, const VRayBaseTypes::AttrValue& value, bool forceUpdate = false);
	void        sendPluginMsg(const std::string& pluginName, zmq::message_t&& message);
			    
	int         getExportedPluginsCount() const;
	void        resetExportedPluginsCount();

	RenderImage getImage        ();
	RenderImage getPass         (const std::string& name);
	RenderImage getRenderChannelImage(RenderChannelType channelType);
	void        setRenderSize   (const VrayZmqWrapper::VRayMessage::RenderSizes &sizes);
	void        setCameraPlugin (const std::string &pluginName);
	void        commitChanges   ();

	void        setCurrentFrame(float frame);
	float       getCurrentFrame() const;
	void        setLastRenderedFrame(int frame) { m_lastRenderedFrame = frame; }
	int	        getLastRenderedFrame() const { return m_lastRenderedFrame; }
	bool        isAborted() const { return m_isAborted; }

	AttrPlugin  exportPlugin(const PluginDesc &pluginDesc);

	void        set_callback_on_image_ready(ExporterCallback cb)      { callback_on_image_ready = cb; }
	void        set_callback_on_rt_image_updated(ExporterCallback cb) { callback_on_rt_image_updated = cb; }
	void        set_callback_on_message_updated(UpdateMessageCb cb)   { callback_on_message_update = cb; }
	void        set_callback_on_bucket_ready(BucketReadyCb cb)        { callback_on_bucket_ready = cb; }
	void		set_callback_on_vfb_layers_updated(UpdateMessageCb cb){ callback_on_vfb_layers_updated = cb; }
	void		set_callback_on_render_stopped(ExporterCallback cb)	  { callback_on_render_stopped = cb; }

private:
	bool readViewportImage  ();

	void handleMsg(const VrayZmqWrapper::VRayMessage &message);
	void handleError(const std::string& err);

private:
	ExporterSettings m_settings;
	ZmqAgentPtr      m_client;

	ExporterCallback     callback_on_image_ready;
	ExporterCallback     callback_on_rt_image_updated;
	UpdateMessageCb      callback_on_message_update;
	BucketReadyCb        callback_on_bucket_ready;
	UpdateMessageCb		 callback_on_vfb_layers_updated;
	ExporterCallback	 callback_on_render_stopped;

	bool              m_dirty = true;  // Set to true if scene has to be re-rendered
	std::atomic<bool> m_isAborted = false;
	std::atomic<int>  m_exportedCount = 0;  // Number of exported plugins

	std::mutex   m_imgMutex;  // Ensures the image is not changed while it is read
	std::mutex   m_zmqClientMutex;

	ImageMap     m_layerImages;
	ImgIdReaderPtr m_imgIdReader;

	int64_t      m_exportedAttributes = 0;
	int64_t      m_exportedSize = 0;
	float        m_currentSceneFrame = 0;
	float        renderProgress;     // Fraction of job done in [0, 1]
	std::string  progressMessage;
	std::atomic<int>	 m_lastRenderedFrame = 0;

	std::map<std::string, AttrStats> m_exportStats;
	std::mutex m_statsMutex;

	ValueCache m_cachedValues;
};

} // namespace VRayForBlender

