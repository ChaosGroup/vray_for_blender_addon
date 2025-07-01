cmake_minimum_required(VERSION 3.21 FATAL_ERROR)


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


if(PROJECT_IS_TOP_LEVEL)
    set(BLENDER_VER      ""  CACHE STRING  "Blender version in format major.minor, e.g. 4.2")
    set(INSTALL_LOCAL    ON  CACHE BOOL    "Configure Install step")
    set(INSTALL_PREFIX   ""  CACHE PATH    "Path to the installation folder")
endif()

include(common_macros)

if (NOT BINTOOLS_PATH)
    set(BOOST_LIBDIR    "./boost"               CACHE PATH    "Path to Boost libs")
    set(ZMQ_LIBDIR      "./zmq"                 CACHE PATH    "Path to ZMQ libs")
    set(BLENDER_SDK_ROOT "./blender_sdk"        CACHE PATH    "Path to libs shipped with Blender")
else()
    include(xpak)
    include(xpak_versions)
    xpak_work_dir()
    install_xpak(PAK ${BLENDER_SDK_XPAK} VERSION ${BLENDER_SDK_XPAK_VERSION})

    set(SDK_ROOT        "${${BLENDER_SDK_XPAK}_ROOT}"     CACHE PATH    "Path to addon SDK")
    set(BOOST_LIBDIR    "${SDK_ROOT}/boost"               CACHE PATH    "Path to Boost libs")
    set(ZMQ_LIBDIR      "${SDK_ROOT}/zmq"                 CACHE PATH    "Path to ZMQ libs")
    set(BLENDER_SDK_ROOT "${SDK_ROOT}/blender_sdk"        CACHE PATH    "Path to libs shipped with Blender")
endif()

set_build_configurations()

if(PROJECT_IS_TOP_LEVEL)
    set(VRAY_ZMQ_WRAPPER_ROOT           "${CMAKE_BINARY_DIR}/VRayZmqWrapper" CACHE PATH "Path to ZMQ wrapper library install location")
    set(VRAY_ZMQ_WRAPPER_INCLUDE        "${VRAY_ZMQ_WRAPPER_ROOT}")
     set(VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE "${VRAY_ZMQ_WRAPPER_ROOT}")
     set(VRAY_ZMQ_WRAPPER_LIBS           "${VRAY_ZMQ_WRAPPER_ROOT}/lib/")
endif()

fix_separators(INSTALL_PREFIX)
fix_separators(SDK_ROOT)
fix_separators(BOOST_LIBDIR)
fix_separators(ZMQ_LIBDIR)
fix_separators(VRAY_ZMQ_WRAPPER_ROOT)
fix_separators(VRAY_ZMQ_WRAPPER_INCLUDE)
fix_separators(VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE)
fix_separators(VRAY_ZMQ_WRAPPER_LIBS)


message(STATUS "   Dependencies")
message(STATUS "   -------------")
message(STATUS "   Addon SDK root: \"${SDK_ROOT}\"")
message(STATUS "   Blener SDK root: \"${BLENDER_SDK_ROOT}\"")
message(STATUS "   Boost root: \"${BOOST_LIBDIR}\"")
message(STATUS "   ZMQ root: \"${ZMQ_LIBDIR}\"")


if (${INSTALL_LOCAL} AND NOT EXISTS ${INSTALL_PREFIX})
    message(FATAL_ERROR "Folder referenced by INSTALL_PREFIX '${INSTALL_PREFIX}' is missing, but is required by option INSTALL_LOCAL")
endif()


# Organize the source files by folder in the IDE
GroupSources(api .)
GroupSources(export .)
GroupSources(utils .)


include_directories(.)
include_directories(utils)
include_directories(${VRAY_ZMQ_WRAPPER_INCLUDE}/include/)
include_directories(${VRAY_ZMQ_WRAPPER_INCLUDE}/utils/)
include_directories(${VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE}/)
include_directories(${BOOST_LIBDIR}/include)
include_directories(${BLENDER_SDK_ROOT}/jpeg/include/)
include_directories(${BLENDER_SDK_ROOT}/imath/include/)
include_directories(${BLENDER_SDK_ROOT}/osl/include/)
include_directories(${BLENDER_SDK_ROOT}/OpenImageIO/include/)
include_directories(${BLENDER_SDK_ROOT}/python/311/include/)

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
    "${VRAY_ZMQ_WRAPPER_ROOT}/include/*.h"
    "${VRAY_ZMQ_WRAPPER_ROOT}/include/*.hpp"
)

use_zmq(${ZMQ_LIBDIR})

if(WIN32 AND CMAKE_C_COMPILER_ID MATCHES "MSVC")
    # set(CMAKE_GENERATOR_TOOLSET "v142")

    # Disable warnings on external includes
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /external:W0 /external:anglebrackets /W4 /WX")
    # Enable multiprocess compilation
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /MP /GR-")

    set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} /O2 /Ob2 /Oi /Ot ")
    set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "${CMAKE_CXX_FLAGS_RELWITHDEBINFO} /Ob0 /Od /D\"VASSERT_ENABLED\"")
