#pragma once

#include <boost/python.hpp>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>
#include <span>
#include <string>


#include "render_image.h"
#include "zmq_exporter.h"
#include "api/interop/types.h"
#include "utils/thread_manager.h"


namespace proto = VrayZmqWrapper;

namespace VRayForBlender {
	struct ExporterBase {
		virtual ~ExporterBase() = default;

		virtual void init(ZmqExporter* zmqExporter) = 0;
		virtual void renderStart(RenderPass * /*renderPass*/, py::object /*cbImageUpdated*/) {}
		virtual void renderEnd() {}
		virtual void renderFrame() {}
		virtual void continueRenderSequence() {}
		virtual void renderSequence(int /*start*/, int /*end*/, int /*step*/){}
		virtual bool isRendering() { return false; }
		virtual bool vrsceneExportRunning() { return false; }
		virtual int  lastRenderedFrame() { return false; }
		virtual void setRenderFrame(float /*frame*/) {}
		virtual void setupCallbacks() {}
		virtual void abortRender() {}
		virtual bool imageWasUpdated() {return false;}
		virtual bool isRenderReady() {return false;}
	};


class SceneExporter {
public:
	static const bool EXPORT_FULL    = false;
	static const bool EXPORT_CHANGES = true;

	using TimePoint        = std::chrono::steady_clock::time_point;
	using ZmqExporterPtr   = std::unique_ptr<ZmqExporter>;
	using ExporterBasePtr  = std::unique_ptr<ExporterBase>;
	using CondWaitGroupPtr = std::unique_ptr<CondWaitGroup>;

	SceneExporter(const SceneExporter&) = delete;
	SceneExporter& operator=(const SceneExporter&) = delete;

public:
	SceneExporter();
	~SceneExporter();

public:
	void     init(ExporterBase* policy, const Interop::ExporterSettings& settings);
	void     free();

	void    renderStart(RenderPass * renderPass, py::object cbImageUpdated) { m_policy->renderStart(renderPass, cbImageUpdated); }
	void    renderEnd()                  { m_policy->renderEnd(); }
	void    renderFrame()                { m_policy->renderFrame();}
	void    continueRenderSequence()     { m_policy->continueRenderSequence(); }
	void    renderSequence(int start, int end, int step)  { m_policy->renderSequence(start, end, step);}
	bool    isRendering()                { return m_policy->isRendering(); }
	int     lastRenderedFrame()          { return m_policy->lastRenderedFrame(); }
	void    setRenderFrame(float frame)  { m_policy->setRenderFrame(frame); }
	void    setupCallbacks()             { m_policy->setupCallbacks(); }
	void    abortRender()                { m_policy->abortRender(); }


	void          exportMesh(MeshDataPtr mesh, bool asyncExport);
	void          exportHair(HairDataPtr hair);
	void          exportPointCloud(PointCloudDataPtr pc);
	void          exportSmoke(SmokeDataPtr smoke);
	void          exportInstancer(InstancerDataPtr pc);
	void          clearFrameData(float upToTime);

	void          startExport(int threadCount);
	void          finishExport(bool interactive);
	int           writeVrscene(const ExportSceneSettings& exportSettings);
	void          startStatsCollection();
	void          endStatsCollection(bool printStats, const std::string& title);
	void          setRenderSizes(const proto::RenderSizes& sizeData);
	void          setCameraName(const std::string& cameraName);
	void          syncView(const ViewSettings& viewSettings);
	void          openVFB();
	void          setVfbAlwaysOnTop(bool alwaysOnTop);
	void          clearScene();

	RenderImage   getImage();
	RenderImage   getRenderPassImage(const std::string& passName);
	float          getRenderProgress() const;

	std::string   getEngineUpdateMessage(); // Returns status of the renderring in text
	bool          isRenderReady(); // Indicates that the final rendered image has come
	bool          imageWasUpdated(); // True when there is updated image for drawing
	bool          vrsceneExportRunning();
	
	ZmqExporter*  getPluginExporter() { return m_exporter.get(); };

	void          setRenderStoppedCallback(py::object cbRenderStopped);


protected:
	ExporterBasePtr          m_policy;
	ZmqExporterPtr           m_exporter;  ///< Pointer to the actual plugin exporter
	ExporterSettings         m_settings; ///< Holder for all settings that affect way of export

	CondWaitGroupPtr         m_wg;
	std::optional<TimePoint> m_tmStartExport;  // Used for the time-to-first-image metric
	ThreadManager::Ptr       m_threadManager;
	bool                     m_rendererStarted = false;
	boost::python::object    m_engineRef;	    // Weak reference to a RenderEngine object
	boost::python::object    m_cbRenderStopped;  // Weak reference to a callback executed when rendering gets stopped

	std::mutex               engineUpdateMsgMtx;
	std::string              engineUpdateMessage;

	std::atomic_bool         m_vrsceneExportInProgress = false; // Waiting for response to a requested .vrscene export operation
};

}


