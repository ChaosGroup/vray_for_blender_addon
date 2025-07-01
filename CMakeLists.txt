cmake_minimum_required(VERSION 3.21 FATAL_ERROR)	# For PROJECT_IS_TOP_LEVEL variable

project(VRayForBlender)

set(CMAKE_CXX_STANDARD 20)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(common_macros)

set_build_configurations()

set(BLENDER_VER    "" CACHE STRING  "Blender version in format major.minor, e.g. 4.2")
set(ADDON_PATH     "" CACHE PATH    "Path to the Blender addon")
set(BINTOOLS_PATH  "" CACHE STRING  "Path to cgrepo (parent of bintools)")
set(INSTALL_LOCAL  ON CACHE BOOL   	"Configure Install step")

fix_separators(ADDON_PATH)

if(NOT ${BLENDER_VER})
	message(FATAL_ERROR "BLENDER_VER not defined")
endif()

if (${INSTALL_LOCAL})
	if(NOT EXISTS ${ADDON_PATH})
		message(FATAL_ERROR "Folder referenced by ADDON_PATH(${ADDON_PATH}) is missing, but is required by option INSTALL_LOCAL")
	endif()
	if(NOT EXISTS "${ADDON_PATH}/bin")
		file(MAKE_DIRECTORY "${ADDON_PATH}/bin")
	endif()
endif()

set (INSTALL_PREFIX "${ADDON_PATH}/bin")


####################################################
# Open-source components

# VRayZmqWrapper
add_subdirectory(vray_for_blender_zmq_wrapper)


# Configure the path to VRayZmqWrapper to be consumed by the Lib and Server projects
set(VRAY_ZMQ_WRAPPER_INCLUDE        "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_zmq_wrapper" CACHE PATH "Path to V-Ray ZMQ wrapper headers")
set(VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_zmq_wrapper/extern" CACHE PATH "Path to ZMQ headers")
set(VRAY_ZMQ_WRAPPER_LIBS           "$<TARGET_FILE_DIR:VRayZmqWrapper>" CACHE PATH "Path to ZMQ libs")

# VRayBlenderLib
add_subdirectory(vray_for_blender_native)
add_dependencies(VRayBlenderLib VRayZmqWrapper)


####################################################
# Closed-source components

# VRayZmqServer
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_server")
	add_subdirectory(vray_for_blender_server)
	add_dependencies(VRayZmqServer VRayZmqWrapper)
else()
	message(STATUS "Skipping project generation for VRayZmqServer")
endif()

# VRayTools
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_tools")
	add_subdirectory(vray_for_blender_tools)
else()
	message(STATUS "Skipping project generation for VRayTools")
endif()

if(INSTALL_LOCAL)
	install(DIRECTORY "vray_for_blender_python_exporter/" DESTINATION "${ADDON_PATH}")
endif()

