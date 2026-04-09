# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.25 FATAL_ERROR)

project(VRayForBlender)

set(CMAKE_CXX_STANDARD 20)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(common_macros)

set_build_configurations()


set(BLENDER_VER         ""  CACHE STRING  "Blender version in format major.minor, e.g. 4.2")

set(_default_addon_path "")
if (WIN32)
	set(_default_addon_path "$ENV{APPDATA}/Blender Foundation/Blender/${BLENDER_VER}/scripts/addons/vray_blender")
elseif(APPLE)
	set(_default_addon_path "$ENV{HOME}/Library/Application Support/Blender/${BLENDER_VER}/scripts/addons/vray_blender")
else()
	set(_default_addon_path "$ENV{HOME}/.config/blender/${BLENDER_VER}/scripts/addons/vray_blender")
endif()
set(ADDON_PATH          "${_default_addon_path}" CACHE PATH "Path to the Blender addon. If set, the INSTALL target will use it as its destination.")

set(BINTOOLS_PATH       ""  CACHE STRING  "Path to cgrepo (parent of bintools). If not set, xpaks won't be used.")
set(INSTALL_LOCAL       ON  CACHE BOOL    "Configure Install step")
set(WITH_DR2            ON  CACHE BOOL    "Enable DR2")
set(CUSTOM_APPSDK_ROOT  ""  CACHE PATH    "Root directory of the App SDK (optional)")
set(WITH_PROFILING      "None" CACHE STRING "Profiling features to enable")
set(BUILD_MOD           "ADV" CACHE STRING "Build modification (ADV, CE)")
set(WITH_OSL            OFF CACHE BOOL    "Enables OSL materials")
set(WITH_DYNAMIC_DEOPT  OFF CACHE BOOL    "Enable MSVS /dynamicdeopt")
set(NANOBIND_LIBDIR     ""  CACHE STRING  "Path to Nanobind library")
set_property(CACHE BUILD_MOD PROPERTY STRINGS ADV CE)
set_property(CACHE WITH_PROFILING PROPERTY STRINGS None Profiling Messages All)

if (BUILD_MOD STREQUAL "CE")
    add_compile_definitions(VRAY_BLENDER_COMMUNITY_EDITION)
endif()

if (MSVC)
    add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN NOGDI _CRT_SECURE_NO_WARNINGS)
    set(CMAKE_MSVC_DEBUG_INFORMATION_FORMAT ProgramDatabase)
    if (WITH_DYNAMIC_DEOPT)
        add_compile_options($<$<CONFIG:Release>:/dynamicdeopt>)
        add_link_options($<$<CONFIG:Release>:/dynamicdeopt>)
        add_link_options("$<$<AND:$<CXX_COMPILER_ID:MSVC>,$<CONFIG:Release>>:/OPT:NOICF>")
        set(CMAKE_STATIC_LINKER_FLAGS_RELEASE "${CMAKE_STATIC_LINKER_FLAGS_RELEASE} /dynamicdeopt")
        set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASE OFF)
    endif()
endif()

# A convenience alias. If bintools path is not set, don't build closed-source components
if (BINTOOLS_PATH)
    set(FULL_BUILD ON)
    message(STATUS "Full build configuration selected (addon and ZmqServer)")
    get_filename_component(BINTOOLS_PATH ${BINTOOLS_PATH} ABSOLUTE)
else()
    message(STATUS "Partial build configuration selected (addon only)")
    set(FULL_BUILD OFF)
endif()

fix_separators(ADDON_PATH)
if (NOT "${ADDON_PATH}" STREQUAL "" AND NOT EXISTS ${ADDON_PATH})
    file(MAKE_DIRECTORY ${ADDON_PATH})
endif()

if (APPLE)
    set(CMAKE_OSX_DEPLOYMENT_TARGET  "11.2")
endif()

if(UNIX)
    add_compile_options(-Wno-switch -Wno-macro-redefined -Wno-ambiguous-reversed-operator)

    if (NOT APPLE)
        add_link_options(-Wl,--gc-sections -Wl,--discard-all -Wl,--no-as-needed -fuse-ld=lld)
    endif()
endif()

if (WITH_DR2)
    add_compile_definitions(WITH_DR2)
endif()

if (WITH_PROFILING STREQUAL "Profiling")
    add_compile_definitions(WITH_PROFILING)
elseif (WITH_PROFILING STREQUAL "Messages")
    add_compile_definitions(WITH_MESSAGE_PROFILING)
