#pragma once

#include "scene_exporter.h"

#include "export/assets/blender_types.h"

#include <mutex>
#include <vector>
#include <boost/python.hpp>

namespace VRayForBlender {

class ProductionExporter : public ExporterBase
{
	using time_point = std::chrono::high_resolution_clock::time_point;
	using ZmqExporterPtr = std::shared_ptr<ZmqExporter>;

	ProductionExporter(const ProductionExporter&) = delete;
	ProductionExporter& operator=(const ProductionExporter&) = delete;
	
public:
	explicit ProductionExporter(const ExporterSettings& settings);
	~ ProductionExporter();

	// ExporterBase interface implementation
	void    init(ZmqExporter* zmqExporter) override;
	void    setupCallbacks() override;
	void    renderStart(RenderPass *renderResult, py::object imageUpdatedCallback) override;
	void    renderEnd() override;
	void    renderFrame() override;
	void    continueRenderSequence() override;
	void    renderSequence(int start, int end, int step) override;
	bool    isRendering() override;
	int     lastRenderedFrame() override;
	void    setRenderFrame(float frame) override;
	void    abortRender()  override;

	// Callbacks
	void              cb_on_image_ready();
	void              cb_on_rt_image_updated();
	void              cb_on_vfb_layers_updated(const std::string& layersJson);
	void              cb_on_bucket_ready(const VRayBaseTypes::AttrImage & image);

private:
	void              updateImage();

	ZmqExporter*      m_exporter;
	ExporterSettings  m_settings;
	std::atomic_bool  m_renderFinished    = false;  // Used to signal a frame has been rendered

	py::object        m_imageUpdateCallback;
	time_point        m_lastImageUpdate;
	RenderPass*       m_renderPass = nullptr;
};

} // namespace VRayForBlender

