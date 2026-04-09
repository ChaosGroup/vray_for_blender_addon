# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.25 FATAL_ERROR)


message(STATUS "")
message(STATUS "CONFIGURE VRayBlenderLib")
message(STATUS "========================")

project(VRayBlenderLib)

# This policy should be enabled in order to be used IN_LIST
# https://cmake.org/cmake/help/v3.7/policy/CMP0057.html
cmake_policy(SET CMP0057 NEW)

set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

if (APPLE)
    message(STATUS "   Architecture: ${CMAKE_OSX_ARCHITECTURES}")
endif()

if(PROJECT_IS_TOP_LEVEL)
    set(BLENDER_VER      ""  CACHE STRING  "Blender version in format major.minor, e.g. 4.2")
    set(INSTALL_LOCAL    ON  CACHE BOOL    "Configure Install step")
    set(INSTALL_PREFIX   ""  CACHE PATH    "Path to the installation folder")
endif()

if (NOT BINTOOLS_PATH)
    set(BOOST_LIBDIR    "./boost"               CACHE PATH    "Path to Boost libs")
    set(ZMQ_LIBDIR      "./zmq"                 CACHE PATH    "Path to ZMQ libs")
    set(BLENDER_SDK_ROOT "./blender_sdk"        CACHE PATH    "Path to libs shipped with Blender")
else()
    set(BOOST_LIBDIR    "${SDK_ROOT}/boost"               CACHE PATH    "Path to Boost libs")
    set(ZMQ_LIBDIR      "${SDK_ROOT}/zmq"                 CACHE PATH    "Path to ZMQ libs")
    set(BLENDER_SDK_ROOT "${SDK_ROOT}/blender_sdk"        CACHE PATH    "Path to libs shipped with Blender")
endif()

fix_separators(ZMQ_LIBDIR)
fix_separators(BLENDER_SDK_ROOT)

message(STATUS "   Dependencies")
message(STATUS "   -------------")
message(STATUS "   Blener SDK root: \"${BLENDER_SDK_ROOT}\"")
message(STATUS "   Boost root: \"${BOOST_LIBDIR}\"")
message(STATUS "   ZMQ root: \"${ZMQ_LIBDIR}\"")
message(STATUS "   Nanobind root: \"${NANOBIND_LIBDIR}\"")

# Organize the source files by folder in the IDE
GroupSources(api .)
GroupSources(export .)
GroupSources(utils .)

file(GLOB_RECURSE SOURCES
    "api/*.cpp"
    "api/*.hpp"
    "export/*.cpp"
    "export/*.hpp"
    "utils/*.cpp"
    "utils/*.hpp"
)

file(GLOB_RECURSE HEADERS
    "api/*.h"
    "api/*.hpp"
    "export/*.h"
    "export/*.hpp"
    "utils/*.h"
    "utils/*.hpp"
)

use_zmq(${ZMQ_LIBDIR})

if(WIN32)

    if(CMAKE_C_COMPILER_ID MATCHES "MSVC")
        add_link_options(
            "/IGNORE:4217" # WARNING: __declspec(dllimport) was specified for a symbol even though the symbol is defined in an object file in the same image.
        )
    endif()

endif()

add_compile_definitions(
    BOOST_ALL_NO_LIB
    BOOST_PROCESS_USE_STD_FS
)

