// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <catch_amalgamated.hpp>

#include <chrono>
#include <future>
#include <iostream>
#include <thread>
#include <mutex>


#include "ipc.h"
#include "tools.hpp"

using namespace std::chrono_literals;
using namespace VrayZmqWrapper;

namespace {
}


#include <boost/interprocess/windows_shared_memory.hpp>
#include <boost/interprocess/mapped_region.hpp>

namespace ipc = boost::interprocess;

TEST_CASE("IPC")
{
	SECTION("Successful write-read") {

		const int value = 5;
		int result = 0;
		
		auto threadWriter = std::thread([value]() {
			SharedMemoryWriter writer("id", "name");

			writer.create(sizeof(int), &value);
			
			// Make sure the shared region stays alive for the reader to access it
			std::this_thread::sleep_for(2s);
		});

		
		auto threadReader = std::thread([&result]() {
			SharedMemoryReader reader("id", "name");
			if (reader.open(1s)) {
				reader.read(1s, &result);
			}
		});

		threadWriter.join();
		threadReader.join();
		REQUIRE(value == result);
	}

	
	SECTION("Fail to create reader without a writer") {
		SharedMemoryReader reader("id", "name");
		REQUIRE(!reader.open(1s));
		REQUIRE(!reader.getLastError().empty());
	}
	
}

