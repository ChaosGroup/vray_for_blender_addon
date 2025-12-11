#pragma once

#include "scene_exporter.h"


namespace VRayForBlender {

class InteractiveExporter : public ExporterBase
{
	using ZmqExporterPtr = std::shared_ptr<ZmqExporter>;

	InteractiveExporter(const InteractiveExporter&) = delete;
	InteractiveExporter& operator=(const InteractiveExporter&) = delete;

public:
  explicit InteractiveExporter(const ExporterSettings& settings);
  ~InteractiveExporter();

private:
	// ExporterBase interface implementation
	void init(ZmqExporter* zmqExporter) override;
	void setupCallbacks () override;
	bool isRenderReady  () override;
	bool imageWasUpdated() override;
	void renderEnd      () override;
	bool isRendering    () override;

private:
	ZmqExporter*      m_exporter;
	ExporterSettings  m_settings;

	std::atomic_bool	m_imageUpdated = false;		// An image update was receved from VRay
	std::atomic_bool	m_imageReadyReceived = false; // The last frame of the currently rendered frame has been received 
};

} // namespace VRayForBlender

