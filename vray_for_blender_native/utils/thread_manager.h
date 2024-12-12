#pragma once

#include <functional>

#include <condition_variable>
#include <mutex>
#include <thread>

#include <atomic>
#include <chrono>
#include <deque>
#include <functional>
#include <memory>
#include <vector>

namespace VRayForBlender {

/// Class representing thread safe counter with the ability to wait for all
/// tasks to be done Implemented using mutex + cond var
class CondWaitGroup {
	CondWaitGroup(const CondWaitGroup &) = delete;
	CondWaitGroup &operator=(const CondWaitGroup &) = delete;

public:
	/// Initialize with number of tasks
	explicit CondWaitGroup(int tasks) : m_remaining(tasks)
	{
	}

	void add(int tasks)
	{
		std::scoped_lock lock(m_mtx);
		m_remaining += tasks;
	}

	/// Mark one task as done (substract 1 from counter)
	void done()
	{
		bool notify = false;

		{
			std::scoped_lock lock(m_mtx);
			--m_remaining;
			notify = m_remaining <= 0;
		}
		if (notify) {
			m_condVar.notify_all();
		}
	}

	/// Get number of tasks remaining at call time, could be less when function
	/// returns
	int remaining() const
	{
		return m_remaining;
	}

	/// Block until all tasks are done
	void wait()
	{
		std::unique_lock lock(m_mtx);
		while (m_remaining > 0) {
			m_condVar.wait(lock, [this]() { return this->m_remaining == 0; });
		}
	}

   private:
	int m_remaining;                    ///< number of remaining tasks
	std::mutex m_mtx;                   ///< lock protecting m_remaining
	std::condition_variable m_condVar;  ///< cond var to wait on m_remaining
};



/// Class representing thread safe counter with the ability to wait for all
/// tasks to be done Implemented using atomic int + sleep/busy wait
class BusyWaitGroup {
	BusyWaitGroup(const BusyWaitGroup &) = delete;
	BusyWaitGroup &operator=(const BusyWaitGroup &) = delete;

public:
	explicit BusyWaitGroup(int tasks) : m_remaining(tasks)
	{
	}


	/// Mark one task as done (substract 1 from counter)
	void done()
	{
		--m_remaining;
	}

	/// Get number of tasks remaining at call time, could be less when function
	/// returns
	int remaining() const
	{
		return m_remaining;
	}

	/// Block until all tasks are done
	/// @intervalMs - if positive will sleep that many ms between checks on the
	/// counter
	///               else it will busy wait
	void wait(int intervalMs = 10) const
	{
		while (m_remaining > 0) {
			if (intervalMs > 0) {
				std::this_thread::sleep_for(std::chrono::milliseconds(intervalMs));
			}
		}
	}

private:
	std::atomic<int> m_remaining;  ///< number of remaining tasks
};



/// RAII wrapper over a task made for CondWaitGroup/BusyWaitGroup this will call
/// the .done() method for the provided wait group object in destructor
/// This class ensures that the done method is called for each task in threaded
/// code so we dont block on wait group's wait call indefinitely
template<typename WGType> 
class NotifyTaskDone {
public:
	/// Construct with reference to a WaitGroup
	explicit NotifyTaskDone(WGType &waitGroup) : m_waitGroup(waitGroup)
	{
	}

	/// Signal the task as done
	~NotifyTaskDone()
	{
		m_waitGroup.done();
	}

	NotifyTaskDone(const NotifyTaskDone &) = delete;
	NotifyTaskDone &operator=(const NotifyTaskDone &) = delete;

private:
	WGType &m_waitGroup;
};



/// Basic thread manager able to execute tasks on different threads
class ThreadManager {

	ThreadManager(const ThreadManager &) = delete;
	ThreadManager &operator=(const ThreadManager &) = delete;

public:
	using Ptr = std::shared_ptr<ThreadManager>;

	// Intended to be used with lambda which will capture all neded data
	// must obey stop flag asap, threadIndex == -1 means calling thread (thCount
	// == 0)
	using Task = std::function<void(int threadIndex, const volatile bool &cancel)>;

	enum class Priority {
		LOW,
		HIGH,
	};

	// Create new Thread Manager with @thCount threads running
	// thCount 0 will mean that addTask will block and complete the task on the
	// current thread
	static Ptr make(int thCount);


	// stop must be called before reaching dtor
	~ThreadManager();

	// Get number of worker threads created for the instance
	int workerCount() const
	{
		return static_cast<int>(m_workers.size());
	}

	/// Change the current thread count to the given
	/// @param count - new number of threads, if less than 0, then forces to 0
	void setThreadCount(int count);

	// Stop all threads and discards any tasks not yet started
	// if thread count is 0, stop will still set the flag for stop to true
	// and if addTask was called from another thread it will signal the task to
	// stop
	void stop();

	// Add task to queue
	// @task with LOW  @priority will be added at the end      of queue
	// @task with HIGH @priority will be added at the begining of queue
	// okay to be called concurrently
	void addTask(Task task, Priority priority);

   private:
	/// Initialize ThreadManager
	/// @thCount - number of threads to create, if 0 all tasks will be executed
	/// immediately on calling thread
	explicit ThreadManager(int thCount);

	/// Base function for each thread
	void workerRun(int thIdx);

	std::mutex m_queueMtx;                   ///< lock guarding @m_tasks
	std::condition_variable m_queueCondVar;  ///< cond var for threads to wait for new tasks
	std::deque<Task> m_tasks;                ///< queue of the pending tasks
	std::vector<std::thread> m_workers;      ///< all worker threads created for this instace
	volatile bool m_stop;     ///< if set to true, will stop all threads, also passed
	                          ///< to each task as a cancellation token
};

}  // namespace VRayForBlender

