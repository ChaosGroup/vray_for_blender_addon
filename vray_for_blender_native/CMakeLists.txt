cmake_minimum_required(VERSION 3.0.0 FATAL_ERROR)

# This policy should be enabled in order to be used IN_LIST
# https://cmake.org/cmake/help/v3.7/policy/CMP0057.html
cmake_policy(SET CMP0057 NEW)

project(VRayBlenderLib)

# Limit build configurations to a selected set
set(BUILD_TYPES Release RelWithDebInfo)
get_property(multi_config GLOBAL PROPERTY GENERATOR_IS_MULTI_CONFIG)
if(multi_config)
  set(CMAKE_CONFIGURATION_TYPES "${BUILD_TYPES}" CACHE STRING "list of supported configuration types" FORCE)
else()
  set(CMAKE_BUILD_TYPE "Release" CACHE STRING "Build Type of the project.")
  set_property(CACHE CMAKE_BUILD_TYPE PROPERTY STRINGS "${BUILD_TYPES}")
  if(NOT CMAKE_BUILD_TYPE IN_LIST BUILD_TYPES)
    message(FATAL_ERROR "Invalid build type '${CMAKE_BUILD_TYPE}'. Possible values:\n ${BUILD_TYPES}")
  endif()
endif()


set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/cmake")
include(macros)


set(CMAKE_POSITION_INDEPENDENT_CODE ON)
set(LIBDIR                 	  "../win64_vc15"      CACHE PATH   "Custom libs root - zmq")
set(ZMQ_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../vray_for_blender_libs/${CMAKE_SYSTEM_NAME}/zmq" CACHE PATH "Path to ZMQ libs")
set(INSTALL_LOCAL             ON                   CACHE BOOL   "Do Install step")
set(VRAY_ZMQ_WRAPPER_ROOT     "${CMAKE_BINARY_DIR}/VRayZmqWrapper"  CACHE STRING "Path to ZMQ wrapper library install location")

if (${INSTALL_LOCAL} AND NOT EXISTS ${INSTALL_PREFIX})
	message(FATAL_ERROR "Folder referenced by INSTALL_PREFIX is missing, but is required by option INSTALL_LOCAL")
endif()

# using "\" causes errors in some cases
string(REPLACE "\\" "/" LIBDIR ${LIBDIR})
string(REPLACE "\\" "/" INSTALL_LOCAL ${INSTALL_LOCAL})
string(REPLACE "\\" "/" VRAY_ZMQ_WRAPPER_ROOT ${VRAY_ZMQ_WRAPPER_ROOT})

if(NOT VRAY_ZMQ_WRAPPER_INCLUDE)
	set(VRAY_ZMQ_WRAPPER_INCLUDE "${VRAY_ZMQ_WRAPPER_ROOT}" CACHE PATH "Path to V-Ray ZMQ wrapper headers")
endif()
if(NOT VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE)
	set(VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE "${VRAY_ZMQ_WRAPPER_ROOT}" CACHE PATH "Path to ZMQ headers")
endif()
if(NOT VRAY_ZMQ_WRAPPER_LIBS)
	set(VRAY_ZMQ_WRAPPER_LIBS "${VRAY_ZMQ_WRAPPER_ROOT}/lib/" CACHE PATH "Path to ZMQ libs")
endif()


include_directories(.)
include_directories(${VRAY_ZMQ_WRAPPER_INCLUDE}/include/)
include_directories(${VRAY_ZMQ_WRAPPER_EXTERN_INCLUDE}/)
include_directories(${LIBDIR}/boost/include/)
include_directories(${LIBDIR}/jpeg/include/)
include_directories(${LIBDIR}/imath/include/)
include_directories(${LIBDIR}/osl/include/)
include_directories(${LIBDIR}/OpenImageIO/include/)
include_directories(${LIBDIR}/python/311/include/)

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

use_zmq()

set(CMAKE_CXX_STANDARD 20)

if(WIN32 AND CMAKE_C_COMPILER_ID MATCHES "MSVC")
    # set(CMAKE_GENERATOR_TOOLSET "v142")

	# Disable warnings on external includes
	set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /external:W0 /external:anglebrackets /W4 /WX")
	# Enable multiprocess compilation
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /MP /GR-")

    set(CMAKE_CXX_FLAGS_RELEASE "${CMAKE_CXX_FLAGS_RELEASE} /O2 /Ob2 /Oi /Ot ")
    set(CMAKE_CXX_FLAGS_RELWITHDEBINFO "${CMAKE_CXX_FLAGS_RELWITHDEBINFO} /Ob0 /Od /D\"WITH_ASSERTS\"")
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
    BOOST_ALL_NO_LIB        # Allow overriding target toolset version of libs
    BOOST_USE_STATIC_LIBS   # Statically link to Boost libraries
)

