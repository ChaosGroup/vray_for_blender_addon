#include "scene_exporter_rt.h"

namespace VRayForBlender
{


InteractiveExporter::InteractiveExporter(const ExporterSettings& settings)
        : SceneExporter(settings)
{
	SceneExporter::init();
}


void InteractiveExporter::setupCallbacks()
{
	if (m_settings.exporterType == int(proto::ExporterType::VANTAGE_LIVE_LINK))
		return;
	m_exporter->set_callback_on_image_ready([this]() {
		imageReadyReceived = true;
	});

	m_exporter->set_callback_on_rt_image_updated([&]() {
		imageUpdated = true;
	});
}


} // end namespace VRayForBlender
