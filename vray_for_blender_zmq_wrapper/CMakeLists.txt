cmake_minimum_required(VERSION 3.21 FATAL_ERROR)

message(STATUS "")
message(STATUS "CONFIGURE VRayZmqWrapper")
message(STATUS "========================")

project(VRayZmqWrapper)

set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")

if(PROJECT_IS_TOP_LEVEL)
    set(BLENDER_VER      ""  CACHE STRING  "Blender version in format major.minor, e.g. 4.2")
    set(INSTALL_LOCAL    ON  CACHE BOOL    "Configure Install step")
    set(INSTALL_PREFIX   ""  CACHE PATH    "Path to the installation folder")
endif()

include(common_macros)
set_build_configurations()

if (WITH_TESTS)
    set(CATCH2_ROOT "" CACHE PATH   "Path to Catch2 install dir")
    fix_separators(CATCH2_ROOT)
endif()

if (NOT BINTOOLS_PATH)

    set(BOOST_LIBDIR  "./boost" CACHE PATH "Path to Boost libs")
    set(ZMQ_LIBDIR    "./zmq"   CACHE PATH "Path to ZMQ libs")
else()
    
    include(xpak)
    include(xpak_versions)
    
    xpak_work_dir()
    install_xpak(PAK ${BLENDER_SDK_XPAK} VERSION ${BLENDER_SDK_XPAK_VERSION})
    
    
    set(SDK_ROOT      "${${BLENDER_SDK_XPAK}_ROOT}")
    
    set(BOOST_LIBDIR  "${SDK_ROOT}/boost" CACHE PATH    "Path to Boost libs")
    set(ZMQ_LIBDIR    "${SDK_ROOT}/zmq"   CACHE PATH    "Path to ZMQ libs")

endif()



fix_separators(BOOST_LIBDIR)
fix_separators(ZMQ_LIBDIR)
fix_separators(INSTALL_PREFIX)

message(STATUS "   Dependencies")
message(STATUS "   -------------")
message(STATUS "   Boost libs: \"${BOOST_LIBDIR}\"")
message(STATUS "   ZMQ libs: \"${ZMQ_LIBDIR}\"")


if (WIN32)
    # Organize the source files by folder in the IDE
    source_group(extern/cppzmq REGULAR_EXPRESSION ".*/extern/cppzmq/.*\\.(cpp|hpp)")
    
    GroupSources(include .)
    GroupSources(sources .)
    GroupSources(utils .)
    
    if (WITH_TESTS)
        source_group(tests REGULAR_EXPRESSION ".*/tests/.*")
        source_group(extern/catch2 FILES ${CATCH2_ROOT}/extras/catch_amalgamated.cpp)
        source_group(extern/catch2 FILES ${CATCH2_ROOT}/extras/catch_amalgamated.hpp)
    endif()

endif()

# Directories to be registered as 'include' for the project
include_directories(
    "extern"
    "include"
	"utils"
    ${BOOST_LIBDIR}/include
)

if (WITH_TESTS)
    include_directories(${CATCH2_ROOT}/extras)

    file(GLOB TESTS_HEADERS 
        "tests/*.hpp"
        "${CATCH2_ROOT}/extras/catch_amalgamated.hpp"
    )

    file(GLOB TESTS_SOURCES 
        "tests/*.cpp"
        "${CATCH2_ROOT}/extras/catch_amalgamated.cpp"
    )
endif()

file(GLOB HEADERS 
    "include/*.h"
    "include/*.hpp"
    "utils/*.h"
    "extern/cppzmq/*.hpp"
)

file(GLOB SOURCES 
    "sources/*.cpp"
    "include/*.hpp"
)

use_zmq(${ZMQ_LIBDIR})

add_compile_definitions(
    BLENDER_VER="${BLENDER_VER}"
    BOOST_ALL_NO_LIB        # Allow overriding target toolset version of libs
    BOOST_USE_STATIC_LIBS   # Statically link to Boost libraries
)

if(WIN32 AND CMAKE_C_COMPILER_ID MATCHES "MSVC")
    # Disable warnings on external includes
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /external:W0 /external:anglebrackets /W4 /WX")

	set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "${CMAKE_CXX_FLAGS_RELWITHDEBINFO} /D\"VASSERT_ENABLED\"")
endif()


add_library( ${PROJECT_NAME} STATIC "${SOURCES};${HEADERS}" )
link_with_zmq(${PROJECT_NAME})

if(VRAY_ZMQ_WRAPPER_INSTALL_PREFIX)
    # using "\" causes errors in some cases
    string(REPLACE "\\" "/" VRAY_ZMQ_WRAPPER_INSTALL_PREFIX ${VRAY_ZMQ_WRAPPER_INSTALL_PREFIX})
    set(INSTALL_PREFIX "${VRAY_ZMQ_WRAPPER_INSTALL_PREFIX}/VRayZmqWrapper/")

    message(STATUS "   Install to: ${INSTALL_PREFIX}")

    install(TARGETS ${PROJECT_NAME} ARCHIVE DESTINATION "${INSTALL_PREFIX}/lib")
    install(DIRECTORY  include/ DESTINATION "${INSTALL_PREFIX}/include")
    install(DIRECTORY  utils/ DESTINATION "${INSTALL_PREFIX}/utils")
    install(FILES  extern/cppzmq/zmq.hpp extern/cppzmq/zmq_addon.hpp DESTINATION "${INSTALL_PREFIX}/include/cppzmq")
endif()

if (WITH_TESTS)
    add_executable("${PROJECT_NAME}Tests" "${HEADERS};${TESTS_SOURCES};${TESTS_HEADERS}")
    target_link_libraries("${PROJECT_NAME}Tests" ${PROJECT_NAME})

    # Add a test executable
    enable_testing()

    # Add a test
    add_test(NAME ZmqTest COMMAND "${PROJECT_NAME}Tests")
endif()