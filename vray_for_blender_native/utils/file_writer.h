#pragma once

#ifdef _WIN32 // Windows
	#include <Windows.h>
	#include <streambuf>

namespace platform
{


/// Exclusive write access to files is implemented differently in Windows and POSIX.
/// FileWriter is a Windows implememtation of exclusive write, shared read mode writer.
/// The capabilities are intentionally kept to a minimum in order to minimize 
/// the amount of platform-specific code.
class FileWriter {
public:
	explicit FileWriter(const std::string& filename);
	~FileWriter();

	int  write(const std::string& data);
	bool good() const;
	
private:
	HANDLE m_file = nullptr;
};

FileWriter& operator << (FileWriter& stream, const std::string& data);

} // end namespace


#endif