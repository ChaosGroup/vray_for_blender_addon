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
	m_exporter->set_callback_on_image_ready([this]() {
		imageReadyReceived = true;
	});

	m_exporter->set_callback_on_rt_image_updated([&]() {
		imageUpdated = true;
	});
}


} // end namespace VRayForBlender