endif()


if(WIN32)
    add_compile_definitions(
        WIN32_LEAN_AND_MEAN 
        NOMINMAX
        NOGDI
    )
    
    if(CMAKE_C_COMPILER_ID MATCHES "MSVC")
        add_link_options(
            "/IGNORE:4217" # WARNING: __declspec(dllimport) was specified for a symbol even though the symbol is defined in an object file in the same image.
        )
    endif()

endif()

add_compile_definitions(
    BLENDER_VER="${BLENDER_VER}"
    BOOST_ALL_NO_LIB        # Allow overriding target toolset version of libs
    BOOST_USE_STATIC_LIBS   # Statically link to Boost libraries
)

add_library(${PROJECT_NAME} SHARED "${SOURCES};${HEADERS}")

if(WIN32)
    set_target_properties(${PROJECT_NAME} PROPERTIES
                            PREFIX ""
                            SUFFIX ".pyd" )
endif()

link_with_zmq(${PROJECT_NAME} PRIVATE)

if(WIN32)

    if(BLENDER_VER STREQUAL "4.2" OR  BLENDER_VER STREQUAL "4.3")
        # For these versions, the lib files for OSL are not matched with the dlls provided in the SDK. This manifest will force
        # the correct versions to be loaded from the addon folder at runtime.
        target_sources(${PROJECT_NAME} PRIVATE ${CMAKE_CURRENT_LIST_DIR}/VRayBlenderLib.manifest)
    endif()

    target_link_directories( ${PROJECT_NAME} PRIVATE
        "${BOOST_LIBDIR}/lib" 
        "${BLENDER_SDK_ROOT}/jpeg/lib"
        "${BLENDER_SDK_ROOT}/imath/lib"
        "${BLENDER_SDK_ROOT}/OpenImageIO/lib" 
        "${BLENDER_SDK_ROOT}/osl/lib" 
        "${BLENDER_SDK_ROOT}/python/311/libs"
        "${VRAY_ZMQ_WRAPPER_LIBS}"
    )
        
    # Windows SDK
    list(APPEND LIB_WIN32 "version.lib")

    list(APPEND LIB_WIN32 "boost_filesystem-vc142-mt-x64-1_82.lib")
    list(APPEND LIB_WIN32 "boost_python311-vc142-mt-x64-1_82.lib")
    list(APPEND LIB_WIN32 "boost_numpy311-vc142-mt-x64-1_82.lib")
    list(APPEND LIB_WIN32 "python311.lib")
    list(APPEND LIB_WIN32 "OpenImageIO_Util.lib")
    list(APPEND LIB_WIN32 "oslquery.lib" "oslcomp.lib")
    list(APPEND LIB_WIN32 "VRayZmqWrapper.lib")
    list(APPEND LIB_WIN32 "libjpeg.lib")
    
    target_link_libraries(${PROJECT_NAME} PRIVATE ${LIB_WIN32})
endif()
    

if (INSTALL_LOCAL)

    if (WIN32)
        if (BLENDER_VER STREQUAL "4.4")
            set (COPY_LIBS 
                "${BOOST_LIBDIR}/lib/boost_filesystem-vc142-mt-x64-1_82.dll"
                "${BOOST_LIBDIR}/lib/boost_python311-vc142-mt-x64-1_82.dll"
                "${BOOST_LIBDIR}/lib/boost_numpy311-vc142-mt-x64-1_82.dll"
            )
        else()
            set (COPY_LIBS
                "${BLENDER_SDK_ROOT}/osl/bin/oslcomp.dll"
                "${BLENDER_SDK_ROOT}/osl/bin/oslquery.dll"
            )
        endif()


        set(COPY_TOOLS
            ${SDK_ROOT}/tools/vrmatconvert.exe
        )

        set(REMOVE_INSTALL_FILES
            ${COPY_LIBS}
            ${COPY_TOOLS}
        )
    
    endif()

    set(CMAKE_INSTALL_PREFIX "${CMAKE_CURRENT_LIST_DIR}")
    set(INSTALL_PREFIX "${INSTALL_PREFIX}/")

    # Copy the newly built files
    message(STATUS "   Install to: ${INSTALL_PREFIX}")

    install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION ${INSTALL_PREFIX})
    install(FILES ${COPY_LIBS} DESTINATION ${INSTALL_PREFIX})
    install(FILES ${COPY_TOOLS} DESTINATION ${INSTALL_PREFIX})

    # set(STUB_FILE ${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/${PROJECT_NAME})
    # set(STUBGEN_COMMAND "cd ${CMAKE_RUNTIME_OUTPUT_DIRECTORY} && stubgen -m ${PROJECT_NAME} -o ${CMAKE_BINARY_DIR}")

    # install(CODE
    #     "execute_process(COMMAND echo HELLO)"
    # )

endif()