if (INSTALL_LOCAL)
    if (WIN32)
        if (BLENDER_VER VERSION_LESS "4.4" AND WITH_OSL)
            set (COPY_LIBS
                "${BLENDER_SDK_ROOT}/osl/bin/oslcomp.dll"
                "${BLENDER_SDK_ROOT}/osl/bin/oslquery.dll"
            )
        endif()

        if(FULL_BUILD)
            set(COPY_TOOLS
                ${SDK_ROOT}/tools/vrmatconvert.exe
            )
        endif()

    else()
        set(DLL_EXT "")
        if (APPLE)
            set(DLL_EXT ".dylib")
        else()
            set(DLL_EXT ".so")
        endif()
        # On linux we always want to copy osl libs, they might be loaded from system package
        # but OSL is version namespaced.
        if (WITH_OSL AND (NOT APPLE OR (BLENDER_VER VERSION_LESS "4.4")))
            set (COPY_LIBS
                "${BLENDER_SDK_ROOT}/osl/lib/liboslcomp${DLL_EXT}"
                "${BLENDER_SDK_ROOT}/osl/lib/liboslquery${DLL_EXT}"
            )
        endif()
    endif()

    set(CMAKE_INSTALL_PREFIX "${CMAKE_CURRENT_LIST_DIR}")

    fix_separators(VRAY_LIB_INSTALL_PREFIX)
    if(VRAY_LIB_INSTALL_PREFIX)
        set(INSTALL_PREFIX "${VRAY_LIB_INSTALL_PREFIX}")
    endif()

    if(NOT EXISTS "${INSTALL_PREFIX}")
        file(MAKE_DIRECTORY "${INSTALL_PREFIX}")
    endif()

    # Copy the newly built files
    message(STATUS "   Install to: ${INSTALL_PREFIX}")

    install(FILES ${COPY_LIBS} DESTINATION ${INSTALL_PREFIX})
    install(FILES ${COPY_TOOLS} DESTINATION ${INSTALL_PREFIX})
    install(FILES "${CMAKE_CURRENT_LIST_DIR}/VRayBlenderLib.pyi" DESTINATION ${INSTALL_PREFIX})
endif()

