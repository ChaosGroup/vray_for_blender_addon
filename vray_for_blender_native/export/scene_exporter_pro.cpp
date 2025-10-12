#include "scene_exporter_pro.h"

#include "zmq_exporter.h"
#include "zmq_server.h"
#include "api/interop/utils.hpp"
#include "utils/logger.hpp"
#include "utils/synchronization.hpp"

namespace py = boost::python;

using namespace std::chrono;
using namespace VRayForBlender;


ProductionExporter::ProductionExporter(const ExporterSettings& settings)
	: SceneExporter(settings)
{
	SceneExporter::init();
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
	m_renderFinished = true;
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

void ProductionExporter::renderStart(RenderPass *renderPass, py::object cbImageUpdated)
{
	m_lastImageUpdate = high_resolution_clock::now();

	m_imageUpdateCallback = cbImageUpdated;

	// Setting the pixel data to the RenderPass Blender object is prohibitively slow
	// to do in Python. Therefore we are forced to do it through a pointer to the 
	// native structure
	m_renderPass = renderPass;
}


void ProductionExporter::renderEnd()
{
	if (m_settings.closeVfbOnStop) {
		m_exporter->free();
	}
	else {
		m_exporter->stop();
	}

	{
		// This method is called from Python. Unlock the GIL to avoid a deadlock from 
		// trying to acquire the callbacks mutex here and in Python callbacks invoked
		// from server events.
		WithNoGIL noGIL;
		m_exporter->set_callback_on_image_ready(nullptr);
		m_exporter->set_callback_on_bucket_ready(nullptr);
		m_exporter->set_callback_on_rt_image_updated(nullptr);
		m_exporter->set_callback_on_vfb_layers_updated(nullptr);
	}

	m_renderFinished = true;
	m_renderPass = nullptr;
}


void ProductionExporter::renderFrame()
{
	WithNoGIL noGIL;

	m_renderFinished = false;

	m_exporter->commitChanges();
	m_exporter->start();
}

void ProductionExporter::continueRenderSequence()
{
	m_exporter->continueRenderSequence();
}

void ProductionExporter::renderSequence(int start, int end, int step)
{
	WithNoGIL noGIL;

	m_renderFinished = false;

	m_exporter->commitChanges();
	m_exporter->setLastRenderedFrame(0);
	m_exporter->renderSequence(start, end, step);
}

bool VRayForBlender::ProductionExporter::renderSequenceRunning()
{
	WithNoGIL noGIL;

	return !m_renderFinished && !m_exporter->isAborted();
}

bool VRayForBlender::ProductionExporter::vrsceneExportRunning()
{
	WithNoGIL noGIL;

	return m_vrsceneExportInProgress;
}

int VRayForBlender::ProductionExporter::lastRenderedFrame()
{
	WithNoGIL noGIL;

	return m_exporter->getLastRenderedFrame();
}

void ProductionExporter::setRenderFrame(float frame)
{
	m_exporter->setCurrentFrame(frame);
}

void VRayForBlender::ProductionExporter::abortRender()
{
	WithNoGIL noGIL;
	m_renderFinished = true;
	m_exporter->abortRender();
}

/// Set image data to the render pass and call the screen update handler in Python
void ProductionExporter::updateImage()
{
	// The exporter's isAborted() method should be checked before accessing the render pass buffer,
	// because there will not be a valid reference to the buffer if rendering has been aborted.
	if (!m_exporter->isAborted() && !m_imageUpdateCallback.is_none() && !m_renderFinished) {
		auto layerImg = m_exporter->getImage();
		if (layerImg.channels != m_renderPass->channels){
			// TODO: figure out when RenderPass might not be RGBA
			Logger::error("Rendered image (%1% channles) not in the same format as RenderPass (%2% channels)",
								layerImg.channels, m_renderPass->channels);
			return;
		}


		// Occasionally, there is a slight rounding difference between the layerImg sizes  
		// calculated in the 'vray_blender.exporting.view_export._fillViewFromProdCamera' function  
		// of the Python module and the 'm_renderPass' rect size calculated in Blender.  
		// If the difference is smaller than 'ALLOWED_SIZE_DIFF', it can be ignored.
		const int ALLOWED_SIZE_DIFF = 1;

		if (layerImg.w * layerImg.h == 0 || 
			(std::abs(layerImg.w - m_renderPass->rectx) > ALLOWED_SIZE_DIFF) ||
			(std::abs(layerImg.h - m_renderPass->recty) > ALLOWED_SIZE_DIFF) )
		{
			Logger::error("Rendered image: invalid image size %1%x%2%, expected %3%x%4%",
				layerImg.w, layerImg.h, m_renderPass->rectx, m_renderPass->recty);
			return;
		}
		
		int destSizeX = std::min(layerImg.w,  m_renderPass->rectx);
		int destSizeY = std::min(layerImg.h, m_renderPass->recty);

		auto dest = m_renderPass->ibuf->float_buffer.data;
		::memcpy(dest, layerImg.pixels, destSizeX * destSizeY * sizeof(float[4]));

		// Notify Python add-on that the image is written to the render pass buffer and can be 
		// updated on the screen
		invokePythonCallback("imageUpdated", m_imageUpdateCallback);
	}
}
