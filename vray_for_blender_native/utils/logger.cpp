#include "logger.hpp"

#include <array>
#include <cstring>
#include <iomanip>
#include <iostream>
#include <sstream>


#include "utils/assert.h"

namespace 
{

const std::string COLOR_RED      = "\033[0;31m";
const std::string COLOR_GREEN    = "\033[0;32m";
const std::string COLOR_YELLOW   = "\033[0;33m";
const std::string COLOR_BLUE     = "\033[0;34m";
const std::string COLOR_CYAN     = "\033[1;34m";
const std::string COLOR_MAGENTA  = "\033[0;35m";
const std::string COLOR_DEFAULT  = "\033[0m";



const std::vector<std::string> LEVEL_NAMES = {"", "ERROR", "WARNING", "INFO", "DEBUG"};
const std::vector<std::string> LEVEL_COLORS = {COLOR_DEFAULT, COLOR_RED, COLOR_YELLOW, COLOR_CYAN, COLOR_DEFAULT};

} 


const std::string& levelColor(Logger::LogLevel logLevel) {
	VRAY_ASSERT(LogLevel::Always <= logLevel && logLevel <= LogLevel::Debug);
	return LEVEL_COLORS[static_cast<int>(logLevel)];
}


const std::string& Logger::levelName(Logger::LogLevel logLevel) {
	VRAY_ASSERT(LogLevel::Always <= logLevel && logLevel <= LogLevel::Debug);
	return LEVEL_NAMES[static_cast<int>(logLevel)];
}


// ConsoleLogger
void ConsoleLogger::write(Logger::LogLevel level, const std::string& msg) {

	std::cout << levelColor(level) << msg << levelColor(Logger::LogLevel::Always);
}


// FileLogger
FileLogger::FileLogger(const std::string& filePath) :
	m_file(filePath)
{
}

void FileLogger::write(Logger::LogLevel /*level*/, const std::string& msg) {

	m_file << msg;
}

bool FileLogger::good() const {
	return m_file.good();
}


Logger& Logger::get()
{
	static Logger logger;
	return logger;
}


Logger::~Logger()
{
	VRAY_ASSERT(!m_isRunning && "Logger must be stopped before being destroyed");
}


void Logger::addWriter(WriterPtr writer) {
	std::scoped_lock lock(m_writersLock);
	m_writers.emplace_back(std::move(writer));
}


void Logger::setLogLevel(LogLevel value)
{
	m_logLevel = value;
}


void Logger::run()
{
	while (m_isRunning) {
		if (m_queue.empty()) {
			std::this_thread::sleep_for(std::chrono::milliseconds(10));
		}
		else {
			flush();
		}
	}
}


void Logger::flush()
{
	LogQueue logBatch;
	{
		std::scoped_lock lock(m_queueLock);
		std::swap(m_queue, logBatch);
	}


	std::scoped_lock lock(m_writersLock);
	
	for (auto& writer : m_writers) {
		for (const LogItem& item : logBatch) {
			writer->write(item.level, item.msg);
		}
	}
}


void Logger::startLogging()
{
	m_isRunning = true;
	m_logThread = std::jthread(&Logger::run, this);
}


void Logger::stopLogging()
{
	if (!m_isRunning) {
		return;
	}
	m_isRunning = false;

	flush();

	// When the plugin is disabled and then enabled again,  
	// a new writer will be added, causing the log to be published twice.
	std::scoped_lock lock(m_writersLock);
	m_writers.clear();

	if (m_logThread.joinable()) {
		m_logThread.join();
	}
}



std::string Logger::formatTime() {
	using namespace std::chrono;

	auto now = system_clock::now();
	// Get duration in milliseconds
	auto ms = duration_cast<milliseconds>(now.time_since_epoch()).count() % 1000;

	const time_t timeNow = system_clock::to_time_t(now);

	// get printable result:
	tm tmNow = {};
	gmtime_s(&tmNow, &timeNow);

	std::stringstream ss;
	ss << std::put_time(&tmNow, "%d-%m-%Y %H:%M:%S:") << std::setw(3) << std::setfill('0') << ms;
	return ss.str();
}