function (setup_blender_lib_target BLENDER_LIB_TARGET)
    if(WIN32 AND CMAKE_C_COMPILER_ID MATCHES "MSVC")
        # Disable warnings on external includes
        target_compile_options(${BLENDER_LIB_TARGET} PRIVATE
            /external:W0 /external:anglebrackets /W4 /WX
            /MP /GR-
            $<$<CONFIG:Release>:/O2 /Ob2 /Oi /Ot>
        )
        target_compile_definitions(${BLENDER_LIB_TARGET} PRIVATE $<$<CONFIG:NoOpt>:VASSERT_ENABLED>)
    else()
        target_compile_options(${BLENDER_LIB_TARGET} PRIVATE -Werror)
    endif()

    link_with_zmq(${BLENDER_LIB_TARGET} PRIVATE)

    target_link_libraries(${BLENDER_LIB_TARGET} PRIVATE VRayZmqWrapper)

    if (WITH_OSL)
        target_compile_definitions(${BLENDER_LIB_TARGET} PRIVATE WITH_OSL)
    endif()


    target_link_directories(${BLENDER_LIB_TARGET} PRIVATE
        "${BLENDER_SDK_ROOT}/jpeg/lib"
        "${VRAY_ZMQ_WRAPPER_LIBS}"
    )
    if (WITH_OSL)
        target_link_directories(${BLENDER_LIB_TARGET} PRIVATE
            "${BLENDER_SDK_ROOT}/OpenImageIO/lib"
            "${BLENDER_SDK_ROOT}/osl/lib"
        )
    endif()

    target_include_directories(${BLENDER_LIB_TARGET} PRIVATE
        .
        utils
        ${VRAY_ZMQ_WRAPPER_ROOT}/include/
        ${VRAY_ZMQ_WRAPPER_ROOT}/utils/
        ${VRAY_ZMQ_WRAPPER_ROOT}/extern/
        ${BOOST_LIBDIR}/include
        ${BLENDER_SDK_ROOT}/jpeg/include/
    )

    if (WITH_OSL)
        target_include_directories(${BLENDER_LIB_TARGET} PRIVATE
            ${BLENDER_SDK_ROOT}/osl/include/
            ${BLENDER_SDK_ROOT}/openimageio/include/
            ${BLENDER_SDK_ROOT}/imath/include/
        )
    endif()

    if(WIN32)
        # Windows SDK
        list(APPEND LIB_WIN32 "version.lib")

        if (WITH_OSL)
            if(BLENDER_VER STREQUAL "4.2" OR  BLENDER_VER STREQUAL "4.3")
                # For these versions, the lib files for OSL are not matched with the dlls provided in the SDK. This manifest will force
                # the correct versions to be loaded from the addon folder at runtime.
                target_sources(${BLENDER_LIB_TARGET} PRIVATE ${CMAKE_CURRENT_LIST_DIR}/VRayBlenderLib.manifest)
            endif()

            list(APPEND LIB_WIN32 "OpenImageIO_Util.lib")
            list(APPEND LIB_WIN32 "oslquery.lib" "oslcomp.lib")
        endif()

        list(APPEND LIB_WIN32 "libjpeg.lib")

        target_link_libraries(${BLENDER_LIB_TARGET} PRIVATE ${LIB_WIN32})

        if (MSVC)
			target_link_options(${BLENDER_LIB_TARGET} PRIVATE
                "$<$<CONFIG:Release>:/DEBUG:FULL>"
                "$<$<CONFIG:Release>:/OPT:REF>"
            )
            if (NOT WITH_DYNAMIC_DEOPT)
                target_link_options(${BLENDER_LIB_TARGET} PRIVATE
                    "$<$<CONFIG:Release>:/OPT:ICF>"
                )
            endif()

            # Set debugging environment and target
            set_target_properties(${BLENDER_LIB_TARGET} PROPERTIES
                VS_DEBUGGER_COMMAND "C:/Program Files/Blender Foundation/Blender ${BLENDER_VER}/blender.exe" # Set blender as the default debugger command to make it easy to start.
                VS_DEBUGGER_ENVIRONMENT "TBB_MALLOC_DISABLE_REPLACEMENT=1" # Disable the TBB malloc replacement, which throws SEH exceptions
            )
            
            # Add the Python interface description file to the VS project for easier access
            target_sources(${BLENDER_LIB_TARGET} PRIVATE ${CMAKE_CURRENT_LIST_DIR}/VRayBlenderLib.pyi)
			set_source_files_properties(${CMAKE_CURRENT_LIST_DIR}/VRayBlenderLib.pyi PROPERTIES HEADER_FILE_ONLY TRUE)
        endif()

    else()
        list(APPEND LIB_UNIX jpeg)

        if (APPLE)
            if (WITH_OSL)
                target_link_directories(${BLENDER_LIB_TARGET} PRIVATE "${BLENDER_SDK_ROOT}/openimageio/lib")

                list(APPEND LIB_UNIX oslquery oslcomp oslexec oslnoise)
                list(APPEND LIB_UNIX OpenImageIO_Util)
            endif()

            # Neccessary for std::stop_source in clang14(on MacOS)...
            target_compile_options(${BLENDER_LIB_TARGET} PRIVATE -fexperimental-library -Wno-enum-constexpr-conversion)

            target_link_options(${BLENDER_LIB_TARGET} PRIVATE "-undefined" "dynamic_lookup")
        endif()
        target_link_libraries(${BLENDER_LIB_TARGET} PRIVATE ${LIB_UNIX})
    endif()
    # TODO: no boost, no need for rpath?
    if (APPLE)
        set_target_properties(${BLENDER_LIB_TARGET} PROPERTIES
            INSTALL_RPATH "@loader_path"
            BUILD_WITH_INSTALL_RPATH ON
        )
    elseif(UNIX)
        set_target_properties(${BLENDER_LIB_TARGET} PROPERTIES
            INSTALL_RPATH "$ORIGIN"
            BUILD_WITH_INSTALL_RPATH ON
        )
    endif()

    if (INSTALL_LOCAL)
        install(TARGETS ${BLENDER_LIB_TARGET} LIBRARY DESTINATION ${INSTALL_PREFIX})
        if (MSVC)
            # Install the PDB file
            install(FILES $<TARGET_PDB_FILE:${BLENDER_LIB_TARGET}>
                DESTINATION ${INSTALL_PREFIX}
                CONFIGURATIONS Release NoOpt
            )
        endif()
    endif()
endfunction()

