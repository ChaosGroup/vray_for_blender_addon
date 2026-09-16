// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

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


using namespace VRayForBlender;
using namespace VrayZmqWrapper;
using namespace VRayBaseTypes;


SceneExporter::SceneExporter() :
		m_wg(std::make_unique<CondWaitGroup>(0)),
		m_threadManager(ThreadManager::make(2))
{
}


SceneExporter::~SceneExporter()
{
	free();
}


void SceneExporter::init(ExporterBase* policy, const ExporterSettings& settings) {
	m_settings = settings;

	// The main renderer is reused across render sessions, so reset the sticky status message -
	// otherwise a message from a previous render (e.g. a warning shown at a higher log level)
	// would linger in the viewport status line for the next render.
	{
		std::lock_guard<std::mutex> grd(engineUpdateMsgMtx);
		engineUpdateMessage.clear();
	}

	if (!m_exporter || m_exporter->isStopped()) {
		const auto exporterType = static_cast<VrayZmqWrapper::ExporterType>(settings.exporterType);
		m_exporter.reset(new ZmqExporter(exporterType));
	}

	m_policy.reset(policy);
	m_policy->init(m_exporter.get());

	m_exporter->init(m_settings);

	m_exporter->set_callback_on_message_updated([this](const std::string& info){
				std::lock_guard<std::mutex> grd(engineUpdateMsgMtx);
				engineUpdateMessage = info;
			});

	m_exporter->set_callback_on_async_op_complete([this](proto::RendererAsyncOp op, bool success, const std::string& message){
			switch (op) {
			case proto::RendererAsyncOp::ExportVrscene:
				m_vrsceneExportInProgress = false;
				break;
			case proto::RendererAsyncOp::ExportVrmesh: {
				std::lock_guard<std::mutex> lk(m_proxyExportWaitMtx);
				m_lastProxyExportResult = success;
				m_lastProxyExportMessage = success ? std::string{} : message;
				m_proxyExportInProgress = false;
				break;
			}

			default:
				vassert("Invalid async operation type.");
			}

			if (op == proto::RendererAsyncOp::ExportVrmesh) {
				m_proxyExportCv.notify_all();
			}

			if (!success) {
				Logger::error("Asyncronous operation %1% failed: %2%", static_cast<char>(op), message);
			}
		});

	// Set up any callbacks specific to the derived classes
	setupCallbacks();

	m_threadManager->stop();
}


std::string SceneExporter::getEngineUpdateMessage()
{
	std::lock_guard<std::mutex> grd(engineUpdateMsgMtx);
	return engineUpdateMessage;
}


bool SceneExporter::isRenderReady()
{
	return m_policy->isRenderReady();
}


bool SceneExporter::imageWasUpdated()
{
	return m_policy->imageWasUpdated();
}

bool SceneExporter::vrsceneExportRunning()
{
	nb::gil_scoped_release noGIL;
	return m_vrsceneExportInProgress;
}

void SceneExporter::setRenderStoppedCallback(nb::callable&& cbRenderStopped)
{
	m_exporter->set_callback_on_render_stopped([renderStoppedCallback = std::move(cbRenderStopped)](bool isAborted) {
		invokePythonCallback("renderStoppedCallback", renderStoppedCallback, isAborted);
	});

}

