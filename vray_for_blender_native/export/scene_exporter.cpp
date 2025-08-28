#include "scene_exporter.h"

#include <functional>

#include <base_types.h>
#include <zmq_message.hpp>

#include "assets/hair_exporter.h"
#include "assets/mesh_exporter.h"
#include "assets/object_exporter.h"
#include "assets/smoke_exporter.h"
#include "plugin_desc.hpp"
#include "vassert.h"
#include "utils/timers.h"
#include "utils/synchronization.hpp"



using namespace VRayForBlender;
using namespace VrayZmqWrapper;
using namespace VRayBaseTypes;


SceneExporter::SceneExporter(const ExporterSettings& settings)
		: m_settings(settings)
		, m_wg(std::make_unique<CondWaitGroup>(0))
		, imageReadyReceived(false)
{
}


SceneExporter::~SceneExporter()
{
	free();
}


void SceneExporter::init() {
	m_exporter.reset(new ZmqExporter(m_settings));

	vassert(m_exporter && "Failed to create exporter!");
	m_exporter->init();

	// directly bind to the engine
	m_exporter->set_callback_on_message_updated([this](const std::string& info){
				std::lock_guard<std::mutex> grd(engineUpdateMsgMtx);
				engineUpdateMessage = info;
			});

	m_exporter->set_callback_on_async_op_complete([this](proto::RendererAsyncOp op, bool success, const std::string& message){
			switch (op) {
			case proto::RendererAsyncOp::ExportVrscene:
				m_vrsceneExportInProgress = false;
				break;

			default:
				vassert("Invalid async operation type."); 
			}

			if (!success) {
				Logger::error("Asyncronous operation {} failed: {}", static_cast<char>(op), message);
			}
		});

	setupCallbacks();

	m_threadManager = ThreadManager::make(2);
}


std::string SceneExporter::getEngineUpdateMessage()
{
	std::lock_guard<std::mutex> grd(engineUpdateMsgMtx);
	return engineUpdateMessage;
}


bool SceneExporter::isRenderReady()
{
	if (imageReadyReceived) {
		// Clearing the render ready flag,
		// so the checking for image and tagging for redrawing
		// in the python render engine implementation could start
		imageReadyReceived = false;
		return true;
	}

	return false;
}


bool SceneExporter::imageWasUpdated()
{
	if (imageUpdated) {
		imageUpdated = false;
		return true;
	}
	return false;
}

void SceneExporter::setRenderStoppedCallback(py::object cbRenderStopped)
{
	m_cbRenderStopped = cbRenderStopped;
	m_exporter->set_callback_on_render_stopped([this]() {
		invokePythonCallback("renderStoppedCallback", m_cbRenderStopped);
	});

}

void SceneExporter::free()
{
	if (m_threadManager) {
		m_threadManager->stop();
	}
}


void SceneExporter::setRenderSizes(const RenderSizes& sizeData)
{
	m_exporter->setRenderSize(sizeData);
}


void SceneExporter::setCameraName(const std::string& cameraName)
{
	m_exporter->setCameraName(cameraName);
}


void SceneExporter::syncView(const ViewSettings& viewSettings)
{
	m_exporter->syncView(viewSettings);
}


void SceneExporter::openVFB()
{
	m_exporter->showVFB();
}

void SceneExporter::setVfbAlwaysOnTop(bool alwaysOnTop)
{
	m_exporter->setVfbAlwaysOnTop(alwaysOnTop);
}

void SceneExporter::clearScene() {
	m_exporter->clearScene();

	// Sending the RendererAction::Reset message (which executes ZmqExporter::clearScene())
	// triggers VRayRenderer::clearScene(), stopping the renderer.
	m_rendererStarted = false;
}


void SceneExporter::clearFrameData(float upToTime) {
	m_exporter->clearFrameData(upToTime);
}


RenderImage SceneExporter::getImage()
{
	return m_exporter->getImage();
}


RenderImage SceneExporter::getRenderPassImage(const std::string& passName)
{
	return m_exporter->getPass(passName);
}


float SceneExporter::getRenderProgress() const
{
	return m_exporter->getRenderProgress();
}


