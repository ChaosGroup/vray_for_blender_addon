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

	set(QT_LIBPATH ${_qt_root}/lib)

	include_directories(${QT_INCLUDES})
	link_directories(${QT_LIBPATH})
endmacro()


macro(link_with_qt)
	target_link_libraries(${PROJECT_NAME} Qt6Core)
	target_link_libraries(${PROJECT_NAME} Qt6Gui)
	target_link_libraries(${PROJECT_NAME} Qt6Widgets)
	target_link_libraries(${PROJECT_NAME} Qt6WebChannel)
	target_link_libraries(${PROJECT_NAME} Qt6WebEngineCore)
	target_link_libraries(${PROJECT_NAME} Qt6WebEngineWidgets)
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
		target_link_libraries(${PROJECT_NAME} ${visibility} libzmq.a)
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
	if (WIN32)
		link_directories(${_appsdk_root}/cpp/lib)
	endif()

endmacro()

macro(use_vraysdk _appsdk_root _sdk_root)
	include_directories(${_appsdk_root}/vraysdk/include/)
	include_directories(${GALAXY_CONTENT_API_ROOT}/include)

	target_link_directories(${PROJECT_NAME} PRIVATE ${GALAXY_CONTENT_API_ROOT})
	target_link_directories(${PROJECT_NAME} PRIVATE ${ZLIB_ROOT})
	target_link_directories(${PROJECT_NAME} PRIVATE ${_appsdk_root}/vraysdk/lib)

	set(_vray_sdk_libs
		plugman_s # for PluginBase
		putils_s
		openexr_s
		vutils_s
		cosmos_client_s
		collaboration_common_s
		chaos_unified_login_s
		pll_s # for uuid
		vray # for valloc
		chaos_networking_201 # used by galaxy client
		galaxy_content_api_s
	)
	target_link_libraries(${PROJECT_NAME} ${_vray_sdk_libs})
	include_directories(${GRPC_ROOT}/include/)

	set(XPAK_GRPC_LIB_DIR ${GRPC_ROOT}/lib)
	if (WIN32)
		set(XPAK_GRPC_LIBS
			${XPAK_GRPC_LIB_DIR}/cares.lib
			${XPAK_GRPC_LIB_DIR}/libprotobuf.lib
			${XPAK_GRPC_LIB_DIR}/libprotoc.lib
			${XPAK_GRPC_LIB_DIR}/address_sorting.lib
			${XPAK_GRPC_LIB_DIR}/gpr.lib
			${XPAK_GRPC_LIB_DIR}/grpc.lib
			${XPAK_GRPC_LIB_DIR}/grpcpp_channelz.lib
			${XPAK_GRPC_LIB_DIR}/grpc_plugin_support.lib
			${XPAK_GRPC_LIB_DIR}/grpc_unsecure.lib
			${XPAK_GRPC_LIB_DIR}/grpc++.lib
			${XPAK_GRPC_LIB_DIR}/grpc++_error_details.lib
			${XPAK_GRPC_LIB_DIR}/grpc++_reflection.lib
			${XPAK_GRPC_LIB_DIR}/grpc++_unsecure.lib
			zlib_ng_s
			ws2_32.lib
		)
	elseif(UNIX)
		if (NOT APPLE)
			set(XPAK_GRPC_LIB_DIR ${GRPC_ROOT}/lib/clang-gcc-11.2)
		endif()
		set(XPAK_GRPC_LIBS
			${XPAK_GRPC_LIB_DIR}/libcares.a
			${XPAK_GRPC_LIB_DIR}/libprotobuf.a
			${XPAK_GRPC_LIB_DIR}/libprotoc.a
			${XPAK_GRPC_LIB_DIR}/libaddress_sorting.a
			${XPAK_GRPC_LIB_DIR}/libgpr.a
			${XPAK_GRPC_LIB_DIR}/libgrpc.a
			${XPAK_GRPC_LIB_DIR}/libgrpcpp_channelz.a
			${XPAK_GRPC_LIB_DIR}/libgrpc_plugin_support.a
			${XPAK_GRPC_LIB_DIR}/libgrpc_unsecure.a
			${XPAK_GRPC_LIB_DIR}/libgrpc++.a
			${XPAK_GRPC_LIB_DIR}/libgrpc++_error_details.a
			${XPAK_GRPC_LIB_DIR}/libgrpc++_reflection.a
			${XPAK_GRPC_LIB_DIR}/libgrpc++_unsecure.a
		)
		if (APPLE)
			list(APPEND XPAK_GRPC_LIBS ${XPAK_GRPC_LIB_DIR}/libz.a)
			list(APPEND XPAK_GRPC_LIBS
				${XPAK_GRPC_LIB_DIR}/libabsl_bad_any_cast_impl.a
				${XPAK_GRPC_LIB_DIR}/libabsl_bad_optional_access.a
				${XPAK_GRPC_LIB_DIR}/libabsl_bad_variant_access.a
				${XPAK_GRPC_LIB_DIR}/libabsl_base.a
				${XPAK_GRPC_LIB_DIR}/libabsl_city.a
				${XPAK_GRPC_LIB_DIR}/libabsl_civil_time.a
				${XPAK_GRPC_LIB_DIR}/libabsl_cord.a
				${XPAK_GRPC_LIB_DIR}/libabsl_debugging_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_demangle_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_examine_stack.a
				${XPAK_GRPC_LIB_DIR}/libabsl_exponential_biased.a
				${XPAK_GRPC_LIB_DIR}/libabsl_failure_signal_handler.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_commandlineflag.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_commandlineflag_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_config.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_marshalling.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_parse.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_private_handle_accessor.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_program_name.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_reflection.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_usage.a
				${XPAK_GRPC_LIB_DIR}/libabsl_flags_usage_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_graphcycles_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_hash.a
				${XPAK_GRPC_LIB_DIR}/libabsl_hashtablez_sampler.a
				${XPAK_GRPC_LIB_DIR}/libabsl_int128.a
				${XPAK_GRPC_LIB_DIR}/libabsl_leak_check.a
				${XPAK_GRPC_LIB_DIR}/libabsl_leak_check_disable.a
				${XPAK_GRPC_LIB_DIR}/libabsl_log_severity.a
				${XPAK_GRPC_LIB_DIR}/libabsl_malloc_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_periodic_sampler.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_distributions.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_distribution_test_util.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_platform.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_pool_urbg.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_randen.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_randen_hwaes.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_randen_hwaes_impl.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_randen_slow.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_internal_seed_material.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_seed_gen_exception.a
				${XPAK_GRPC_LIB_DIR}/libabsl_random_seed_sequences.a
				${XPAK_GRPC_LIB_DIR}/libabsl_raw_hash_set.a
				${XPAK_GRPC_LIB_DIR}/libabsl_raw_logging_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_scoped_set_env.a
				${XPAK_GRPC_LIB_DIR}/libabsl_spinlock_wait.a
				${XPAK_GRPC_LIB_DIR}/libabsl_stacktrace.a
				${XPAK_GRPC_LIB_DIR}/libabsl_status.a
				${XPAK_GRPC_LIB_DIR}/libabsl_statusor.a
				${XPAK_GRPC_LIB_DIR}/libabsl_str_format_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_strerror.a
				${XPAK_GRPC_LIB_DIR}/libabsl_strings.a
				${XPAK_GRPC_LIB_DIR}/libabsl_strings_internal.a
				${XPAK_GRPC_LIB_DIR}/libabsl_symbolize.a
				${XPAK_GRPC_LIB_DIR}/libabsl_synchronization.a
				${XPAK_GRPC_LIB_DIR}/libabsl_throw_delegate.a
				${XPAK_GRPC_LIB_DIR}/libabsl_time.a
				${XPAK_GRPC_LIB_DIR}/libabsl_time_zone.a
				${XPAK_GRPC_LIB_DIR}/libabsl_wyhash.a
				${XPAK_GRPC_LIB_DIR}/libcrypto.a
				${XPAK_GRPC_LIB_DIR}/libgrpc++_alts.a
				${XPAK_GRPC_LIB_DIR}/libre2.a
				${XPAK_GRPC_LIB_DIR}/libssl.a
				${XPAK_GRPC_LIB_DIR}/libupb.a
			)
		else() # linux
			list(APPEND XPAK_GRPC_LIBS zlib_ng_s)
		endif()
	endif()
	target_link_libraries(${PROJECT_NAME} ${XPAK_GRPC_LIBS})

	if (APPLE)
		set (SYSTEM_LIBS "-framework CoreServices" "-framework Foundation" "-framework IOKit" "-framework ApplicationServices" "-framework Cocoa" "-framework Carbon")
		target_link_libraries(${PROJECT_NAME} ${SYSTEM_LIBS})
	endif()
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
	set(BUILD_TYPES Debug Release RelWithDebInfo)
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