elseif (WITH_PROFILING STREQUAL "All")
    add_compile_definitions(WITH_PROFILING)
    add_compile_definitions(WITH_MESSAGE_PROFILING)
endif()

if (MSVC AND MSVC_VERSION GREATER_EQUAL 1940)
    add_compile_definitions(_DISABLE_CONSTEXPR_MUTEX_CONSTRUCTOR)
endif()

if(NOT ${BLENDER_VER})
    message(FATAL_ERROR "BLENDER_VER not defined")
endif()

string(REPLACE "." ";" BLENDER_VER_LIST ${BLENDER_VER})
list(GET BLENDER_VER_LIST 0 BLENDER_VER_MAJOR)
list(GET BLENDER_VER_LIST 1 BLENDER_VER_MINOR)

math(EXPR BLENDER_VERSION_NUM "${BLENDER_VER_MAJOR} * 100 + ${BLENDER_VER_MINOR} * 10")

add_compile_definitions(
    BLENDER_VERSION=${BLENDER_VERSION_NUM}
)

if (INSTALL_LOCAL AND ADDON_PATH)
    if(NOT EXISTS ${ADDON_PATH})
        message(FATAL_ERROR "Folder referenced by ADDON_PATH(${ADDON_PATH}) is missing.")
    endif()
    if(NOT EXISTS "${ADDON_PATH}/bin")
        file(MAKE_DIRECTORY "${ADDON_PATH}/bin")
    endif()
endif()

set (INSTALL_PREFIX "${ADDON_PATH}/bin")
fix_separators(INSTALL_PREFIX)

fix_separators(VRAY_LIB_INSTALL_PREFIX)
fix_separators(VRAY_ZMQ_SERVER_INSTALL_PREFIX)
fix_separators(VRAY_TOOLS_INSTALL_PREFIX)

if (FULL_BUILD)
    include(xpak)
    include(xpak_versions)

    xpak_work_dir()

    install_xpak(PAK ${BLENDER_SDK_XPAK} VERSION ${BLENDER_SDK_XPAK_VERSION})
    set(SDK_ROOT     "${${BLENDER_SDK_XPAK}_ROOT}")

    if (CUSTOM_APPSDK_ROOT)
        set(APPSDK_ROOT ${CUSTOM_APPSDK_ROOT})
    else()
        set(APPSDK_XPAK "Blender_AppSDK_7")

        install_xpak(PAK ${APPSDK_XPAK} VERSION ${APPSDK_XPAK_VERSION})
        set(APPSDK_ROOT "${${APPSDK_XPAK}_ROOT}")
    endif()

	if ("${NANOBIND_LIBDIR}" STREQUAL "")
	    set(NANOBIND_LIBDIR ${CMAKE_CURRENT_SOURCE_DIR}/xsdk/nanobind)
	endif()

	fix_separators(APPSDK_ROOT)
    fix_separators(SDK_ROOT)
endif()

# Configure the path to VRayZmqWrapper to be consumed by the Lib and Server projects
set(VRAY_ZMQ_WRAPPER_ROOT  "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_zmq_wrapper" CACHE PATH "Path to V-Ray ZMQ wrapper headers")
set(VRAY_ZMQ_WRAPPER_LIBS  "$<TARGET_FILE_DIR:VRayZmqWrapper>" CACHE PATH "Path to ZMQ libs")

fix_separators(VRAY_ZMQ_WRAPPER_ROOT)
message(STATUS "NANOBIND: ${NANOBIND_LIBDIR}")

if("${NANOBIND_LIBDIR}" STREQUAL "")
    message(FATAL_ERROR "NANOBIND_LIBDIR is empty")
endif()

fix_separators(NANOBIND_LIBDIR)

# robin_map - header-only hash map/set used across all C++ projects
add_library(tsl::robin_map INTERFACE IMPORTED)
set_target_properties(tsl::robin_map PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${NANOBIND_LIBDIR}/ext/robin_map/include"
)

####################################################
# Open-source components

# VRayZmqWrapper
add_subdirectory(vray_for_blender_zmq_wrapper)

# VRayBlenderLib
add_subdirectory(vray_for_blender_native)

####################################################
# Closed-source components

if (FULL_BUILD)
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

endif()

if(INSTALL_LOCAL AND ADDON_PATH)
    install(DIRECTORY "vray_for_blender_python_exporter/" DESTINATION "${ADDON_PATH}")
endif()

if (FULL_BUILD)
    if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_installator_generator)
        add_subdirectory(vray_for_blender_installator_generator)
    else()
        message(STATUS "Skipping generation installation scripts")
    endif()
endif()
