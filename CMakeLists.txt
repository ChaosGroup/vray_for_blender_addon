cmake_minimum_required(VERSION 3.10)

project(VRayForBlender)

set (LIBDIR "${CMAKE_CURRENT_SOURCE_DIR}/win64_vc15" CACHE PATH "Path to platform libs")
set (ZMQ_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_libs/${CMAKE_SYSTEM_NAME}/zmq" CACHE PATH "Path to ZMQ libs")
set (ADDON_PATH "" CACHE PATH "Path to the blender addon")
set (INSTALL_LOCAL ON CACHE BOOL   "Do Install step")

string(REPLACE "\\" "/" ZMQ_ROOT ${ZMQ_ROOT})
string(REPLACE "\\" "/" LIBDIR ${LIBDIR})
string(REPLACE "\\" "/" ADDON_PATH ${ADDON_PATH})

if (${INSTALL_LOCAL})
	if(NOT EXISTS ${ADDON_PATH})
		message(FATAL_ERROR "Folder referenced by ADDON_PATH(${ADDON_PATH}) is missing, but is required by option INSTALL_LOCAL")
	endif()
	if(NOT EXISTS "${ADDON_PATH}/bin")
		file(MAKE_DIRECTORY "${ADDON_PATH}/bin")
	endif()
endif()

set(CMAKE_CXX_STANDARD 20)

add_subdirectory(vray_for_blender_zmq_wrapper)

set (INSTALL_PREFIX "${ADDON_PATH}/bin")

set(VRAY_ZMQ_WRAPPER_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_zmq_wrapper" CACHE PATH "Path to V-Ray ZMQ wrapper headers")
set(VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_zmq_wrapper/extern" CACHE PATH "Path to ZMQ headers")
set(VRAY_ZMQ_WRAPPER_LIBS "$<TARGET_FILE_DIR:VRayZmqWrapper>" CACHE PATH "Path to ZMQ libs")

add_subdirectory(vray_for_blender_native)
add_dependencies(VRayBlenderLib VRayZmqWrapper)

# Building the closed-source component of the plugin responsible for rendering.
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_server")
	set (LIBS_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_libs" CACHE PATH "Path to V-Ray for blender libs")
	string(REPLACE "\\" "/" LIBS_ROOT ${LIBS_ROOT})
	set (VRAY_ZMQ_SERVER_INSTALL_PREFIX "${ADDON_PATH}/bin")

	add_subdirectory(vray_for_blender_server)
	add_dependencies(VRayZmqServer VRayZmqWrapper)
else()
	message(STATUS "Skipping project generation for VRayZmqServer")
endif()

if(INSTALL_LOCAL)
	install(DIRECTORY "vray_for_blender_python_exporter/" DESTINATION "${ADDON_PATH}")
endif()