void SceneExporter::onImageUpdated()
{
	if (!m_tmStartExport.has_value()){
		return;
	}

	const auto now = std::chrono::high_resolution_clock::now();
	const auto totalMs = std::chrono::duration_cast<std::chrono::microseconds>((now - m_tmStartExport.value()) / 1000.f).count();

	std::ostringstream os;
    os  << "Time to first frame: " << totalMs << " ms" << std::endl;
	std::cout << os.str();

	m_tmStartExport.reset();
}


void SceneExporter::exportMesh(MeshDataPtr mesh, bool asyncExport)
{
	if (asyncExport) {
		m_wg->add(1);
		m_threadManager->addTask([this, mesh](int, const volatile bool &)mutable{
			NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);

			PluginDesc pluginDesc(mesh->name, "GeomStaticMesh");

			{

				ScopeTimer tm("fillMeshData async");
				Assets::fillMeshData(*mesh, pluginDesc);
			}

			m_exporter->exportPlugin(pluginDesc);

			{
				WithGIL gil;
				mesh.reset();
			}
		}, ThreadManager::Priority::LOW);
	} else {
		PluginDesc pluginDesc(mesh->name, "GeomStaticMesh");
		ScopeTimer tm("fillMeshData sync");
		Assets::fillMeshData(*mesh, pluginDesc);
		m_exporter->exportPlugin(pluginDesc);
		WithGIL gil;
		mesh.reset();
	}
}


void SceneExporter::exportHair(HairDataPtr hair)
{
	m_wg->add(1);
	m_threadManager->addTask([this, hair](int, const volatile bool &) mutable {
		NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);
		
		ScopeTimer tm("exportHair");
		Assets::exportGeomHair(*hair, *m_exporter);

		{
			WithGIL gil;
			hair.reset();
		}

	}, ThreadManager::Priority::LOW);
}


void SceneExporter::exportSmoke(SmokeDataPtr smoke)
{
	m_wg->add(1);
	m_threadManager->addTask([this, smoke](int, const volatile bool &) mutable {
		NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);
		
		using Matrix = float[4][4];
		AttrTransform transform(*reinterpret_cast<Matrix*>(smoke->transform.data()));

		ScopeTimer tm("exportSmoke");
		Assets::exportVRayNodePhxShaderSim(smoke->name, smoke->cacheDir, transform, smoke->domainRes.data(), *m_exporter);

		{
			WithGIL gil;
			smoke.reset();
		}

	}, ThreadManager::Priority::LOW);
}



void SceneExporter::exportPointCloud(PointCloudDataPtr pc)
{
	m_wg->add(1);
	m_threadManager->addTask([this, pc](int, const volatile bool &) mutable {
		NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);
		
		ScopeTimer tm("exportPointCloud");
		Assets::exportPointCloud(*pc, *m_exporter);

		{
			WithGIL gil;
			pc.reset();
		}

	}, ThreadManager::Priority::LOW);
}


void SceneExporter::exportInstancer(InstancerDataPtr inst)
{
	m_wg->add(1);
	m_threadManager->addTask([this, inst](int, const volatile bool &) mutable {
		NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);
		
		ScopeTimer tm("exportInstancer");
		Assets::exportInstancer(*inst, *m_exporter);

		{
			WithGIL gil;
			inst.reset();
		}

	}, ThreadManager::Priority::LOW);

}


void SceneExporter::startExport(int threadCount)
{
	m_threadManager->setThreadCount(threadCount);
}


void SceneExporter::finishExport(bool interactive)
{
	WithNoGIL noGIL;

	ScopeTimer tm("finish_export: wait");
	m_wg->wait();
	

	if (interactive)
	{
		if (!m_rendererStarted){
			// Only start the renderer once, then use commitChanges() to apply changs to the scene
			m_exporter->start();
			m_rendererStarted = true;
		}
		else {
			m_exporter->commitChanges();
		}
	}
}


int SceneExporter::writeVrscene(const ExportSceneSettings& exportSettings)
{
	bool inProgress = false;

	if (m_vrsceneExportInProgress.compare_exchange_strong(inProgress, true)) {
		const int result = m_exporter->exportVrscene(exportSettings);
		if (!result) {
			m_vrsceneExportInProgress.store(false);
		}
		return result;
	}
	return false;
}


void SceneExporter::startStatsCollection()
{
	ScopeTimer::startCollection();
}


void SceneExporter::endStatsCollection(bool printStats, const std::string& title)
{
	ScopeTimer::endCollection(printStats, title);
}


