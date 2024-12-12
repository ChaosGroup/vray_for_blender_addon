#
# Copyright (c) 2015, Chaos Software Ltd
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

macro(use_qt _qt_root)
	if (NOT EXISTS ${_qt_root})
		message(FATAL_ERROR "Could not find QT: \"${_qt_root}\"")
	endif()

	set(QT_INCLUDES
		${QT_ROOT}/include
		${QT_ROOT}/include/QtCore
		${QT_ROOT}/include/QtWidgets
		${QT_ROOT}/include/QtGui
	)

	if (WIN32)
		set(QT_LIBPATH ${QT_ROOT}/lib/x64/vc14)
		set(QT_LIB_EXT ".lib")
		set(QT_LIB_PREFIX "")
	else()
		if (APPLE)
			set(QT_LIBPATH ${QT_ROOT}/lib)
			set(QT_LIB_EXT ".dylib")
			set(QT_LIB_PREFIX "lib")
		else()
			set(QT_LIBPATH ${QT_ROOT}/lib)
			set(QT_LIB_EXT ".so")
			set(QT_LIB_PREFIX "lib")
		endif()
	endif()

	include_directories(${QT_INCLUDES})
	link_directories(${QT_LIBPATH})
endmacro()


macro(link_with_qt)
	if (UNIX AND NOT APPLE)
		# target_link_libraries(${PROJECT_NAME} libicui18n.so)
		# target_link_libraries(${PROJECT_NAME} libicuuc.so)
		# target_link_libraries(${PROJECT_NAME} libicudata.so)
	endif()

	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6Core${QT_LIB_EXT})
	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6Gui${QT_LIB_EXT})
	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6Widgets${QT_LIB_EXT})
endmacro()


macro(use_zmq)
	if (NOT EXISTS ${ZMQ_ROOT})
		message(FATAL_ERROR "Could not find ZMQ: \"${ZMQ_ROOT}\"")
	endif()

	if (WIN32)
		#string(REPLACE "/MDd" "/MTd" CMAKE_CXX_FLAGS_DEBUG ${CMAKE_CXX_FLAGS_DEBUG})
		#string(REPLACE "/MD" "/MT" CMAKE_CXX_FLAGS_RELEASE ${CMAKE_CXX_FLAGS_RELEASE})
		#string(REPLACE "/MD" "/MT" CMAKE_CXX_FLAGS_RELWITHDEBINFO ${CMAKE_CXX_FLAGS_RELWITHDEBINFO})

		string(REPLACE "/MTd" "/MDd" CMAKE_CXX_FLAGS_DEBUG ${CMAKE_CXX_FLAGS_DEBUG})
		string(REPLACE "/MT" "/MD" CMAKE_CXX_FLAGS_RELEASE ${CMAKE_CXX_FLAGS_RELEASE})
		string(REPLACE "/MT" "/MD" CMAKE_CXX_FLAGS_RELWITHDEBINFO ${CMAKE_CXX_FLAGS_RELWITHDEBINFO})
	else()
		set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++0x -L/usr/lib64")
		link_directories(/usr/lib64)
	endif()

	add_definitions(-DZMQ_STATIC)

	link_directories(${ZMQ_ROOT}/lib)
	#link_directories(${ZMQ_ROOT}/bin)
	include_directories(${ZMQ_ROOT}/include)
endmacro()


macro(link_with_zmq _name)
	if(UNIX)
		target_link_libraries(${PROJECT_NAME}
			${LIBS_ROOT}/${CMAKE_SYSTEM_NAME}/zmq/lib/Release/libzmq.a
			${LIBS_ROOT}/${CMAKE_SYSTEM_NAME}/sodium/lib/Release/libsodium.a
			)
	elseif(WIN32)
		if (MSVC_VERSION EQUAL 1800)
			set(MSVC_DIR_NAME "v120")
		elseif(MSVC_VERSION GREATER_EQUAL 1900)
			set(MSVC_DIR_NAME "v142")
		endif()
		#target_link_libraries(${_name} debug Debug/v140/static/libzmq)
		target_link_libraries(${_name} optimized Release/${MSVC_DIR_NAME}/libzmq-v142-mt-s-4_3_4)
		target_link_libraries(${_name} wsock32 ws2_32 Iphlpapi)
	endif()
endmacro()


macro(use_vray_appsdk _appsdk_root)
	if(NOT EXISTS ${_appsdk_root})
		message(FATAL_ERROR "V-Ray AppSDK root (\"${_appsdk_root}\") doesn't exist!")
	endif()

	find_library(VRAY_APPSDK_LIB
		NAMES VRaySDKLibrary
		PATHS ${_appsdk_root}/bin ${_appsdk_root}/lib
	)

	if(NOT VRAY_APPSDK_LIB)
		message(FATAL_ERROR "V-Ray AppSDK libraries are not found! Check APPSDK_PATH variable (current search path ${_appsdk_root}/bin)")
	else()
		message(STATUS "V-Ray AppSDK path: ${_appsdk_root}")
	endif()

	add_definitions(-DVRAY_SDK_INTEROPERABILITY)

	include_directories(${_appsdk_root}/cpp/include)
	link_directories(${_appsdk_root}/bin)
	link_directories(${_appsdk_root}/cpp/lib)
endmacro()


macro(link_with_vray_appsdk _name)
	set(APPSDK_LIBS
		VRaySDKLibrary
	)
	target_link_libraries(${_name} ${APPSDK_LIBS})
endmacro()