void SceneExporter::free()
{
	// Stop all activity on ZmqExportet before deleting it or the policy which uses it
	m_exporter->stop();
	m_exporter->detach();

	m_policy.reset();
	m_exporter.reset();

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


void SceneExporter::setResumableRendering(bool enabled, const std::string& outputFileName, int autosaveSeconds, bool deleteOnSuccess)
{
	m_exporter->setResumableRendering(enabled, outputFileName, autosaveSeconds, deleteOnSuccess);
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


void SceneExporter::requestRenderChannel(int channelType, const std::string& pluginInstanceName, int subIndex)
{
	m_exporter->requestRenderChannel(channelType, pluginInstanceName, subIndex);
}


void SceneExporter::queuePendingRef(nb::object&& ref)
{
	std::lock_guard lock(m_pendingRefsMtx);
	m_pendingRefs.push_back(std::move(ref));
}


void SceneExporter::drainPendingRefs()
{
	std::vector<nb::object> refs;
	{
		std::lock_guard lock(m_pendingRefsMtx);
		refs.swap(m_pendingRefs);
	}

	if (!refs.empty()) {
		nb::gil_scoped_acquire gil;
		refs.clear();
	}
}


void SceneExporter::exportMesh(MeshDataPtr mesh, bool asyncExport)
{
	if (asyncExport) {
		m_wg->add(1);
		m_threadManager->addTask([this, mesh](int, const std::atomic<bool> &)mutable{
			NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);

			PluginDesc pluginDesc(mesh->name, "GeomStaticMesh");

			{

				ScopeTimer tm("fillMeshData async");
				Assets::fillMeshData(*mesh, pluginDesc);
			}

			m_exporter->exportPlugin(pluginDesc);

			queuePendingRef(std::move(mesh->ref));
			mesh.reset();
		}, ThreadManager::Priority::LOW);
	} else {
		PluginDesc pluginDesc(mesh->name, "GeomStaticMesh");
		ScopeTimer tm("fillMeshData sync");
		Assets::fillMeshData(*mesh, pluginDesc);
		m_exporter->exportPlugin(pluginDesc);
		nb::gil_scoped_acquire gil;
		mesh.reset();
	}
}


void SceneExporter::exportHair(HairDataPtr hair)
{
	m_wg->add(1);
	m_threadManager->addTask([this, hair](int, const std::atomic<bool> &) mutable {
		NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);

		ScopeTimer tm("exportHair");
		Assets::exportGeomHair(*hair, *m_exporter);

		{
			nb::gil_scoped_acquire gil;
			hair.reset();
		}

	}, ThreadManager::Priority::LOW);
}


void SceneExporter::exportSmoke(SmokeDataPtr smoke)
{
	m_wg->add(1);
	m_threadManager->addTask([this, smoke](int, const std::atomic<bool> &) mutable {
		NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);

		using Matrix = float[4][4];
		AttrTransform transform(*reinterpret_cast<Matrix*>(smoke->transform.data()));

		ScopeTimer tm("exportSmoke");
		Assets::exportVRayNodePhxShaderSim(smoke->name, smoke->cacheDir, transform, smoke->domainRes.data(), *m_exporter);

		{
			nb::gil_scoped_acquire gil;
			smoke.reset();
		}

	}, ThreadManager::Priority::LOW);
}



void SceneExporter::exportPointCloud(PointCloudDataPtr pc, bool asyncExport)
{
	if (asyncExport) {
		m_wg->add(1);
		m_threadManager->addTask([this, pc](int, const std::atomic<bool> &) mutable {
			NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);

			ScopeTimer tm("exportPointCloud");
			Assets::exportPointCloud(*pc, *m_exporter);

			{
				nb::gil_scoped_acquire gil;
				pc.reset();
			}

		}, ThreadManager::Priority::LOW);
	} else {
		ScopeTimer tm("exportPointCloud sync");
		Assets::exportPointCloud(*pc, *m_exporter);
		nb::gil_scoped_acquire gil;
		pc.reset();
	}
}


void SceneExporter::exportInstancer(InstancerDataPtr inst)
{
	m_wg->add(1);
	m_threadManager->addTask([this, inst](int, const std::atomic<bool> &) mutable {
		NotifyTaskDone<CondWaitGroup> doneTask(*m_wg);

		ScopeTimer tm("exportInstancer");
		Assets::exportInstancer(*inst, *m_exporter);

		{
			nb::gil_scoped_acquire gil;
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
	nb::gil_scoped_release noGIL;

	{
		ScopeTimer tm("finish_export: wait");
		m_wg->wait();
	}

	{
		ScopeTimer tm("finish_export: drain refs");
		drainPendingRefs();
	}


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

std::pair<bool, std::string> SceneExporter::exportProxy(const ProxyExportSettings& proxySettings)
{
	bool inProgress = false;

	if (!m_proxyExportInProgress.compare_exchange_strong(inProgress, true)) {
		return {false, "A proxy export is already in progress."};
	}

	const int result = m_exporter->exportProxy(proxySettings);
	if (!result) {
		m_proxyExportInProgress.store(false);
		return {false, "Failed to send proxy export request."};
	}

	std::unique_lock<std::mutex> lock(m_proxyExportWaitMtx);
	m_proxyExportCv.wait(lock, [this] { return !m_proxyExportInProgress.load(); });

	return { m_lastProxyExportResult, m_lastProxyExportMessage };
}


void SceneExporter::startStatsCollection()
{
	ScopeTimer::startCollection();
}


void SceneExporter::endStatsCollection(bool printStats, const std::string& title)
{
	ScopeTimer::endCollection(printStats, title);
}


