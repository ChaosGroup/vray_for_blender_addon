// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "scene_exporter_rt.h"

namespace VRayForBlender
{


InteractiveExporter::InteractiveExporter(const ExporterSettings& settings) :
	m_settings(settings)
{
}

InteractiveExporter::~InteractiveExporter() {
	m_exporter->set_callback_on_image_ready(nullptr);
	m_exporter->set_callback_on_rt_image_updated(nullptr);
}


void InteractiveExporter::init(ZmqExporter* zmqExporter) {
	vassert(zmqExporter != nullptr);
	m_exporter = zmqExporter;
}

void InteractiveExporter::setupCallbacks()
{
	if (m_settings.exporterType == int(proto::ExporterType::VANTAGE_LIVE_LINK))
		return;
	m_exporter->set_callback_on_image_ready([this]() {
		m_imageReadyReceived = true;
	});

	m_exporter->set_callback_on_rt_image_updated([&]() {
		m_imageUpdated = true;
	});
}


void InteractiveExporter::renderEnd()
{
	m_exporter->stopRendering();
}


bool InteractiveExporter::isRenderReady()
{
	if (m_imageReadyReceived) {
		// Clearing the render ready flag,
		// so the checking for image and tagging for redrawing
		// in the python render engine implementation could start
		m_imageReadyReceived = false;
		return true;
	}

	return false;
}


bool VRayForBlender::InteractiveExporter::isRendering()
{
	return m_exporter->isRendering();
}


bool InteractiveExporter::imageWasUpdated()
{
	if (m_imageUpdated) {
		m_imageUpdated = false;
		return true;
	}
	return false;
}

} // end namespace VRayForBlender
