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

if (APPLE)
    message(STATUS "   Architecture: ${CMAKE_OSX_ARCHITECTURES}")
endif()

if(PROJECT_IS_TOP_LEVEL)
    set(BLENDER_VER      ""  CACHE STRING  "Blender version in format major.minor, e.g. 4.2")
    set(INSTALL_LOCAL    ON  CACHE BOOL    "Configure Install step")
    set(INSTALL_PREFIX   ""  CACHE PATH    "Path to the installation folder")
    set(WITH_DR2         OFF CACHE BOOL    "Enable DR2")
endif()

if (WITH_DR2)
    add_compile_definitions(WITH_DR2)
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

fix_separators(BOOST_LIBDIR)
fix_separators(ZMQ_LIBDIR)
fix_separators(BLENDER_SDK_ROOT)

message(STATUS "   Dependencies")
message(STATUS "   -------------")
message(STATUS "   Blener SDK root: \"${BLENDER_SDK_ROOT}\"")
message(STATUS "   Boost root: \"${BOOST_LIBDIR}\"")
message(STATUS "   ZMQ root: \"${ZMQ_LIBDIR}\"")

# Organize the source files by folder in the IDE
GroupSources(api .)
GroupSources(export .)
GroupSources(utils .)

include_directories(.)
include_directories(utils)
include_directories(${VRAY_ZMQ_WRAPPER_ROOT}/include/)
include_directories(${VRAY_ZMQ_WRAPPER_ROOT}/utils/)
include_directories(${VRAY_ZMQ_WRAPPER_ROOT}/extern/)
include_directories(${BOOST_LIBDIR}/include)
include_directories(${BLENDER_SDK_ROOT}/jpeg/include/)
include_directories(${BLENDER_SDK_ROOT}/imath/include/)
include_directories(${BLENDER_SDK_ROOT}/osl/include/)
include_directories(${BLENDER_SDK_ROOT}/OpenImageIO/include/)

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

    set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} /O2 /Ob2 /Oi /Ot /Zi")
    set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "${CMAKE_CXX_FLAGS_RELWITHDEBINFO} /Ob0 /Od /D\"VASSERT_ENABLED\"")
else()
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -Werror")
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
elseif(APPLE)
    set_target_properties(${PROJECT_NAME} PROPERTIES
        PREFIX ""
        SUFFIX ".so"
    )
endif()

link_with_zmq(${PROJECT_NAME} PRIVATE)

if(WIN32)
    if(BLENDER_VER STREQUAL "4.2" OR  BLENDER_VER STREQUAL "4.3")
        # For these versions, the lib files for OSL are not matched with the dlls provided in the SDK. This manifest will force
        # the correct versions to be loaded from the addon folder at runtime.
        target_sources(${PROJECT_NAME} PRIVATE ${CMAKE_CURRENT_LIST_DIR}/VRayBlenderLib.manifest)
    endif()

    include_directories(${BLENDER_SDK_ROOT}/python/311/include/)
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

    if (MSVC)
        target_link_options(${PROJECT_NAME} PRIVATE 
            "$<$<CONFIG:Release>:/DEBUG:FULL>"
            "$<$<CONFIG:Release>:/OPT:REF>"
            "$<$<CONFIG:Release>:/OPT:ICF>"
        )
    endif()

elseif (APPLE)
    add_compile_options(-Wno-enum-constexpr-conversion)

    include_directories(${BLENDER_SDK_ROOT}/python/include/python3.11)
    target_link_directories( ${PROJECT_NAME} PRIVATE
        "${BOOST_LIBDIR}/lib"
        "${BLENDER_SDK_ROOT}/jpeg/lib"
        "${BLENDER_SDK_ROOT}/imath/lib"
        "${BLENDER_SDK_ROOT}/openimageio/lib"
        "${BLENDER_SDK_ROOT}/osl/lib"
        "${VRAY_ZMQ_WRAPPER_LIBS}"
    )

    list(APPEND LIB_APPLE libboost_filesystem.dylib)
    # Link only with libboost_python, for some reason when linking blender's python lib nothing works.
    list(APPEND LIB_APPLE libboost_python311.dylib)
    list(APPEND LIB_APPLE libboost_numpy311.dylib)
    list(APPEND LIB_APPLE libboost_atomic.dylib)
    list(APPEND LIB_APPLE libOpenImageIO.dylib)
    list(APPEND LIB_APPLE libOpenImageIO_Util.dylib)
    list(APPEND LIB_APPLE liboslquery.dylib liboslcomp.dylib liboslexec.dylib liboslnoise.dylib)
    list(APPEND LIB_APPLE libVRayZmqWrapper.a)
    list(APPEND LIB_APPLE libjpeg.a)

    target_link_libraries(${PROJECT_NAME} PRIVATE ${LIB_APPLE})
    target_link_options(${PROJECT_NAME} PRIVATE "-undefined" "dynamic_lookup")

    set_target_properties(${PROJECT_NAME} PROPERTIES
        INSTALL_RPATH "@loader_path"
        BUILD_WITH_INSTALL_RPATH ON
    )
endif()

if (INSTALL_LOCAL)
    if (WIN32)
        if (BLENDER_VER VERSION_GREATER_EQUAL "4.4")
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
    elseif(APPLE)
        if (BLENDER_VER VERSION_GREATER_EQUAL "4.4")
            set (COPY_LIBS
                "${BOOST_LIBDIR}/lib/libboost_filesystem.dylib"
                "${BOOST_LIBDIR}/lib/libboost_python311.dylib"
                "${BOOST_LIBDIR}/lib/libboost_numpy311.dylib"
                "${BOOST_LIBDIR}/lib/libboost_atomic.dylib"
            )
        else()
            set (COPY_LIBS
                "${BLENDER_SDK_ROOT}/osl/lib/liboslcomp.dylib"
                "${BLENDER_SDK_ROOT}/osl/lib/liboslquery.dylib"
            )
        endif()
    endif()

    set(CMAKE_INSTALL_PREFIX "${CMAKE_CURRENT_LIST_DIR}")

    if(VRAY_LIB_INSTALL_PREFIX)
        set(INSTALL_PREFIX "${VRAY_LIB_INSTALL_PREFIX}")
    endif()

    if(NOT EXISTS "${INSTALL_PREFIX}")
        file(MAKE_DIRECTORY "${INSTALL_PREFIX}")
    endif()

    # Copy the newly built files
    message(STATUS "   Install to: ${INSTALL_PREFIX}")

    if (WIN32)
        install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION ${INSTALL_PREFIX})
    else()
        install(TARGETS ${PROJECT_NAME} LIBRARY DESTINATION ${INSTALL_PREFIX})
    endif()
    install(FILES ${COPY_LIBS} DESTINATION ${INSTALL_PREFIX})
    install(FILES ${COPY_TOOLS} DESTINATION ${INSTALL_PREFIX})

    if(MSVC)
      # Install the PDB file
      install(FILES $<TARGET_PDB_FILE:${PROJECT_NAME}>
          DESTINATION ${INSTALL_PREFIX}
          CONFIGURATIONS Release RelWithDebInfo
      )
    endif()

    # set(STUB_FILE ${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/${PROJECT_NAME})
    # set(STUBGEN_COMMAND "cd ${CMAKE_RUNTIME_OUTPUT_DIRECTORY} && stubgen -m ${PROJECT_NAME} -o ${CMAKE_BINARY_DIR}")

    # install(CODE
    #     "execute_process(COMMAND echo HELLO)"
    # )


endif()

