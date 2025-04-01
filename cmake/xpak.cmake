set(X_PAK "" CACHE PATH "Conan work directory")
set(X_PAK_WORK "" CACHE PATH "Conan work directory override")
set(BINTOOLS_PATH "" CACHE PATH "Path to bintools")

if(APPLE)
	if("${CMAKE_APPLE_SILICON_PROCESSOR}" STREQUAL "arm64")
		set(MACOS_XPAK bigsur_univ)
		else()
		set(MACOS_XPAK mavericks_x64)
		endif()
		set(X_XPAKTOOL_EXECUTABLE ${BINTOOLS_PATH}/bintools/${MACOS_XPAK}/xpaktool)
elseif(WIN32)
	set(X_XPAKTOOL_EXECUTABLE ${BINTOOLS_PATH}/bintools/x64/xpaktool.exe)
else()
	set(X_XPAKTOOL_EXECUTABLE ${BINTOOLS_PATH}/bintools/linux_x64/xpaktool)
endif()


function(install_xpak)
	cmake_parse_arguments(ARG "" "PAK;VERSION" "" ${ARGN})
	if(ARG_UNPARSED_ARGUMENTS)
		message(FATAL_ERROR "Unrecognized options \"${ARG_UNPARSED_ARGUMENTS}\" passed to install_xpak()!")
	endif()
	if(NOT ARG_PAK)
		message(FATAL_ERROR "PAK is a required argument")
	endif()
	if(NOT ARG_VERSION)
		message(FATAL_ERROR "VERSION is a required argument")
	endif()

	if(WIN32)
		set(HOME $ENV{HOME})
		set(ENV{HOME} $ENV{USERPROFILE})
	endif()

	if(NOT EXISTS ${X_XPAKTOOL_EXECUTABLE})
		message(FATAL_ERROR "${X_XPAKTOOL_EXECUTABLE} not found!")
	endif()

	set(xpak_workdir ${X_PAK})
	if(NOT EXISTS ${xpak_workdir})
		file(MAKE_DIRECTORY ${xpak_workdir})
	endif()

	set(xpak_pak ${ARG_PAK}/${ARG_VERSION})

	message(STATUS "Using xpak ${xpak_pak} [workdir:${xpak_workdir}]")

	set(CMD ${X_XPAKTOOL_EXECUTABLE} xinstall -pak ${xpak_pak} -workdir ${xpak_workdir})

	execute_process(COMMAND ${CMD} RESULT_VARIABLE errCode)

	if(WIN32)
		set(ENV{HOME} ${HOME})
	endif()

	if(NOT "${errCode}" STREQUAL "0")
		message(FATAL_ERROR "${CMD} failed with code ${errCode}!")
	endif()

	set(${ARG_PAK}_ROOT "${xpak_workdir}/${ARG_PAK}" PARENT_SCOPE)
endfunction()



function(xpak_work_dir)
	if(NOT "${X_PAK_WORK}" STREQUAL "")
		set(X_PAK "${X_PAK_WORK}" CACHE PATH "" FORCE)
	else()
		execute_process(
			COMMAND
				${X_XPAKTOOL_EXECUTABLE} setup -check-only
			OUTPUT_VARIABLE
				output
		)

		foreach(line IN ITEMS ${output})
			string(REPLACE "=" ";" line ${line})
			list(GET line 0 key)
			if (${key} STREQUAL "XPAK_DIR")
				list(GET line 1 value)
				set(X_PAK "${value}" CACHE PATH "" FORCE)
				break()
			endif()
		endforeach()
	endif()

	if(APPLE)
		set(X_PAK "${X_PAK}/${MACOS_XPAK}" CACHE PATH "" FORCE)
	endif()

	file(MAKE_DIRECTORY "${X_PAK}")
endfunction()
