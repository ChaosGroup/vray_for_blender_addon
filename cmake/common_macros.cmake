#
# Copyright (c) 2015, Chaos Software Ltd
#
# V-Ray Application SDK For C++
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#


if(WIN32)
	SET(CMAKE_FIND_LIBRARY_SUFFIXES ".lib" ".dll")
endif()

macro(use_qt _qt_root)
	if (NOT EXISTS ${_qt_root})
		message(FATAL_ERROR "Could not find QT: \"${_qt_root}\"")
	endif()

	set(QT_INCLUDES
		${_qt_root}/include
		${_qt_root}/include/QtCore
		${_qt_root}/include/QtWidgets
		${_qt_root}/include/QtGui
		${_qt_root}/include/QtWebEngineWidgets
		${_qt_root}/include/QtWebEngineCore
	)

	if (WIN32)
		set(QT_LIBPATH ${_qt_root}/lib)
		set(QT_LIB_EXT ".lib")
		set(QT_LIB_PREFIX "")
	else()
		if (APPLE)
			set(QT_LIBPATH ${_qt_root}/lib)
			set(QT_LIB_EXT ".dylib")
			set(QT_LIB_PREFIX "lib")
		else()
			set(QT_LIBPATH ${_qt_root}/lib)
			set(QT_LIB_EXT ".so")
			set(QT_LIB_PREFIX "lib")
		endif()
	endif()

	include_directories(${QT_INCLUDES})
	link_directories(${QT_LIBPATH})
endmacro()


macro(link_with_qt)
	if (UNIX AND NOT APPLE)
		# target_link_libraries(${PROJECT_NAME} libicui18n.so)
		# target_link_libraries(${PROJECT_NAME} libicuuc.so)
		# target_link_libraries(${PROJECT_NAME} libicudata.so)
	endif()

	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6Core${QT_LIB_EXT})
	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6Gui${QT_LIB_EXT})
	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6Widgets${QT_LIB_EXT})
	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6WebChannel${QT_LIB_EXT})
	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6WebEngineCore${QT_LIB_EXT})
	target_link_libraries(${PROJECT_NAME} ${QT_LIB_PREFIX}Qt6WebEngineWidgets${QT_LIB_EXT})
endmacro()


# Generate moc file. Add source dependencies, so that moc file will be automatically regenerated
#	when source file changes.
# param TARGET: logical target name
# param FILE_IN: input h/cpp filespec (must be present in target SOURCES)
# param FILE_OUT_NAME: name of the output moc
# param DEFINITIONS: Additional definitions (optional)
#
function(cgr_moc)
	cmake_parse_arguments(PAR "" "MOC_COMPILER;TARGET;FILE_IN;FILE_OUT_NAME;FILE_OUT_VAR" "DEFINITIONS" ${ARGN})

	set(_moc_compiler ${PAR_MOC_COMPILER})
	if(NOT _moc_compiler)
		if (NOT MOC_COMPILER)
			message(FATAL_ERROR "cgr_moc() MOC_COMPILER is not set!")
		endif()
	endif()

	if(PAR_UNPARSED_ARGUMENTS)
		message(FATAL_ERROR "cgr_moc() invalid arguments: ${PAR_UNPARSED_ARGUMENTS}")
	endif()

	# Unix Makefiles generator will not automagically create the output dir needed
	# by the custom command, so do it manually
	set(FILE_OUT_DIR ${CMAKE_CURRENT_BINARY_DIR}/moc_output)
	file(MAKE_DIRECTORY ${FILE_OUT_DIR})

	# Execute moc on file changes
	add_custom_command(
		OUTPUT ${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME}
		COMMAND ${_moc_compiler} ${PAR_DEFINITIONS} -o${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME} ${PAR_FILE_IN} 
		DEPENDS	${PAR_FILE_IN}
		COMMENT	"Using ${_moc_compiler} to compile ${PAR_FILE_IN} to ${PAR_FILE_OUT_NAME}"
		VERBATIM
	)

	# Add dependency
	set_source_files_properties(${PAR_FILE_IN} PROPERTIES OBJECT_DEPENDS ${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME})

	# For target to find output moc files
	target_include_directories(${PAR_TARGET} PRIVATE ${FILE_OUT_DIR})
endfunction()

