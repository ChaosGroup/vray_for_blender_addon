#pragma once

#include "scene_exporter.h"

#include "export/assets/blender_types.h"

#include <mutex>
#include <vector>
#include <boost/python.hpp>

namespace VRayForBlender {

class ProductionExporter : public SceneExporter
{
	using time_point = std::chrono::high_resolution_clock::time_point;


public:
	explicit ProductionExporter(const ExporterSettings& settings);

	// SceneExporter interface implementation
	virtual void      setupCallbacks() override;
	virtual void	  renderStart(RenderPass *renderResult, py::object imageUpdatedCallback) override;
	virtual void      renderEnd() override;
	virtual bool      renderFrame(bool waitForCompletion) override;
	virtual void      renderSequence(int start, int end, int step) override;
	virtual bool	  renderSequenceRunning() override;
	virtual int 	  lastRenderedFrame() override;
	virtual void      setRenderFrame(float frame) override;
	virtual void	  abortRender()  override;

	// Callbacks
	void              cb_on_image_ready();
	void              cb_on_rt_image_updated();
	void              cb_on_bucket_ready(const VRayBaseTypes::AttrImage & image);

private:
	void              updateImage();

	std::atomic_bool  m_renderFinished    = false;  // Used to signal a frame has been rendered
	std::mutex        m_callback_mtx;

	py::object		  m_imageUpdateCallback;
	time_point        m_lastImageUpdate;
	RenderPass*       m_renderPass = nullptr;
};

} // namespace VRayForBlender

