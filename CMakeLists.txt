# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.21 FATAL_ERROR)

project(VRayForBlender)

set(CMAKE_CXX_STANDARD 20)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(common_macros)

set_build_configurations()

set(BLENDER_VER         ""  CACHE STRING  "Blender version in format major.minor, e.g. 4.2")
set(ADDON_PATH          ""  CACHE PATH    "Path to the Blender addon. If set, the INSTALL target will use it as its destination.")
set(BINTOOLS_PATH       ""  CACHE STRING  "Path to cgrepo (parent of bintools). If not set, xpaks won't be used.")
set(INSTALL_LOCAL       ON  CACHE BOOL    "Configure Install step")
set(WITH_DR2            ON  CACHE BOOL    "Enable DR2")
set(CUSTOM_APPSDK_ROOT  ""  CACHE PATH    "Root directory of the App SDK (optional)")
set(WITH_PROFILING      OFF CACHE BOOL    "Enables some profiling features")

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

if (APPLE)
    set(CMAKE_OSX_DEPLOYMENT_TARGET  "11.0")
endif()

if(UNIX)
    add_compile_options(-Wno-switch -Wno-macro-redefined -Wno-ambiguous-reversed-operator)

    if (NOT APPLE)
        set(link_magic_common -Wl,--gc-sections -Wl,--discard-all -Wl,--no-as-needed -fuse-ld=lld)
        string(JOIN " " linker_args ${link_magic_common})
        set(CMAKE_EXE_LINKER_FLAGS "${CMAKE_EXE_LINKER_FLAGS} ${linker_args}")
        set(CMAKE_SHARED_LINKER_FLAGS "${CMAKE_SHARED_LINKER_FLAGS} ${linker_args}")
        set(CMAKE_MODULE_LINKER_FLAGS "${CMAKE_MODULE_LINKER_FLAGS} ${linker_args}")
    endif()
endif()

if (WITH_DR2)
    add_compile_definitions(WITH_DR2)
endif()
if (WITH_PROFILING)
    add_compile_definitions(WITH_MESSAGE_PROFILING)
endif()

if (MSVC AND MSVC_VERSION GREATER_EQUAL 1940)
    add_compile_definitions(_DISABLE_CONSTEXPR_MUTEX_CONSTRUCTOR)
endif()

if(NOT ${BLENDER_VER})
    message(FATAL_ERROR "BLENDER_VER not defined")
endif()

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


if (FULL_BUILD)
    include(xpak)
    include(xpak_versions)

    set_build_configurations()

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

    fix_separators(APPSDK_ROOT)
    fix_separators(SDK_ROOT)
endif()


# Configure the path to VRayZmqWrapper to be consumed by the Lib and Server projects
set(VRAY_ZMQ_WRAPPER_ROOT  "${CMAKE_CURRENT_SOURCE_DIR}/vray_for_blender_zmq_wrapper" CACHE PATH "Path to V-Ray ZMQ wrapper headers")
set(VRAY_ZMQ_WRAPPER_LIBS  "$<TARGET_FILE_DIR:VRayZmqWrapper>" CACHE PATH "Path to ZMQ libs")

fix_separators(VRAY_ZMQ_WRAPPER_ROOT)
fix_separators(VRAY_ZMQ_WRAPPER_LIBS)


####################################################
# Open-source components

# VRayZmqWrapper
add_subdirectory(vray_for_blender_zmq_wrapper)

# VRayBlenderLib
add_subdirectory(vray_for_blender_native)
add_dependencies(VRayBlenderLib VRayZmqWrapper)


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
    if(APPLE)
        install(
            DIRECTORY "vray_for_blender_python_exporter/"
            DESTINATION "${ADDON_PATH}"
            PATTERN "external/mmh3.cp311-win_amd64.pyd" EXCLUDE
            PATTERN "external/mmh3.cpython-311-darwin.so" EXCLUDE
        )
    elseif(WIN32)
        install(DIRECTORY "vray_for_blender_python_exporter/"
            DESTINATION "${ADDON_PATH}"
            PATTERN "external/mmh3.cpython-311-darwin.so" EXCLUDE
            PATTERN "external/mmh3.cpython-311-x86_64-linux-gnu.so" EXCLUDE
        )
    else()
        install(DIRECTORY "vray_for_blender_python_exporter/"
            DESTINATION "${ADDON_PATH}"
            PATTERN "external/mmh3.cpython-311-darwin.so" EXCLUDE
            PATTERN "external/mmh3.cp311-win_amd64.pyd" EXCLUDE
        )
    endif()
endif()
