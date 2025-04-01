cmake_minimum_required(VERSION 3.10)
project(VRayZmqWrapper)

set(CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/../cmake")
include(vfb_macros)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

if (WITH_TESTS)
	set(CATCH2_ROOT "" CACHE PATH   "Path to Catch2 install dir")
endif()

set(LIBDIR      "../win64_vc15"   CACHE PATH   "Blender libs root")
set(ZMQ_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/../vray_for_blender_libs/${CMAKE_SYSTEM_NAME}/zmq" CACHE PATH "Path to ZMQ libs")



if (WIN32)
	# generate folder structure for Visual Studio
	source_group(extern/cppzmq REGULAR_EXPRESSION ".*/extern/cppzmq/.*\\.(cpp|hpp)")
	source_group(include REGULAR_EXPRESSION ".*/include/.*")
	
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
	${LIBDIR}
	${LIBDIR}/boost/include/
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
	"extern/cppzmq/*.hpp"
)

file(GLOB SOURCES 
	"sources/*.cpp"
	"include/*.hpp"
)

use_zmq()

add_compile_definitions(
    BOOST_ALL_NO_LIB        # Allow overriding target toolset version of libs
    BOOST_USE_STATIC_LIBS   # Statically link to Boost libraries
)

if(WIN32 AND CMAKE_C_COMPILER_ID MATCHES "MSVC")
	# Disable warnings on external includes
	set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} /external:W0 /external:anglebrackets /W4 /WX")
endif()


add_library( ${PROJECT_NAME} STATIC "${SOURCES};${HEADERS}" )
link_with_zmq(${PROJECT_NAME})

if(VRAY_ZMQ_WRAPPER_INSTALL_PREFIX)
	# using "\" causes errors in some cases
	string(REPLACE "\\" "/" VRAY_ZMQ_WRAPPER_INSTALL_PREFIX ${VRAY_ZMQ_WRAPPER_INSTALL_PREFIX})
	set(INSTALL_PREFIX "${VRAY_ZMQ_WRAPPER_INSTALL_PREFIX}/VRayZmqWrapper/")

	install(TARGETS ${PROJECT_NAME} ARCHIVE DESTINATION "${INSTALL_PREFIX}/lib")
	install(DIRECTORY  include/ DESTINATION "${INSTALL_PREFIX}/include")
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