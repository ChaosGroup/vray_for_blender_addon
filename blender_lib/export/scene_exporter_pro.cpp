// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "scene_exporter_pro.h"

#include "zmq_exporter.h"
#include "zmq_server.h"
#include "api/interop/utils.hpp"
#include "utils/logger.hpp"


using namespace std::chrono;
using namespace VRayForBlender;


ProductionExporter::ProductionExporter(const ExporterSettings& settings) :
	m_settings(settings), m_exporter(nullptr)
{
}


ProductionExporter::~ProductionExporter() {
	m_exporter->set_callback_on_image_ready(nullptr);
	m_exporter->set_callback_on_bucket_ready(nullptr);
	m_exporter->set_callback_on_rt_image_updated(nullptr);
	m_exporter->set_callback_on_vfb_layers_updated(nullptr);
	nb::gil_scoped_acquire gil;
	m_imageUpdateCallback = nb::callable();
}


void ProductionExporter::init(ZmqExporter* zmqExporter) {
	vassert(zmqExporter != nullptr);
	m_exporter = zmqExporter;
}


void ProductionExporter::setupCallbacks()
{
	// This method is only called from SceneExporter::init()

	m_exporter->set_callback_on_image_ready([this]() {
		cb_on_image_ready();
	});

	m_exporter->set_callback_on_bucket_ready([this](const VRayBaseTypes::AttrImage & img){
		cb_on_bucket_ready(img);
	});

	m_exporter->set_callback_on_rt_image_updated([this]() {
		cb_on_rt_image_updated();
	});

	m_exporter->set_callback_on_vfb_layers_updated([this](const std::string& layersJson) {
		cb_on_vfb_layers_updated(layersJson);
	});
}


void ProductionExporter::cb_on_image_ready()
{
	// updateImage() bails on m_renderFinished, so push the final frame before setting it.
	updateImage();
	m_renderFinished = true;
}


void ProductionExporter::setElementPasses(const nb::list& passes)
{
	// Python tuple shape: (pass_ptr, channelType, pluginInstanceName, subIndex).
	m_elementPasses.clear();
	m_elementPasses.reserve(passes.size());

	std::vector<std::pair<ZmqExporter::PerInstanceKey, ZmqExporter::ElementDestination>> destinations;
	destinations.reserve(passes.size());

	for (size_t i = 0; i < passes.size(); i++) {
		const auto entry = nb::cast<nb::tuple>(passes[i]);
		const size_t passPtr             = nb::cast<size_t>(entry[0]);
		const int    channelType         = nb::cast<int>(entry[1]);
		const auto   pluginInstanceName  = nb::cast<std::string>(entry[2]);
		const int    subIndex            = nb::cast<int>(entry[3]);
		auto* pass = reinterpret_cast<RenderPass*>(passPtr);
		m_elementPasses.push_back({
			pass,
			channelType,
			pluginInstanceName,
			subIndex,
		});

		// Lazily-unallocated ibufs (float_buffer.data == nullptr) yield a null buffer
		// and are silently dropped on the read side.
		ZmqExporter::ElementDestination dest;
		dest.buffer   = (pass->ibuf && pass->ibuf->float_buffer.data) ? pass->ibuf->float_buffer.data : nullptr;
		dest.width    = pass->rectx;
		dest.height   = pass->recty;
		dest.channels = pass->channels;
		destinations.push_back({ZmqExporter::PerInstanceKey{pluginInstanceName, subIndex}, dest});
	}

	m_exporter->setElementDestinations(std::move(destinations));
}


void ProductionExporter::cb_on_bucket_ready(const VRayBaseTypes::AttrImage& img)
{
	vassert(img.isBucket() && "Image for cb_on_bucket_ready is not bucket image");
	if (!img.isBucket()) {
		return;
	}


	auto now = high_resolution_clock::now();

	// Rate-limit screen updates
	if ((now - m_lastImageUpdate) > 100ms) {
		updateImage();
		m_lastImageUpdate = now;
	}
}


void ProductionExporter::cb_on_rt_image_updated()
{
	updateImage();
}


void ProductionExporter::cb_on_vfb_layers_updated(const std::string& layersJson)
{
	auto callback = ZmqServer::get().getPythonCallback("vfbLayersUpdate");

	if (!callback.is_none()) {
		invokePythonCallback("vfbLayersUpdate", callback, layersJson);
	}
}