add_library(${PROJECT_NAME} SHARED "${SOURCES};${HEADERS}")

if(WIN32)
    set_target_properties(${PROJECT_NAME} PROPERTIES
                            PREFIX ""
                            SUFFIX ".pyd" )
endif()


link_with_zmq(${PROJECT_NAME})

if(WIN32)
	target_sources(${PROJECT_NAME} PRIVATE ${CMAKE_CURRENT_LIST_DIR}/VRayBlenderLib.manifest)

    target_link_directories( ${PROJECT_NAME} PRIVATE
        "${LIBDIR}/boost/lib" 
        "${LIBDIR}/jpeg/lib"
        "${LIBDIR}/imath/lib"
        "${LIBDIR}/OpenImageIO/lib" 
        "${LIBDIR}/osl/lib" 
        "${LIBDIR}/python/311/libs"
        "${VRAY_ZMQ_WRAPPER_LIBS}"
    )
        
    # Windows SDK
    list(APPEND LIB_WIN32 "version.lib")

    list(APPEND LIB_WIN32 "${LIBDIR}/boost/lib/boost_filesystem-vc142-mt-x64-1_82.lib")
    list(APPEND LIB_WIN32 "${LIBDIR}/boost/lib/boost_python311-vc142-mt-x64-1_82.lib")
    list(APPEND LIB_WIN32 "${LIBDIR}/boost/lib/boost_numpy311-vc142-mt-x64-1_82.lib")
    list(APPEND LIB_WIN32 "${LIBDIR}/python/311/libs/python311.lib")
    list(APPEND LIB_WIN32 "OpenImageIO_Util.lib")
    list(APPEND LIB_WIN32 "oslquery.lib" "oslcomp.lib")
    list(APPEND LIB_WIN32 "VRayZmqWrapper.lib")
    list(APPEND LIB_WIN32 "libjpeg.lib")
    
    target_link_libraries(${PROJECT_NAME} PRIVATE ${LIB_WIN32})
endif()
    

if (INSTALL_LOCAL)
    set(CMAKE_INSTALL_PREFIX "${CMAKE_CURRENT_LIST_DIR}")
    set(INSTALL_PREFIX "${INSTALL_PREFIX}/")
    install(TARGETS ${PROJECT_NAME} RUNTIME DESTINATION ${INSTALL_PREFIX})
    install(FILES
        "${LIBDIR}/osl/bin/oslcomp.dll"
        "${LIBDIR}/osl/bin/oslquery.dll"

        DESTINATION ${INSTALL_PREFIX}
    )

    # set(STUB_FILE ${CMAKE_RUNTIME_OUTPUT_DIRECTORY}/${PROJECT_NAME})
    # set(STUBGEN_COMMAND "cd ${CMAKE_RUNTIME_OUTPUT_DIRECTORY} && stubgen -m ${PROJECT_NAME} -o ${CMAKE_BINARY_DIR}")

    # install(CODE
    #     "execute_process(COMMAND echo HELLO)"
    # )

endif()

