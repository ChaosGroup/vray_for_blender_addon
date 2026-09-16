# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.25 FATAL_ERROR)

project(VRayForBlender)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_INSTALL_MESSAGE LAZY)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(common_macros)

set_build_configurations()

set(BLENDER_VER         ""  CACHE STRING  "Blender version in format major.minor, e.g. 5.1")
if("${BLENDER_VER}" STREQUAL "")
    message(FATAL_ERROR "BLENDER_VER not defined - please set in format 'major.minor', e.g. 5.1")
endif()

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
set(INSTALL_AUTOMATIC   ON  CACHE BOOL    "Include the INSTALL target in the MSVS default build")
set(CUSTOM_APPSDK_ROOT  ""  CACHE PATH    "Root directory of the App SDK (optional)")
set(WITH_PROFILING      "None" CACHE STRING "Profiling features to enable")
set(WITH_OSL            OFF CACHE BOOL    "Enables OSL materials")
set(WITH_COSMOS         ON  CACHE BOOL    "Build the Chaos Cosmos browser and importer into VRayZmqServer")
set(WITH_VERAS          ON  CACHE BOOL    "Build the Chaos Veras upload window into VRayZmqServer")
set(WITH_CHAOS_SCATTER  ON  CACHE BOOL    "Install the Chaos Scatter add-on beside vray_blender")
set(WITH_DYNAMIC_DEOPT  OFF CACHE BOOL    "Enable MSVS /dynamicdeopt")
set(NANOBIND_LIBDIR     ""  CACHE STRING  "Path to Nanobind library")
set_property(CACHE WITH_PROFILING PROPERTY STRINGS None Profiling Messages All)

if (MSVC)
    add_compile_definitions(NOMINMAX WIN32_LEAN_AND_MEAN NOGDI _CRT_SECURE_NO_WARNINGS)
    set(CMAKE_MSVC_DEBUG_INFORMATION_FORMAT ProgramDatabase)
    if (INSTALL_AUTOMATIC)
        # Build Solution runs INSTALL too, instead of it having to be built separately.
        set(CMAKE_VS_INCLUDE_INSTALL_TO_DEFAULT_BUILD ON)
    endif()
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

# Fallback destination for components that got no explicit VRAY_*_INSTALL_PREFIX.
# Guarded, or an empty ADDON_PATH would turn it into the literal "/bin".
if (ADDON_PATH)
    set (INSTALL_PREFIX "${ADDON_PATH}/bin")
    fix_separators(INSTALL_PREFIX)
endif()

fix_separators(VRAY_LIB_INSTALL_PREFIX)
fix_separators(VRAY_ZMQ_SERVER_INSTALL_PREFIX)
fix_separators(VRAY_TOOLS_INSTALL_PREFIX)

if (FULL_BUILD)
    include(xpak)
    include(xpak_versions)

    xpak_work_dir()

    # No Blender SDK pack for win_arm64, and everything used from it there - the Boost and
    # jpeg headers, the tinyxml sources, the Qt conf - carries no architecture anyway.
    set(_blender_sdk_arch "")
    if (CGR_TARGET_ARCH STREQUAL "win_arm64")
        set(_blender_sdk_arch ARCH x64)
    endif()

    install_xpak(PAK ${BLENDER_SDK_XPAK} VERSION ${BLENDER_SDK_XPAK_VERSION} ${_blender_sdk_arch})
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
set(VRAY_ZMQ_WRAPPER_ROOT  "${CMAKE_CURRENT_SOURCE_DIR}/zmq_wrapper" CACHE PATH "Path to V-Ray ZMQ wrapper headers")
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
# ZMQ
#
# The full build takes all of ZMQ from the 'libzmq' libpak in the xsdk submodule: zmq.h and the
# header-only cppzmq binding from its sources, the static library from the libpak build.
#
# The open-source build has no libpak tooling, so the caller supplies libzmq through
# ZMQ_LIBRARIES / ZMQ_INCLUDE_DIRS. Those name the library files outright rather than a root to
# search, so nothing here has to know how the library was named - that varies by toolset, libzmq
# version and whoever built it. ZMQ_CPPZMQ_DIR can override cppzmq as well, which is what lets the
# open-source build work without an xsdk checkout at all.

