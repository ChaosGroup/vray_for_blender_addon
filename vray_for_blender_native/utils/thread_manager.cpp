#include "thread_manager.h"
#include "logger.hpp"
#include "vassert.h"


namespace VRayForBlender {

ThreadManager::ThreadManager(int thCount)
	: m_stop(false)
{
	setThreadCount(thCount);
}

ThreadManager::Ptr ThreadManager::make(int thCount) {
	return ThreadManager::Ptr(new ThreadManager(thCount));
}

ThreadManager::~ThreadManager() {
	// m_workers is not protected because there is no sane way to do this from inside the ThreadManager
	// it can't handle calling some methond and dtor concurrently
	if (!m_workers.empty()) {
		vassert(!"VFB ThreadManager exiting while threads are running!");
		stop();
	}
}

void ThreadManager::setThreadCount(int count) {
	// It is easier to stop and re-create all threads as
	// it does not force us to stop only indivudual threads and syncronize m_stop.
	if (count == m_workers.size()) {
		return;
	}

	stop();
	{
		std::scoped_lock lock(m_queueMtx);
		m_stop = false;
	}

	if (count > 0) {
		for (int c = 0; c < count; ++c) {
			m_workers.emplace_back(std::thread(&ThreadManager::workerRun, this, c));
		}
	}
}

void ThreadManager::stop() {
	{
		std::scoped_lock lock(m_queueMtx);
		m_stop = true;
	}

	if (!m_workers.empty()) {
		m_queueCondVar.notify_all();

		for (int c = 0; c < m_workers.size(); ++c) {
			if (m_workers[c].joinable()) {
				m_workers[c].join();
			} else {
				vassert(!"VFB ThreadManager's thread is not joinable during stop!");
			}
		}

		m_workers.clear();
	}
}

void ThreadManager::addTask(ThreadManager::Task task, ThreadManager::Priority priority) {
	if (m_workers.empty()) {
		// no workers - do the job ourselves
		task(-1, m_stop);
	} else {
		{
			std::scoped_lock lock(m_queueMtx);
			if (priority == Priority::HIGH) {
				m_tasks.push_front(task);
			} else {
				m_tasks.push_back(task);
			}
		}
		m_queueCondVar.notify_one();
	}
}

void ThreadManager::workerRun(int thIdx) {
	Logger::debug("Thread [%1%] starting...", thIdx);

	while (!m_stop) {
		Task task;
		{
			std::unique_lock lock(m_queueMtx);
			// wait for task or stop
			m_queueCondVar.wait(lock, [this] { return !m_tasks.empty() || m_stop; });

			if (m_stop) {
				break;
			}

			task = m_tasks.front();
			m_tasks.pop_front();
		}

		task(thIdx, m_stop);
	}

	Logger::info("Thread [%1%] stopping...", thIdx);
}

} // end namespace VRayForBlender