# Generate moc file. Add source dependencies, so that moc file will be automatically regenerated
#	when source file changes.
# param TARGET: logical target name
# param FILE_IN: input qrc filespec (must be present in target SOURCES)
# param FILE_OUT_NAME: name of the output resource
# param FILE_OUT_VAR: declares the name of a variable that will receive the
#    full path to the generated file to be added to SOURCES
# param DEFINITIONS: Additional definitions (optional)
function(cgr_rcc)
	cmake_parse_arguments(PAR "" "RCC_COMPILER;TARGET;FILE_IN;FILE_OUT_NAME;FILE_OUT_VAR" "DEFINITIONS" ${ARGN})

	set(_rcc_compiler ${PAR_RCC_COMPILER})
	if(NOT _rcc_compiler)
		if (NOT RCC_COMPILER)
			message(FATAL_ERROR "cgr_rcc() RCC_COMPILER is not set!")
		endif()
	endif()

	if(PAR_UNPARSED_ARGUMENTS)
		message(FATAL_ERROR "cgr_rcc() invalid arguments: ${PAR_UNPARSED_ARGUMENTS}")
	endif()

	# Unix Makefiles generator will not automagically create the output dir needed
	# by the custom command, so do it manually
	set(FILE_OUT_DIR ${CMAKE_CURRENT_BINARY_DIR}/rcc_output)
	file(MAKE_DIRECTORY ${FILE_OUT_DIR})

	# Execute rcc on file changes
	add_custom_command(
		OUTPUT ${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME}
		COMMAND ${_rcc_compiler} ${PAR_DEFINITIONS}  ${PAR_FILE_IN} -o ${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME}
		DEPENDS	${PAR_FILE_IN}
		COMMENT	"Using ${_rcc_compiler} to compile ${PAR_FILE_IN} to ${PAR_FILE_OUT_NAME}"
		WORKING_DIRECTORY ${CMAKE_BINARY_DIR}
		VERBATIM
	)

	set_source_files_properties(${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME} GENERATED)
	
	# Add dependency
	set_source_files_properties(${PAR_FILE_IN} PROPERTIES OBJECT_DEPENDS ${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME})
	set(${PAR_FILE_OUT_VAR} "${FILE_OUT_DIR}/${PAR_FILE_OUT_NAME}" PARENT_SCOPE)

endfunction()


macro(use_zmq _zmq_root)
	if (NOT EXISTS ${_zmq_root})
		message(FATAL_ERROR "Could not find ZMQ: \"${_zmq_root}\"")
	endif()

	if (WIN32)
		#string(REPLACE "/MDd" "/MTd" CMAKE_CXX_FLAGS_DEBUG ${CMAKE_CXX_FLAGS_DEBUG})
		#string(REPLACE "/MD" "/MT" CMAKE_CXX_FLAGS_RELEASE ${CMAKE_CXX_FLAGS_RELEASE})
		#string(REPLACE "/MD" "/MT" CMAKE_CXX_FLAGS_RELWITHDEBINFO ${CMAKE_CXX_FLAGS_RELWITHDEBINFO})

		string(REPLACE "/MTd" "/MDd" CMAKE_CXX_FLAGS_DEBUG ${CMAKE_CXX_FLAGS_DEBUG})
		string(REPLACE "/MT" "/MD" CMAKE_CXX_FLAGS_RELEASE ${CMAKE_CXX_FLAGS_RELEASE})
		string(REPLACE "/MT" "/MD" CMAKE_CXX_FLAGS_RELWITHDEBINFO ${CMAKE_CXX_FLAGS_RELWITHDEBINFO})
	else()
		set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -std=c++0x -L/usr/lib64")
		link_directories(/usr/lib64)
	endif()

	add_definitions(-DZMQ_STATIC)

	link_directories(${_zmq_root}/lib)
	#link_directories(${ZMQ_ROOT}/bin)
	include_directories(${_zmq_root}/include)
endmacro()


macro(link_with_zmq _name)

	set(visibility "") # Linkage visibility
    
    # If optional argument is given to link_with_zmq() assign it to the 
	# visibility variable
	set (extra_args ${ARGN})
    list(LENGTH extra_args extra_count)
	if (${extra_count} GREATER 0)
        list(GET extra_args 0 visibility)
    endif ()

	if(UNIX)
		target_link_libraries(${PROJECT_NAME} ${visibility}
			${LIBS_ROOT}/${CMAKE_SYSTEM_NAME}/zmq/lib/Release/libzmq.a
			${LIBS_ROOT}/${CMAKE_SYSTEM_NAME}/sodium/lib/Release/libsodium.a
			)
	elseif(WIN32)
		if (MSVC_VERSION EQUAL 1800)
			set(MSVC_DIR_NAME "v120")
		elseif(MSVC_VERSION GREATER_EQUAL 1900)
			set(MSVC_DIR_NAME "v142")
		endif()
		target_link_libraries(${_name} ${visibility} optimized Release/${MSVC_DIR_NAME}/libzmq-v142-mt-s-4_3_4)
		target_link_libraries(${_name} ${visibility} wsock32 ws2_32 Iphlpapi)
	endif()
