# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

cmake_minimum_required(VERSION 3.25 FATAL_ERROR)

message(STATUS "")
message(STATUS "CONFIGURE VRayZmqWrapper")
message(STATUS "========================")

project(VRayZmqWrapper)

set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")
include(common_macros)

if (WITH_TESTS)
    # Vendored Catch2 v2.13.4 single header (this repo doesn't ship the v3 "amalgamated" distribution).
    set(CATCH2_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../xsdk/catch/catch_2_13_4" CACHE PATH "Path to Catch2 install dir")
    fix_separators(CATCH2_ROOT)
endif()

if (NOT BINTOOLS_PATH)
    set(BOOST_LIBDIR  "./boost" CACHE PATH "Path to Boost libs")
else()
    set(BOOST_LIBDIR  "${SDK_ROOT}/boost" CACHE PATH    "Path to Boost libs")
endif()

fix_separators(BOOST_LIBDIR)

message(STATUS "   Dependencies")
message(STATUS "   -------------")
message(STATUS "   Boost libs: \"${BOOST_LIBDIR}\"")


if (WIN32)
    # Organize the source files by folder in the IDE
    GroupSources(include .)
    GroupSources(sources .)
    GroupSources(utils .)

    if (WITH_TESTS)
        source_group(tests REGULAR_EXPRESSION ".*/tests/.*")
    endif()

endif()

# Directories to be registered as 'include' for the project
include_directories(
    "include"
	"utils"
    ${BOOST_LIBDIR}/include
)

if (WITH_TESTS)
    include_directories(${CATCH2_ROOT} tests)

    file(GLOB TESTS_HEADERS
        "tests/*.hpp"
    )

    file(GLOB TESTS_SOURCES
        "tests/*.cpp"
    )
endif()

file(GLOB HEADERS 
    "include/*.h"
    "include/*.hpp"
    "utils/*.h"
)

file(GLOB SOURCES 
    "sources/*.cpp"
    "include/*.hpp"
)

# When built as part of the top-level project these targets already exist (CMakeLists.txt);
# define them here too so zmq_wrapper also configures standalone (see readme.md).
if (NOT TARGET tsl::robin_map)
    add_library(tsl::robin_map INTERFACE IMPORTED)
    set_target_properties(tsl::robin_map PROPERTIES
        INTERFACE_INCLUDE_DIRECTORIES "${CMAKE_CURRENT_SOURCE_DIR}/../xsdk/nanobind/ext/robin_map/include"
    )
endif()

if (NOT TARGET zmq::zmq)
    set(ZMQ_LIBRARIES    "" CACHE STRING "libzmq library file(s) to link, as full paths.")
    set(ZMQ_INCLUDE_DIRS "" CACHE STRING "Directories holding zmq.h.")
    set(ZMQ_CPPZMQ_DIR   "" CACHE PATH   "Directory holding zmq.hpp and zmq_addon.hpp.")
    fix_separators(ZMQ_LIBRARIES)
    fix_separators(ZMQ_INCLUDE_DIRS)
    fix_separators(ZMQ_CPPZMQ_DIR)

    set(_zmq_standalone_libs ${ZMQ_LIBRARIES})
    if (WIN32)
        # Winsock, UuidCreateSequential and GetAdaptersAddresses respectively. A static libzmq
        # carries no import records, so its consumers have to link these themselves.
        list(APPEND _zmq_standalone_libs ws2_32.lib rpcrt4.lib iphlpapi.lib)
    endif()

    add_library(zmq::zmq INTERFACE IMPORTED)
    set_target_properties(zmq::zmq PROPERTIES
        INTERFACE_INCLUDE_DIRECTORIES "${ZMQ_INCLUDE_DIRS};${ZMQ_CPPZMQ_DIR}"
        INTERFACE_COMPILE_DEFINITIONS "ZMQ_STATIC;ZMQ_BUILD_DRAFT_API"
        INTERFACE_LINK_LIBRARIES      "${_zmq_standalone_libs}"
    )
endif()


add_compile_definitions(
    BOOST_ALL_NO_LIB        # Allow overriding target toolset version of libs
    BOOST_USE_STATIC_LIBS   # Statically link to Boost libraries
)

add_library(${PROJECT_NAME} STATIC "${SOURCES};${HEADERS}")
target_link_libraries(${PROJECT_NAME} tsl::robin_map zmq::zmq)

if(WIN32 AND CMAKE_C_COMPILER_ID MATCHES "MSVC")
    # Disable warnings on external includes
    target_compile_options(${PROJECT_NAME} PRIVATE /external:W0 /external:anglebrackets /W4 /WX)
    target_compile_definitions(${PROJECT_NAME} PRIVATE $<$<CONFIG:NoOpt>:VASSERT_ENABLED>)
else()
    target_compile_options(${PROJECT_NAME} PRIVATE -Werror)
endif()

if(VRAY_ZMQ_WRAPPER_INSTALL_PREFIX)
    # using "\" causes errors in some cases
    string(REPLACE "\\" "/" VRAY_ZMQ_WRAPPER_INSTALL_PREFIX ${VRAY_ZMQ_WRAPPER_INSTALL_PREFIX})
    set(INSTALL_PREFIX "${VRAY_ZMQ_WRAPPER_INSTALL_PREFIX}/VRayZmqWrapper/")

    message(STATUS "   Install to: ${INSTALL_PREFIX}")

    install(TARGETS ${PROJECT_NAME} ARCHIVE DESTINATION "${INSTALL_PREFIX}/lib")
    install(DIRECTORY  include/ DESTINATION "${INSTALL_PREFIX}/include")
    install(DIRECTORY  utils/ DESTINATION "${INSTALL_PREFIX}/utils")
    # cppzmq goes out with the wrapper's own headers - they include it, and a consumer of the
    # packaged wrapper has no xsdk to take it from.
    install(FILES  "${ZMQ_CPPZMQ_DIR}/zmq.hpp" "${ZMQ_CPPZMQ_DIR}/zmq_addon.hpp"
            DESTINATION "${INSTALL_PREFIX}/include")
endif()

if (WITH_TESTS)
    add_executable("${PROJECT_NAME}Tests" "${HEADERS};${TESTS_SOURCES};${TESTS_HEADERS}")

    # An executable has to resolve these itself, unlike the .so. On RHEL8's glibc the pthread_*
    # and shm_*/sem_* that boost::interprocess and libzmq need are not in libc yet.
    find_package(Threads REQUIRED)
    target_link_libraries("${PROJECT_NAME}Tests" ${PROJECT_NAME} Threads::Threads)
    if (UNIX AND NOT APPLE)
        target_link_libraries("${PROJECT_NAME}Tests" rt)
    endif()

    # Add a test executable
    enable_testing()

    # Add a test
    add_test(NAME ZmqTest COMMAND "${PROJECT_NAME}Tests")
endif()