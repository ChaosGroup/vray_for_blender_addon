#include "timers.h"

std::map<std::string, std::pair<float, int>> ScopeTimer::s_times;
std::mutex ScopeTimer::s_lock;
bool ScopeTimer::s_running = false;



ScopeTimer::ScopeTimer(const std::string& m_name) :
	m_tmStart(std::chrono::high_resolution_clock::now()),
	m_name(m_name)
{
};

ScopeTimer::~ScopeTimer()
{
	std::lock_guard<std::mutex> lock(s_lock);
	
	if (s_running) {
		const auto tmEnd = std::chrono::high_resolution_clock::now();
		const auto clockTime = std::chrono::duration_cast<std::chrono::microseconds>(tmEnd - m_tmStart).count();

		s_times[m_name].first += static_cast<float>(clockTime) / 1000;  // us -> ms
		s_times[m_name].second += 1;
	}
}

void ScopeTimer::startCollection()
{
	std::lock_guard<std::mutex> lock(s_lock);
	s_times.clear();
	s_running = true;
}


void ScopeTimer::endCollection(bool printStats, const std::string& title)
{
	std::lock_guard<std::mutex> lock(s_lock);
	
	if (s_running && printStats) {
		std::ostringstream os;
		os << std::endl << title << std::endl
			<< std::string(50, '-') << std::endl
			<< std::left << std::setw(35) << "Operation"
			<< std::right << std::setw(10) << "Count"
			<< std::right << std::setw(11) << "Time" << std::endl;

		for (const auto it : s_times)
		{
			os << std::left << std::setw(35) << it.first.c_str()
				<< std::right << std::setw(10) << it.second.second
				<< std::right << std::setw(8) << std::setprecision(4) << it.second.first << " ms" << std::endl;
		}

		os << std::string(50, '-') << std::endl;
		std::cout << os.str();
	}

	s_running = false;
}

