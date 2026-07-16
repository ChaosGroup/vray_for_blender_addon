// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include "scene_exporter.h"

#include "export/assets/blender_types.h"

#include <mutex>
#include <vector>
#include <nanobind/nanobind.h>

namespace nb = nanobind;

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
	void    renderStart(RenderPass *renderResult, nb::callable&& imageUpdatedCallback, bool imageToBlender) override;
	void    renderEnd() override;
	void    renderFrame() override;
	void    continueRenderSequence() override;
	void    renderSequence(const vray::AttrList<int>& sequences) override;
	bool    isRendering() override;
	int     lastRenderedFrame() override;
	void    setRenderFrame(float frame) override;
	void    abortRender()  override;
	void    setElementPasses(const nb::list& passes) override;

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

	nb::callable      m_imageUpdateCallback;
	time_point        m_lastImageUpdate;
	RenderPass*       m_renderPass = nullptr;

	struct ElementPass {
		RenderPass* pass;
		int channelType;                ///< V-Ray RenderElement::Type
		std::string pluginInstanceName; ///< V-Ray plugin instance name
		int subIndex;                   ///< Cryptomatte layer or ObjectSelect 0=matte/1=filter/2=alpha
	};
	std::vector<ElementPass> m_elementPasses;  // Non-Combined render element passes
};

} // namespace VRayForBlender