set(XSDK_LIBZMQ_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/xsdk/libzmq")

set(_zmq_includes "")
set(_zmq_libs     "")
set(_zmq_defines
    # Without this zmq.h declares every entry point __declspec(dllimport) on Windows.
    ZMQ_STATIC
    # The wrapper uses draft sockets (poller_t), so zmq.h has to declare them and cppzmq has to
    # wrap them. libzmq itself must be built with drafts enabled or the two disagree.
    ZMQ_BUILD_DRAFT_API
)

if (FULL_BUILD)
    set(ZMQ_LIBPAK "libzmq")
    install_libpak(PAK ${ZMQ_LIBPAK})

    list(APPEND _zmq_includes "${XSDK_LIBZMQ_ROOT}/libzmq/include")

    # Not overridable here: these headers have to be the ones the libpak was packaged from.
    set(ZMQ_CPPZMQ_DIR "${XSDK_LIBZMQ_ROOT}/cppzmq")
    set(_cppzmq_hint   "run 'git submodule update --init'")

    # zmq_s is the payload of the libpak's xsdk_libzmq_s target - the static half. Its sibling
    # zmq.lib is the import library of the DLL half, which we do not use. Located rather than
    # spelled out, and unset first because the artifacts path moves with the libpak version.
    unset(_zmq_libpak_lib CACHE)
    find_library(_zmq_libpak_lib NAMES zmq_s PATHS "${${ZMQ_LIBPAK}_ROOT}" NO_DEFAULT_PATH)
    if (NOT _zmq_libpak_lib)
        message(FATAL_ERROR "Static libzmq not found in the libzmq libpak artifacts (${${ZMQ_LIBPAK}_ROOT})")
    endif()
    list(APPEND _zmq_libs "${_zmq_libpak_lib}")
else()
    set(ZMQ_LIBRARIES    "" CACHE STRING "libzmq library file(s) to link, as full paths.")
    set(ZMQ_INCLUDE_DIRS "" CACHE STRING "Directories holding zmq.h.")
    set(ZMQ_CPPZMQ_DIR "" CACHE PATH "Directory holding zmq.hpp and zmq_addon.hpp.")

    fix_separators(ZMQ_LIBRARIES)
    fix_separators(ZMQ_INCLUDE_DIRS)
    fix_separators(ZMQ_CPPZMQ_DIR)

    if (ZMQ_LIBRARIES)
        list(APPEND _zmq_libs ${ZMQ_LIBRARIES})
    else()
        message(FATAL_ERROR "ZMQ_LIBRARIES is empty - set it to the libzmq library file(s) to link.")
    endif()

    if (ZMQ_INCLUDE_DIRS)
        list(APPEND _zmq_includes ${ZMQ_INCLUDE_DIRS})
    else()
        message(FATAL_ERROR "ZMQ_INCLUDE_DIRS is empty - set it to the directories holding zmq.h.")
    endif()

    if (ZMQ_CPPZMQ_DIR)
        set(_cppzmq_hint   "check ZMQ_CPPZMQ_DIR")
    else()
        message(FATAL_ERROR "ZMQ_CPPZMQ_DIR is empty - set it to the directory holding zmq.hpp and zmq_addon.hpp.")
    endif()
endif()

if (NOT EXISTS "${ZMQ_CPPZMQ_DIR}/zmq.hpp")
    message(FATAL_ERROR "cppzmq not found in \"${ZMQ_CPPZMQ_DIR}\" - ${_cppzmq_hint}")
endif()
list(APPEND _zmq_includes "${ZMQ_CPPZMQ_DIR}")

if (WIN32)
    # Winsock, UuidCreateSequential and GetAdaptersAddresses respectively. A static libzmq
    # carries no import records, so its consumers have to link these themselves.
    list(APPEND _zmq_libs ws2_32.lib rpcrt4.lib iphlpapi.lib)
endif()

message(STATUS "ZMQ: ${_zmq_libs}")
message(STATUS "cppzmq: ${ZMQ_CPPZMQ_DIR}")

add_library(zmq::zmq INTERFACE IMPORTED)
set_target_properties(zmq::zmq PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_zmq_includes}"
    INTERFACE_COMPILE_DEFINITIONS "${_zmq_defines}"
    INTERFACE_LINK_LIBRARIES      "${_zmq_libs}"
)

####################################################
# Open-source components

# VRayZmqWrapper
add_subdirectory(zmq_wrapper)

# VRayBlenderLib
add_subdirectory(blender_lib)

####################################################
# Closed-source components

if (FULL_BUILD)
    # VRayZmqServer
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/server")
        add_subdirectory(server)
        add_dependencies(VRayZmqServer VRayZmqWrapper)
    else()
        message(STATUS "Skipping project generation for VRayZmqServer")
    endif()

    # VRayTools
    if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/vray_tools")
        add_subdirectory(vray_tools)
    else()
        message(STATUS "Skipping project generation for VRayTools")
    endif()

endif()

if(INSTALL_LOCAL AND ADDON_PATH)
    install(DIRECTORY "python_exporter/" DESTINATION "${ADDON_PATH}")

    # The Chaos Scatter addon installs beside vray_blender so it can be enabled independently.
    if (WITH_CHAOS_SCATTER)
        get_filename_component(_addonsRoot "${ADDON_PATH}" DIRECTORY)
        install(DIRECTORY "chaos_scatter/" DESTINATION "${_addonsRoot}/chaos_scatter")
    endif()
endif()

if (FULL_BUILD)
    if(EXISTS ${CMAKE_CURRENT_SOURCE_DIR}/installer)
        add_subdirectory(installer)
    else()
        message(STATUS "Skipping generation installation scripts")
    endif()
endif()
