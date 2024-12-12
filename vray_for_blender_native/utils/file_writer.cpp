#include "file_writer.h"

#ifdef _WIN32

namespace platform
{

FileWriter::FileWriter(const std::string& filename) :
	m_file(CreateFile(filename.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr,
						CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr ))
{
}

FileWriter::~FileWriter() {

	if (m_file != INVALID_HANDLE_VALUE) {
		CloseHandle(m_file);
	}
}

int FileWriter::write(const std::string& data) {
	
	if (m_file != INVALID_HANDLE_VALUE) {
		DWORD written = 0;

		if (WriteFile(m_file, data.data(), static_cast<DWORD>(data.size()), &written, nullptr)) {
			return written;
		}
	}
	
	return EOF;
}


bool FileWriter::good() const {
	return m_file != INVALID_HANDLE_VALUE;
}


FileWriter& operator << (FileWriter& stream, const std::string& data) {
	stream.write(data);
	return stream;
}

} // end namespace

#endif // WIN32