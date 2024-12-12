.PHONY: clean
clean:
	rm -f vs/CMakeCache.txt
	rm -f -r vs/CMakeFiles
	rm -f vs/*.vcxproj*

.PHONY: make
make: clean
	mkdir -p vs/install
	cmake -G "Visual Studio 17 2022" -A x64 -B vs/ -S . -DLIBS_ROOT=../vray_for_blender_libs -DLIBDIR="../win64_vc15" -DINSTALL_PREFIX=vs/install