function(setup_nanobind_target PYTHON_VERSION STABLE_ABI)
    if (STABLE_ABI)
        set(NANOBIND_TARGET "nanobind-${PYTHON_VERSION}-stable")
    else()
        set(NANOBIND_TARGET "nanobind-${PYTHON_VERSION}")
    endif()
    set(NANOBIND_TARGET "${NANOBIND_TARGET}" PARENT_SCOPE)

    add_library(${NANOBIND_TARGET} STATIC
        ${NANOBIND_LIBDIR}/src/nb_internals.cpp
        ${NANOBIND_LIBDIR}/src/nb_func.cpp
        ${NANOBIND_LIBDIR}/src/nb_type.cpp
        ${NANOBIND_LIBDIR}/src/nb_enum.cpp
        ${NANOBIND_LIBDIR}/src/nb_ndarray.cpp
        ${NANOBIND_LIBDIR}/src/nb_static_property.cpp
        ${NANOBIND_LIBDIR}/src/common.cpp
        ${NANOBIND_LIBDIR}/src/error.cpp
        ${NANOBIND_LIBDIR}/src/trampoline.cpp
        ${NANOBIND_LIBDIR}/src/implicit.cpp
    )
    set_target_properties(${NANOBIND_TARGET} PROPERTIES POSITION_INDEPENDENT_CODE ON)
    if (NOT MSVC)
        target_compile_options(${NANOBIND_TARGET} PRIVATE -fno-strict-aliasing)
    endif()

    target_compile_features(${NANOBIND_TARGET} PRIVATE cxx_std_17)

    set_target_properties(${NANOBIND_TARGET} PROPERTIES CXX_VISIBILITY_PRESET hidden)

    set(PYTHON_VERSION_INCLUDES "")
    if (STABLE_ABI)
        # nanobind supports stable abi from python3.12, so we just use an appsdk python
        install_xpak(PAK "AppsdkPython312" VERSION "3.12.4.1000")
        set(PYTHON_PATH ${X_PAK}/AppsdkPython312)
	if (UNIX)
		set(PYTHON_VERSION_INCLUDES python3.12)
	endif()
    else()
        if (WIN32)
            set(PYTHON_PATH ${BLENDER_SDK_ROOT}/python/311)
        else()
            set(PYTHON_VERSION_INCLUDES python3.11)
            set(PYTHON_PATH ${BLENDER_SDK_ROOT}/python)
        endif()
    endif()

    set(PYTHON_PATH ${PYTHON_PATH} PARENT_SCOPE)

    target_include_directories(${NANOBIND_TARGET} PUBLIC
	${PYTHON_PATH}/include/${PYTHON_VERSION_INCLUDES}
        ${NANOBIND_LIBDIR}/include
    )

    if (UNIX)
        target_link_directories(${NANOBIND_TARGET} PUBLIC ${PYTHON_PATH}/lib)
    else()
        target_link_directories(${NANOBIND_TARGET} PUBLIC ${PYTHON_PATH}/libs)
    endif()

    if (STABLE_ABI)
        target_compile_definitions(${NANOBIND_TARGET} PUBLIC -DPy_LIMITED_API=0x030C0000) # 3.12
    endif()

    if (WIN32)
        if (STABLE_ABI)
            target_link_libraries(${NANOBIND_TARGET} PUBLIC python3)
        else()
            string(REPLACE "." "" VERSION_NO_DOT "${PYTHON_VERSION}")
            target_link_libraries(${NANOBIND_TARGET} PUBLIC python${VERSION_NO_DOT})
        endif()
    endif()

    target_link_libraries(${NANOBIND_TARGET} PUBLIC tsl::robin_map)
endfunction()

function(set_lib_suffix VERSION STABLE_ABI PYTHON_PATH)
    if (STABLE_ABI)
        set(PYTHON_EXECUTABLE ${X_PAK}/AppsdkPython312/python)
    elseif(WIN32)
        set(PYTHON_EXECUTABLE ${BLENDER_SDK_ROOT}/python/311/bin/python)
    else()
	set(PYTHON_EXECUTABLE ${BLENDER_SDK_ROOT}/python/bin/python3.11)
    endif()
    if (STABLE_ABI)
        if (WIN32)
            set(EXT_SUFFIX ".pyd")
        else()
            # Technically we don't really need the abi3 on unix as loading order there should
            # be .cpython3.11.so -> abi3.so -> .so, but add it just in case.
            set(EXT_SUFFIX ".abi3.so")
        endif()
    else()
        execute_process(
            COMMAND "${PYTHON_EXECUTABLE}" "-c" "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))"
            RESULT_VARIABLE SUFFIX_CMD_RET
            OUTPUT_VARIABLE EXT_SUFFIX
            OUTPUT_STRIP_TRAILING_WHITESPACE
        )

        if(SUFFIX_CMD_RET AND NOT SUFFIX_CMD_RET EQUAL 0)
		    # message(FATAL_ERROR "Python sysconfig query to find 'EXT_SUFFIX' property failed ${PYTHON_EXECUTABLE}!")
            # Temporary workaround because of a problematic python in our Blender SDK MacOS xpak.
            set(EXT_SUFFIX ".so")
        endif()
    endif()

    set(EXT_SUFFIX ${EXT_SUFFIX} PARENT_SCOPE)
