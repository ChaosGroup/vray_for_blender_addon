#pragma once


#include "ipc.h"
#include <thread>
#include <chrono>

#include "boost/date_time/posix_time/posix_time.hpp"
#include <boost/interprocess/sync/scoped_lock.hpp>

namespace ipc = boost::interprocess;

namespace VrayZmqWrapper{

const std::string MAPPING_BASE_NAME = "data_block";


SharedMemoryBase::SharedMemoryBase(const std::string& id, const std::string& name) :
	m_id (id),
	m_name(name),
	m_lock(std::make_unique<NamedLock>(ipc::open_or_create, createUniqueName("lock").c_str()))
{
	// After creation, the mutex is in locked state. Unlock to allow operation.
	m_lock->unlock();
}
	

std::string SharedMemoryBase::getLastError() const{
	return m_lastError;	
}


size_t SharedMemoryBase::getPayloadSize() const {
	ipc::scoped_lock lock(*m_lock);
	return getPayload().size;
}


SharedMemoryBase::Payload& SharedMemoryBase::getPayload() const {
	return *static_cast<Payload*>(m_region->get_address());
}


std::string SharedMemoryBase::createUniqueName(const std::string& objName) const {
	return std::string("vray-zmq-") + m_id + "-" + m_name + "-" + objName;
}




////////////////////////////////////////////
// SharedMemoryWriter
////////////////////////////////////////////

SharedMemoryWriter::SharedMemoryWriter(const std::string& id, const std::string& name) :
	SharedMemoryBase(id, name)
{
}


bool SharedMemoryWriter::create(SizeType size, const void* initialData /* =nullptr */) {

	SharedMemPtr    shm;
	MappedRegionPtr region;

	// Acquire the lock so that the creation of the mapping and the initialization of the 
	// payload data could be seen as atomic by the reader. 
	ipc::scoped_lock lock(*m_lock);
	
	
	try {
		const SizeType blockSize = size + sizeof(SizeType);
		const std::string mappingName = createUniqueName(MAPPING_BASE_NAME);

		shm    = std::make_unique<SharedMem>(ipc::create_only, mappingName.c_str(), ipc::read_write, blockSize);
		region = std::make_unique<MappedRegion>(*shm, ipc::read_write);
	}
	catch (const ipc::interprocess_exception& e) {
		m_lastError = e.what();
		return false;
	}

	m_shm    = std::move(shm);
	m_region = std::move(region);

	// Initialize the payload
	getPayload().size = size;
	
	if (nullptr != initialData) {
		writeImpl(initialData);
	}

	return true;
}



void SharedMemoryWriter::write(const void* data) {
	// Wait until the lock becomes available. A reader should not hold the lock for extended periods.
	ipc::scoped_lock lock(*m_lock);
	writeImpl(data);
}


void SharedMemoryWriter::write(const std::vector<Buffer> buffers) {
	// Wait until the lock becomes available. A reader should not hold the lock for extended periods.
	ipc::scoped_lock lock(*m_lock);

	auto& block = getPayload();
	auto writePtr = block.data;
	
	for( const auto& buf : buffers) {
		::memcpy(writePtr, buf.data, buf.size);
		writePtr += buf.size;
	}
}


void SharedMemoryWriter::writeImpl(const void* data) {
	auto& block = getPayload();
	::memcpy(block.data, data, block.size);
}



////////////////////////////////////////////
// SharedMemoryReader
////////////////////////////////////////////


SharedMemoryReader::SharedMemoryReader(const std::string& id, const std::string& name) :
	SharedMemoryBase(id, name)
{
}


bool SharedMemoryReader::open(std::chrono::milliseconds timeout) {

	using namespace std::chrono_literals;
	using Clock = std::chrono::steady_clock;
	
	const auto deadline = Clock::now() + timeout;

	while(Clock::now() < deadline) {
		
		SharedMemPtr    shm;
		MappedRegionPtr region;

		try {
			const std::string mappingName = createUniqueName(MAPPING_BASE_NAME);

			shm    = std::make_unique<SharedMem>(ipc::open_only, mappingName.c_str(), ipc::read_only);
			region = std::make_unique<MappedRegion>(*shm, ipc::read_only);
		}
		catch(const ipc::interprocess_exception& e) {
			// The shared memory still not created. Try again in a while.
			std::this_thread::sleep_for(1ms);
			m_lastError = e.what(); 
			continue;
		}
	
		m_shm    = std::move(shm);
		m_region = std::move(region);
		
		return true;
	}

	return false;
}


bool SharedMemoryReader::read(std::chrono::milliseconds timeout, void* data) {
	namespace pt = boost::posix_time;

	const auto deadline = pt::second_clock::universal_time() + pt::milliseconds(timeout.count());

	ipc::scoped_lock lock(*m_lock, deadline);

	if (lock) {
		auto& block  = getPayload();
		::memcpy(data, block.data, block.size);
		return true;
	}

	return false;
}



////////////////////////////////////////////
// ImageReader
////////////////////////////////////////////

size_t ImageReader::ImageFormat::imageSize() const {
	return width * height * sizeof(float) * 4;
}


bool ImageReader::ImageBuffer::hasData() const {
	return (width != 0) && (height != 0) && (data != nullptr);
}


ImageReader::ImageReader(const std::string& id, const std::string& name) :
	SharedMemoryReader(id, name)
{
}


ImageReader::ImageBuffer ImageReader::read(std::chrono::milliseconds timeout, const ImageBuffer& buffer) {
	namespace pt = boost::posix_time;

	const auto deadline = pt::second_clock::universal_time() + pt::milliseconds(timeout.count());

	ipc::scoped_lock lock(*m_lock, deadline);

	if (lock) {
		auto& block  = getPayload();
		const ImageLayout& img = *reinterpret_cast<ImageLayout*>(block.data);

		// NOTE: The shared buffer may be larger that the actual data
		const size_t size = img.imageSize();

		if ((img.width == buffer.width) && (img.height == buffer.height)) {
			// The image data has the same dimensions as the buffer, copy to the buffer.
			::memcpy(buffer.data, img.data, size);
			return buffer;
		}

		// The image data format differs from that of the buffer. Allocate a new buffer.
		auto data = std::make_unique<char[]>(size);
		::memcpy(data.get(), img.data, size);
		
		return ImageBuffer{img.width, img.height, data.release()};
	}

	return ImageBuffer();
}

};  // end VrayZmqWrapper namespace 