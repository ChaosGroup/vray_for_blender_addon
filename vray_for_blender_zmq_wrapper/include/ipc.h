// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once


#include <chrono>
#include <memory>
#include <string>
#include <vector>

#ifdef _WIN32
#define USE_WIN_SHARED_MEM
#endif

#ifdef USE_WIN_SHARED_MEM
#include <boost/interprocess/windows_shared_memory.hpp>
#else
#include <boost/interprocess/shared_memory_object.hpp>
#endif
#include <boost/interprocess/mapped_region.hpp>
#include <boost/interprocess/sync/named_mutex.hpp>


////////////////////////////////////////////////////////////////////////////////
/// SharedMemory suite of classes provides a producer-consumer pair of processes
/// synchronized access to a shared memory region.
/// The memory is allocated with a fixed payload size. Read and write operations
/// always operate on the whole payload.
////////////////////////////////////////////////////////////////////////////////

namespace VrayZmqWrapper{


class SharedMemoryBase {
public:
	using SizeType		 = uint32_t;

protected:
#ifdef USE_WIN_SHARED_MEM
	using SharedMem      = boost::interprocess::windows_shared_memory;
#else
	using SharedMem      = boost::interprocess::shared_memory_object;
#endif
	using MappedRegion   = boost::interprocess::mapped_region;
	using NamedLock      = boost::interprocess::named_mutex;
	using SharedMemPtr   = std::unique_ptr<SharedMem>;
	using MappedRegionPtr= std::unique_ptr<MappedRegion>;
	using NamedLockPtr   = std::unique_ptr<NamedLock>;


	// Representation of the memory layout of the mapped region.
	struct alignas(SizeType) Payload {
		SizeType size;		// The size of the payload data
		char	 data[1];	// The payload data
	};

public:
	bool isValid() const;

	/// Get the description of the last error that has occurred.
	/// This method is NOT thread safe and can only be called from the
	/// thread on which the last operation has been invoked.
	std::string getLastError() const;

	size_t getPayloadSize() const;

protected:
	SharedMemoryBase(const std::string& id, const std::string& name);
	virtual ~SharedMemoryBase() = default;

	/// Create a name which is globally unique for the owned memory mapping.
	std::string createUniqueName(const std::string& objName) const;

	/// Get a friendly view of the mapped region.
	Payload& getPayload() const;

protected:
	std::string m_id;	      ///< Unique ID of the group of communicating processes
	std::string m_name;	      ///< The name of the shared memory block
	std::string m_lastError;  ///< A description of the last error that has occurred

	SharedMemPtr m_shm;       ///< The shared memory object
	MappedRegionPtr m_region; ///< Mempry region mapping into the current process
	NamedLockPtr m_lock;      ///< A named waitable lock for syncronizing access from the processes in the group
};



/// The producer of shared memory producer-consumer pair.
class SharedMemoryWriter : public SharedMemoryBase
{
public:
	// A helper struct for scatter/gather writes
	struct Buffer {
		const void*  data   = nullptr;
		const size_t size   = 0;
	};

public:
	SharedMemoryWriter(const std::string& id, const std::string& name);
	~SharedMemoryWriter();

	/// Try to create the shared memory region with write access
	/// @param size - the size of the payload data
	/// @param initialData - payload data which will be written at region creation
	/// @return false if creation failed
	bool create(SizeType size, const void* initialData = nullptr);

	/// Write to the shared memory region.
	/// This function will block until the lock on the region is acquired.
	/// @param data    - start address of a memory block from which to copy the data. The length
	///                  of the data is equal to the size of the shared region.
	void write(const void* data);

	/// Scatter/gather writer. Writes all buffers as an atomic operation.
	/// @param buffers - scattered buffers
	void write(const std::vector<Buffer> buffers);

	/// Remove an existing shared file, used on Unix systems for clean-up between process restarts.
	/// @param id The id of the mapped file.
	/// @param name The base name of the mapped file.
	static void remove(const std::string& id, const std::string& name);

private:
	/// Internal implementation which relies on the caller to provide synchronization.
	void writeImpl(const void* data);

};



/// The consumer of shared memory producer-consumer pair.
class SharedMemoryReader : public SharedMemoryBase
{
public:
	SharedMemoryReader(const std::string& id, const std::string& name);

	/// Try to open the shared memory region for reading.
	/// @param timeout - abort operation if not complete within this interval
	/// @return false if creation failed
	bool open  (std::chrono::milliseconds timeout);

	/// Try to read from the shared memory region.
	/// @param timeout - abort operation if the lock could not be acquired within this interval
	/// @param data    - start address of a memory block to copy the whole shared region to
	/// @returns true if the red is successful, false if the lock could not be acquired
	bool read(std::chrono::milliseconds timeout, void* data);
};



// A specialized implementation for reading image data that helps handle changing buffer sizes.
class ImageReader : public SharedMemoryReader
{
public:
	struct ImageFormat {
		int width  = 0;
		int height = 0;

		/// Get the image size in pixels
		/// @return image size in pixels
		size_t imageSize() const;
	};

	struct ImageBuffer : ImageFormat{
		void* data = nullptr;

		/// Check if image data has been set to the buffer.
		/// @return true if data has been set
		bool hasData() const;
	};

private:
	// Memory layout of the data buffer
	struct ImageLayout : ImageFormat{
		char  data[1] = {0};
	};

	friend struct ImageLayout;

public:
	ImageReader(const std::string& id, const std::string& name);

	/// Read image data into a buffer. If the format of the supplied buffer is the same
	/// as the format of the image, the image data will be copied into the existing buffer.
	/// Otherwise, a new buffer will be allocated to fit the new size requirements.
	//
	/// @param timeout - abort operation if the lock could not be acquired within this interval
	/// @param buffer  - a valid buffer to hold the image data.
	/// @return - an initialized buffer on success, uninitailized on failure
	ImageBuffer read(std::chrono::milliseconds timeout, const ImageBuffer& buffer);

};

};  // end VrayZmqWrapper namespace
