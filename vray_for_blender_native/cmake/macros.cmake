#
# Copyright (c) 2015-2023, Chaos Software Ltd
#
# V-Ray Application SDK For C++
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


if(WIN32)
	SET(CMAKE_FIND_LIBRARY_SUFFIXES ".lib" ".dll")
endif()

macro(use_zmq)
	if (NOT EXISTS ${ZMQ_ROOT})
		message(FATAL_ERROR "Could not find ZMQ: \"${ZMQ_ROOT}\"")
	endif()

	if (WIN32)
		string(REPLACE "/MT" "/MD" CMAKE_CXX_FLAGS_RELEASE ${CMAKE_CXX_FLAGS_RELEASE})
		string(REPLACE "/MT" "/MD" CMAKE_CXX_FLAGS_RELWITHDEBINFO ${CMAKE_CXX_FLAGS_RELWITHDEBINFO})
	else()
		set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++0x -L/usr/lib64")
		link_directories(/usr/lib64)
	endif()

	add_definitions(-DZMQ_STATIC)

	link_directories(${ZMQ_ROOT}/lib)
	include_directories(${ZMQ_ROOT}/include)
endmacro()


macro(link_with_zmq _name)
	if(UNIX)
		target_link_libraries(${PROJECT_NAME} PRIVATE
			${LIBS_ROOT}/${CMAKE_SYSTEM_NAME}/zmq/lib/Release/libzmq.a
			${LIBS_ROOT}/${CMAKE_SYSTEM_NAME}/sodium/lib/Release/libsodium.a
			)
	elseif(WIN32)
		if(MSVC_VERSION GREATER_EQUAL 1900)
			set(MSVC_DIR_NAME "v142")
		endif()
		target_link_libraries(${_name} PRIVATE optimized Release/${MSVC_DIR_NAME}/libzmq-v142-mt-s-4_3_4)
		target_link_libraries(${_name} PRIVATE wsock32 ws2_32 Iphlpapi)
	endif()
endmacro()


if (WIN32)
	# Generate folder structure for Visual Studio
	macro(GroupSources curdir ignore_prefix)
		file(GLOB children RELATIVE ${PROJECT_SOURCE_DIR}/${curdir} ${PROJECT_SOURCE_DIR}/${curdir}/*)
		foreach(child ${children})
			if(IS_DIRECTORY ${PROJECT_SOURCE_DIR}/${curdir}/${child})
				if (${child} STREQUAL ".git")
					# pass
				else()
					GroupSources(${curdir}/${child} ${ignore_prefix})
				endif()
			else()
				string(REPLACE "${ignore_prefix}/" "" rel_path ${curdir})
				string(REPLACE "/" "\\" groupname ${rel_path})
				source_group(${groupname} FILES ${PROJECT_SOURCE_DIR}/${curdir}/${child})
			endif()
		endforeach()
	endmacro()
	GroupSources(api .)
	GroupSources(export .)
	GroupSources(utils .)
endif()
