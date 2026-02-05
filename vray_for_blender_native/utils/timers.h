#pragma once

#include <chrono>
#include <map>
#include <mutex>
#include <iostream>



// Times operations and accumulates the times for the similar ones  
class ScopeTimer
{
public:
	explicit ScopeTimer(const std::string& name);
	~ScopeTimer();

	static void startCollection();
	static void endCollection(bool printStats, const std::string& title);

private:
	std::chrono::high_resolution_clock::time_point m_tmStart;
	std::string                           m_name;

	static std::map<std::string, std::pair<float, int>>	s_times;  // name -> (time_ms, count)
	static std::mutex s_lock;
	static bool s_running;
};
