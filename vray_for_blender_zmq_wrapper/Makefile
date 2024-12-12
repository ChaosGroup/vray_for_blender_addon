# Initialize project after cloning from Git
.PHONY: init
init:
	mkdir vs
	git submodule update --init --recursive
	
# Reset cmake project build
.PHONY: cmake_clean
cmake_clean:
	rm -f vs/CMakeCache.txt


# Force cmake project rebuild
.PHONY: cmake
cmake: cmake_clean
	cmake -B vs -G "Visual Studio 17 2022" -A x64 -DLIBS_ROOT=../vray_for_blender_libs -DVRAY_ZMQ_WRAPPER_INSTALL_PREFIX=C:\


# Force cmake project rebuild
.PHONY: cmake_with_tests
cmake_with_tests: cmake_clean
	cmake -B vs -G "Visual Studio 17 2022" -A x64 -DWITH_TESTS=1 -DLIBS_ROOT=../vray_for_blender_libs -DCATCH2_ROOT="c:\sdk\Catch2" -DVRAY_ZMQ_WRAPPER_INSTALL_PREFIX=C:\