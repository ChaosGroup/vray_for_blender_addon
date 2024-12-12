#pragma once

#include "scene_exporter.h"


namespace VRayForBlender {

class InteractiveExporter : public SceneExporter
{
public:
  explicit InteractiveExporter(const ExporterSettings& settings);

private:
	virtual void setupCallbacks() override;

};

} // namespace VRayForBlender