endmacro()


macro(use_vray_appsdk _appsdk_root)
	if(NOT EXISTS ${_appsdk_root})
		message(FATAL_ERROR "V-Ray AppSDK root (\"${_appsdk_root}\") doesn't exist!")
	endif()

	find_library(VRAY_APPSDK_LIB
		NAMES VRaySDKLibrary
		PATHS ${_appsdk_root}/bin ${_appsdk_root}/lib
	)

	if(NOT VRAY_APPSDK_LIB)
		message(FATAL_ERROR "V-Ray AppSDK libraries are not found! Check APPSDK_PATH variable (current search path ${_appsdk_root}/bin)")
	endif()

	add_definitions(-DVRAY_SDK_INTEROPERABILITY)

	include_directories(${_appsdk_root}/cpp/include)
	link_directories(${_appsdk_root}/bin)
	link_directories(${_appsdk_root}/cpp/lib)
endmacro()

macro(use_vraysdk _appsdk_root _sdk_root)
	include_directories(${_appsdk_root}/vraysdk/include/)
	include_directories(${_sdk_root}/galaxy_content_api/include/)
	target_link_directories(${PROJECT_NAME} PRIVATE ${_sdk_root}/galaxy_content_api)

	target_link_directories(${PROJECT_NAME} PRIVATE ${_appsdk_root}/vraysdk/lib)
	set(_vray_sdk_libs
		plugman_s # for PluginBase
		putils_s
		vutils_s
		cosmos_client_s
		pll_s # for uuid
		vray # for valloc
		chaos_networking # used by galaxy client
		galaxy_content_api_s
	)
	target_link_libraries(${PROJECT_NAME} ${_vray_sdk_libs})
	include_directories(${_sdk_root}/GRPC/include/)
	link_directories(${_sdk_root}/GRPC/lib)
	link_directories(${_sdk_root}/GRPC/bin)

	set(_grpc_lib_dir ${_sdk_root}/GRPC/lib)
	set(_grpc_libs
		${_grpc_lib_dir}/cares.lib
		${_grpc_lib_dir}/libprotobuf.lib
		${_grpc_lib_dir}/libprotoc.lib
		${_grpc_lib_dir}/address_sorting.lib
		${_grpc_lib_dir}/gpr.lib
		${_grpc_lib_dir}/grpc.lib
		${_grpc_lib_dir}/grpcpp_channelz.lib
		${_grpc_lib_dir}/grpc_plugin_support.lib
		${_grpc_lib_dir}/grpc_unsecure.lib
		${_grpc_lib_dir}/grpc++.lib
		${_grpc_lib_dir}/grpc++_error_details.lib
		${_grpc_lib_dir}/grpc++_reflection.lib
		${_grpc_lib_dir}/grpc++_unsecure.lib
		${_grpc_lib_dir}/zlib_s.lib
	)

	target_link_libraries(${PROJECT_NAME} ${_grpc_libs})
endmacro()


macro(link_with_vray_appsdk _name)
	set(APPSDK_LIBS
		VRaySDKLibrary
	)
	target_link_libraries(${_name} ${APPSDK_LIBS})
endmacro()


# Replace slashes with forward slashes in the supplied string variable, if it is defined
macro(fix_separators _path)
	if (DEFINED ${_path})
		string(REPLACE "\\" "/" _path ${_path})
	endif()
endmacro()


# Limit build configurations to a selected set
macro(set_build_configurations)
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
endmacro()


if (WIN32)
	# Generate folder structure for Visual Studio
	macro(GroupSources curdir ignore_prefix)
		file(GLOB children RELATIVE ${PROJECT_SOURCE_DIR}/${curdir} ${PROJECT_SOURCE_DIR}/${curdir}/*)
		foreach(child ${children})
			if(IS_DIRECTORY ${PROJECT_SOURCE_DIR}/${curdir}/${child})
				if (${child} STREQUAL ".git")
					# pass
				else()
					GroupSources(${curdir}/${child} ${ignore_prefix})
				endif()
			else()
				string(REPLACE "${ignore_prefix}/" "" rel_path ${curdir})
				string(REPLACE "/" "\\" groupname ${rel_path})
				source_group(${groupname} FILES ${PROJECT_SOURCE_DIR}/${curdir}/${child})
			endif()
		endforeach()
	endmacro()
else()
	macro(GroupSources curdir ignore_prefix)
	endmacro()
endif()
