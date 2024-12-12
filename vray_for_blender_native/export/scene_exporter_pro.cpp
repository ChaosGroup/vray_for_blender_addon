#include "scene_exporter_pro.h"

#include "zmq_exporter.h"
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
	m_exporter->set_callback_on_image_ready([this]() {
		cb_on_image_ready();
	});
	
	m_exporter->set_callback_on_bucket_ready([this](const VRayBaseTypes::AttrImage & img){
		cb_on_bucket_ready(img);
	});

	m_exporter->set_callback_on_rt_image_updated([this]() {
		cb_on_rt_image_updated();
	});
}


void ProductionExporter::cb_on_image_ready()
{
	m_renderFinished = true;
}


void ProductionExporter::cb_on_bucket_ready(const VRayBaseTypes::AttrImage& img)
{
	VRAY_ASSERT(img.isBucket() && "Image for cb_on_bucket_ready is not bucket image");
	if (!img.isBucket()) {
		return;
	}

	std::lock_guard<std::mutex> l(m_callback_mtx);
	
	auto now = high_resolution_clock::now();

	// Rate-limit screen updates
	if ((now - m_lastImageUpdate) > 100ms) {
		updateImage();
		m_lastImageUpdate = now;
	}
}


void ProductionExporter::cb_on_rt_image_updated()
{
	std::lock_guard<std::mutex> l(m_callback_mtx);
	updateImage();
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
	std::lock_guard<std::mutex> l(m_callback_mtx);

	if (m_settings.closeVfbOnStop) {
		m_exporter->free();
	}
	else {
		m_exporter->stop();
	}

	m_exporter->set_callback_on_image_ready(nullptr);
	m_exporter->set_callback_on_bucket_ready(nullptr);
	m_exporter->set_callback_on_rt_image_updated(nullptr);
	m_exporter->set_callback_on_vfb_layers_updated(nullptr);

	m_renderFinished = true;
	m_renderPass = nullptr;
}


bool ProductionExporter::renderFrame(bool waitForCompletion)
{
	WithNoGIL noGIL;
		
	m_renderFinished = false;

	m_exporter->commitChanges();
	m_exporter->start();

	if (waitForCompletion) {
		while (!m_renderFinished && !m_exporter->isAborted())
		{
			std::this_thread::sleep_for(100ms);
		}

		return !m_exporter->isAborted();
	}

	return true;
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
			Logger::error("Rendered image ({} channles) not in the same format as RenderPass ({} channels)", 
								layerImg.channels, m_renderPass->channels);
			return;
		}

		if (layerImg.w * layerImg.h == 0 || 
			layerImg.w != m_renderPass->rectx ||
			layerImg.h != m_renderPass->recty )
		{
			Logger::error("Rendered image: invalid image size {} x {}, expected {} {}",	
				layerImg.w, layerImg.h, m_renderPass->rectx, m_renderPass->recty);
			return;
		}

		auto dest = m_renderPass->ibuf->float_buffer.data;
		::memcpy(dest, layerImg.pixels, m_renderPass->rectx * m_renderPass->recty * sizeof(float[4]));

		// Notify Python add-on that the image is written to the render pass buffer and can be 
		// updated on the screen
		invokePythonCallback("imageUpdated", m_imageUpdateCallback);
	}
}
