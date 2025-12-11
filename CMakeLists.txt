cmake_minimum_required(VERSION 3.21 FATAL_ERROR)

project(VRayForBlender)

set(CMAKE_CXX_STANDARD 20)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(common_macros)

set_build_configurations()

set(BLENDER_VER    ""  CACHE STRING  "Blender version in format major.minor, e.g. 4.2")
set(ADDON_PATH     ""  CACHE PATH    "Path to the Blender addon. If set, the INSTALL target will use it as its destination.")
set(BINTOOLS_PATH  ""  CACHE STRING  "Path to cgrepo (parent of bintools). If not set, xpaks won't be used.")
set(INSTALL_LOCAL  ON  CACHE BOOL    "Configure Install step")
set(WITH_DR2       ON  CACHE BOOL    "Enable DR2")

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
    add_compile_options(-Wno-switch -Wno-macro-redefined)
endif()

if (WITH_DR2)
    add_compile_definitions(WITH_DR2)
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
    set(APPSDK_XPAK "Blender_AppSDK_7")

    include(xpak)
    include(xpak_versions)

    set_build_configurations()

    xpak_work_dir()

    install_xpak(PAK ${APPSDK_XPAK} VERSION ${APPSDK_XPAK_VERSION})
    install_xpak(PAK ${BLENDER_SDK_XPAK} VERSION ${BLENDER_SDK_XPAK_VERSION})

    set(APPSDK_ROOT  "${${APPSDK_XPAK}_ROOT}" CACHE PATH   "Path to AppSDK root")
    set(SDK_ROOT     "${${BLENDER_SDK_XPAK}_ROOT}")

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
        install(DIRECTORY "vray_for_blender_python_exporter/"
            DESTINATION "${ADDON_PATH}"
            PATTERN "external/mmh3.cp311-win_amd64.pyd" EXCLUDE
        )
    elseif(WIN32)
        install(DIRECTORY "vray_for_blender_python_exporter/"
            DESTINATION "${ADDON_PATH}"
            PATTERN "external/mmh3.cpython-311-darwin.so" EXCLUDE
        )
    endif()
endif()