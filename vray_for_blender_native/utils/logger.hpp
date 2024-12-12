#pragma once

#include <cstdarg>
#include <format>
#include <sstream>
#include <string>
#include <thread>
#include <queue>
#include <mutex>
#include <atomic>

#include <utils/file_writer.h>


enum class LogLevel : int {
	Always = 0,	// Write regardless of the current log level
	Error,
	Warning,
	Info,
	Debug,
};


/// Interface for log writers. Log writers provide to the Logger class publishing of log messages.  
struct LogWriter {
	virtual ~LogWriter() = default;
	virtual void write (LogLevel level, const std::string& msg) = 0;
};


/// Singleton logger. 
/// 
/// Usage:
///   Logger::get().addWriter(...);
///   Logger::get().startLogging();
///   Logger::get().log(...)
/// 
class Logger {

	Logger(const Logger&) = delete;
	Logger& operator=(const Logger&) = delete;

	Logger() = default;
	~Logger();

	struct LogItem {
		LogLevel    level;
		std::string msg;
	};

	using LogQueue  = std::vector<LogItem>;
	using WriterPtr = std::unique_ptr<LogWriter>;
	using Writers   = std::vector<WriterPtr>;

public:

	using LogLevel = LogLevel;

	/// Log with corresponding level. 
	/// The format string should conform to the std::format specification
	
	template <class ...TArgs>
	static void always(std::string format, TArgs&&... args)
	{
		get().log(LogLevel::Always, false, format, args...);
	}

	template <class ...TArgs>
	static void error(std::string format, TArgs&&... args)
	{
		get().log(LogLevel::Error, false, format, args...);
	}

	template <class ...TArgs>
	static void warning(std::string format, TArgs&&... args)
	{
		get().log(LogLevel::Warning, false, format, args...);
	}

	template <class ...TArgs>
	static void info(std::string format, TArgs&&... args)
	{
		get().log(LogLevel::Info, false, format, args...);
	}

	template <class ...TArgs>
	static void debug(std::string format, TArgs&&... args)
	{
		get().log(LogLevel::Debug, false, format, args...);
	}


	/// Set max log level to be printed
	void setLogLevel(LogLevel value);

	/// Initialize the logger, needs to be called only once.
	void startLogging();

	/// Call this before exiting the application, and not from static variable's dtor
	/// It will stop and join the logger thread.
	void stopLogging();

	/// Add a new log writer. There may be multuple writers, the logs
	/// will be sent to all of them.
	void addWriter(std::unique_ptr<LogWriter> logWriter);

	/// Get singleton instance of Logger.
	static Logger& get();


	/// Add message to the queue
	/// @param level Message level
	/// @param raw If true, do not add time and level info
	/// @param format Format string conforming to std::format specification
	/// @param args Format arguments
	template<class ...TArgs>
	void log(LogLevel level, bool raw, const std::string& format, TArgs&&... args) const
	{
		if (m_logLevel < level){
			return;
		}

		std::stringstream msg;
		
		if (!raw) {
			msg << formatTime() << " ";
			if (level != LogLevel::Always) {
				msg << "[" << levelName(level) << "] ";
			}
		}
		
		msg << std::vformat(format, std::make_format_args(args...)) << std::endl;

		std::scoped_lock lock(m_queueLock);
		m_queue.push_back(LogItem{level, msg.str()});
	}

	static const std::string& levelName(LogLevel logLevel);

private:
	/// Log queue processing function
	void run();

	/// Print contents of the queue
	void flush();

	static std::string formatTime();

private:
	std::atomic<LogLevel> m_logLevel = LogLevel::Error;  /// Current max log level to be shown.
	std::atomic<int>      m_isRunning = false;           /// Run flag.
	
	Writers m_writers;						/// A list of log writers
	mutable LogQueue m_queue;               /// Log message queue.
	mutable std::mutex m_queueLock;         /// Queue guard.
	mutable std::mutex m_writersLock;       /// Writers list guard.
	std::jthread m_logThread;               /// Logger thread.
};



class ConsoleLogger : public LogWriter{
public:
	void write(LogLevel level, const std::string& msg) override;
};


class FileLogger : public LogWriter{
public:
	explicit FileLogger(const std::string& filePath);

	void write(Logger::LogLevel level, const std::string& msg) override;
	bool good() const;

private:
	platform::FileWriter  m_file;
};