endfunction()

function (add_python_module VERSION)
    set(options STABLE_ABI)
    cmake_parse_arguments(ARG "${options}" "" "" ${ARGN})

    if (ARG_STABLE_ABI)
        set(BLENDER_PROJECT_SUFFIX "-stable")
    endif()
    set(BLENDER_LIB_TARGET "VRayBlenderLib-${VERSION}${BLENDER_PROJECT_SUFFIX}")
    message(STATUS "Python target ${BLENDER_LIB_TARGET}")
    message(STATUS "=============================================")

    setup_nanobind_target(${VERSION} ${ARG_STABLE_ABI})

    add_library(${BLENDER_LIB_TARGET} MODULE "${SOURCES};${HEADERS}")
    # Ensure the output names are the same, we set a suffix later.
    set_target_properties(${BLENDER_LIB_TARGET} PROPERTIES OUTPUT_NAME "VRayBlenderLib")

    target_link_libraries(${BLENDER_LIB_TARGET} PRIVATE ${NANOBIND_TARGET})

    if (APPLE)
        target_link_options(${BLENDER_LIB_TARGET} PRIVATE $<$<CONFIG:Release>:-Wl,-dead_strip -Wl,-x -Wl,-S>)
    elseif (NOT WIN32)
        target_link_options(${BLENDER_LIB_TARGET} PRIVATE $<$<CONFIG:Release>:-Wl,-s>)
    endif()

    set_lib_suffix(${VERSION} ${ARG_STABLE_ABI} ${PYTHON_PATH})

    set_target_properties(${BLENDER_LIB_TARGET} PROPERTIES PREFIX "" SUFFIX "${EXT_SUFFIX}")

    # Since we manually set the OUTPUT_NAME for potentially two targets to be the same, the name
    # of the PDB also be the same, so we add the EXT_SUFFIX but without the .pyd/.so extension.
    string(REGEX REPLACE "\\.[^.]*$" "" PDB_SUFFIX "${EXT_SUFFIX}")
    set_target_properties(${BLENDER_LIB_TARGET} PROPERTIES PDB_NAME "VRayBlenderLib${PDB_SUFFIX}")

    # Do we need response file on macos? Or is -undefined, dynamic_lookup enough?
    setup_blender_lib_target(${BLENDER_LIB_TARGET})
endfunction()

if (BLENDER_VER VERSION_GREATER_EQUAL "5.1")
    # Blender 5.1 uses python 3.13, for our full builds use 3.12 with STABLE_ABI, for open source
    # for now just go with 3.13 and expect users to download 3.13 python from Blender's libs.
    if (FULL_BUILD)
        add_python_module(3.12 STABLE_ABI)
        set(STARTUP_PROJECT "VRayBlenderLib-3.12-stable")
    else()
        add_python_module(3.13)
        set(STARTUP_PROJECT "VRayBlenderLib-3.11")
    endif()
elseif(UNIX AND NOT APPLE)
    set(STARTUP_PROJECT "VRayBlenderLib-3.11")
    add_python_module(3.11)
    if (FULL_BUILD)
        add_python_module(3.12 STABLE_ABI)
    endif()
else()
    set(STARTUP_PROJECT "VRayBlenderLib-3.11")
    add_python_module(3.11)
endif()


if (MSVC)
	set_property(DIRECTORY PROPERTY VS_STARTUP_PROJECT ${STARTUP_PROJECT})
endif()