void ProductionExporter::renderStart(RenderPass *renderPass, nb::callable&& cbImageUpdated, bool imageToBlender)
{
	m_lastImageUpdate = high_resolution_clock::now();

	m_imageUpdateCallback = std::move(cbImageUpdated);

	// Setting the pixel data to the RenderPass Blender object is prohibitively slow
	// to do in Python. Therefore we are forced to do it through a pointer to the
	// native structure. When imageToBlender is false, Python passes nullptr - the
	// server will suppress all image/element emits and we leave the render buffer
	// detached so nothing can write into a stale Blender pass.
	m_renderPass = renderPass;

	m_exporter->setImageToBlender(imageToBlender);

	if (renderPass) {
		// Point the main render layer at Blender's buffer so ZmqRenderImage::update()
		// writes directly there - eliminating the memcpy in updateImage().
		m_exporter->setRenderBuffer(
			renderPass->ibuf->float_buffer.data,
			renderPass->rectx,
			renderPass->recty,
			renderPass->channels
		);
	} else {
		m_exporter->setRenderBuffer(nullptr, 0, 0, 0);
	}
}


void ProductionExporter::renderEnd()
{
	if (m_settings.closeVfbOnStop) {
		m_exporter->freeRenderer();
	}
	else {
		m_exporter->stopRendering();
	}

	{
		// This method is called from Python. Unlock the GIL to avoid a deadlock from
		// trying to acquire the callbacks mutex here and in Python callbacks invoked
		// from server events.
		nb::gil_scoped_release noGIL;
		m_exporter->set_callback_on_image_ready(nullptr);
		m_exporter->set_callback_on_bucket_ready(nullptr);
		m_exporter->set_callback_on_rt_image_updated(nullptr);
		m_exporter->set_callback_on_vfb_layers_updated(nullptr);
	}

	m_renderFinished = true;
	// Release the reference to Blender's buffer before the render pass is freed.
	m_exporter->setRenderBuffer(nullptr, 0, 0, 0);
	m_exporter->clearElementDestinations();
	m_renderPass = nullptr;
	m_elementPasses.clear();
}


void ProductionExporter::renderFrame()
{
	nb::gil_scoped_release noGIL;

	m_renderFinished = false;

	m_exporter->commitChanges();
	m_exporter->start();
}

void ProductionExporter::continueRenderSequence()
{
	m_exporter->continueRenderSequence();
}


void ProductionExporter::renderSequence(const vray::AttrList<int>& sequences)
{
	nb::gil_scoped_release noGIL;

	m_renderFinished = false;

	m_exporter->commitChanges();
	m_exporter->setLastRenderedFrame(0);
	m_exporter->renderSequence(sequences);
}

bool VRayForBlender::ProductionExporter::isRendering()
{
	return !m_renderFinished && m_exporter->isRendering();
}

int VRayForBlender::ProductionExporter::lastRenderedFrame()
{
	return m_exporter->getLastRenderedFrame();
}

void ProductionExporter::setRenderFrame(float frame)
{
	m_exporter->setCurrentFrame(frame);
}

void VRayForBlender::ProductionExporter::abortRender()
{
	nb::gil_scoped_release noGIL;
	m_renderFinished = true;
	m_exporter->abortRender();
}

/// Notify the Python add-on that the render pass buffer has new data.
/// ZmqRenderImage::update() normally writes directly into Blender's buffer
/// (set up via setRenderBuffer in renderStart), so no copy is needed here.
void ProductionExporter::updateImage()
{
	if (m_renderFinished || !m_exporter->getImageToBlender()) {
		return;
	}

	// The exporter's isRendering() method should be checked before accessing the render pass buffer,
	// because there will not be a valid reference to the buffer if rendering has been aborted.
	vassert(m_renderPass);
	if (m_exporter->isRendering() && !m_imageUpdateCallback.is_none() && m_renderPass) {
		const RenderImage& layerImg = m_exporter->getImage();
		if (layerImg.channels != m_renderPass->channels){
			// TODO: figure out when RenderPass might not be RGBA
			Logger::error("Rendered image (%1% channles) not in the same format as RenderPass (%2% channels)",
								layerImg.channels, m_renderPass->channels);
			return;
		}

		// Normally the data is already in Blender's buffer (written there by ZmqRenderImage::update()).
		// Fall back to copying if dimensions changed mid-render and ZmqRenderImage reallocated.
		float* dest = m_renderPass->ibuf->float_buffer.data;
		if (layerImg.pixels != dest) {
			const int destSizeX = std::min(layerImg.w,  m_renderPass->rectx);
			const int destSizeY = std::min(layerImg.h, m_renderPass->recty);
			::memcpy(dest, layerImg.pixels, destSizeX * destSizeY * sizeof(float[4]));
		}

		// Notify Python add-on that the image is written to the render pass buffer and can be
		// updated on the screen
		invokePythonCallback("imageUpdated", m_imageUpdateCallback);
	}
}
