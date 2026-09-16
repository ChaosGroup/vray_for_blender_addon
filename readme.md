# VRay for Blender building instructions

## 1. Get Blender SDK repo
1. Clone the repository: [Blender Libraries](https://projects.blender.org/blender/lib-windows_x64).
2. Check out the branch **blender-v4.5-release** (or the respective branch for other supported versions).


## 2. Get 3-rd party libraries
### 2.1 Boost
Get boost v 1.82. It can be obtained from the **blender-v4.3-release** branch of [Windows Blender Libraries](https://projects.blender.org/blender/lib-windows_x64/src/branch/blender-v4.3-release) or [MacOS Blender Libraries](https://projects.blender.org/blender/lib-macos_arm64/src/branch/blender-v4.3-release)

### 2.2 Nanobind
Clone Nanobind v 2.11 from https://github.com/wjakob/nanobind (tag v.2.11.0)

### 2.3 ZMQ
1. Clone the cppzmq repository: [CPP ZMQ Library](https://github.com/zeromq/cppzmq).
 - chckout master @ 7f0530688804c2b5b6b0d985773405593fd25ca8 (2026-05-26)

2. Clone the libzmq repository: [ZeroMQ Library](https://github.com/zeromq/libzmq). 
 - checkout master @ 46493370217ac135246617fa2f6ac819d8b61bfc (2026-07-26)
 - build the library by following the instructions provided in the repository.


## 3. Create the install folder
Create the folder passed as the ADDON_PATH parameter to cmake in the next step.

## 4. Build 

* The `BLENDER_VER` parameter specifies the Blender version (currently 4.5, 5.0 and 5.1, 5.2 are supported) for which this build is intended.
* The path passed in 'ADDON_PATH' parameter must exist before the command is run


### 4.1 Windows
Generate a Visual Studio project with the following command:

```bash 
cmake -S ./vray_for_blender_addon \
      -B ./build \
      -G "Visual Studio 17 2022" \
      -A x64 -DWITH_TESTS=0 \
      -DADDON_PATH="./install" \
      -DBOOST_LIBDIR="path/to/boost" \
      -DZMQ_LIBRARIES="path/to/zmq_lib_files" \
      -DZMQ_INCLUDE_DIRS="path/to/zmq_includes" \
      -DZMQ_CPPZMQ_DIR="path/to/cppzmq_source_files" \
      -DNANOBIND_LIBDIR="path/to/nanobind" \
      -DBLENDER_SDK_ROOT="path/to/lib-windows_x64" \
      -DBLENDER_VER="5.2"
```

*Build the plugin*

* Open build/VRayForBlender.sln solution in Visual Studio
* Build the ALL_BUILD project to produce the addon binaries
* Build the INSTALL project to copy the addon files to the install location

### 4.2 MacOS

``` bash
cmake -S . \
    -B ./build \
    -G Ninja \
    -DCMAKE_OSX_ARCHITECTURES="arm64" \
    -DCMAKE_BUILD_TYPE=Release \  # or NoOpt
    -DWITH_TESTS=0 \
    -DADDON_PATH="./install" \
    -DBOOST_LIBDIR="path/to/boost" \
    -DZMQ_LIBRARIES="path/to/zmq_lib_files" \
    -DZMQ_INCLUDE_DIRS="path/to/zmq_includes" \
    -DZMQ_CPPZMQ_DIR="path/to/cppzmq_source_files" \
    -DNANOBIND_LIBDIR="path/to/nanobind" \
    -DBLENDER_SDK_ROOT="path/to/lib-windows_x64" \
    -DBLENDER_VER="5.2"

ninja install
```


### 4.3 Linux


``` bash
cmake -S . \
    -B ./build \
    -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \  # or NoOpt
    -DWITH_TESTS=0 \
    -DADDON_PATH="./install" \
    -DBOOST_LIBDIR="path/to/boost" \
    -DZMQ_LIBRARIES="path/to/zmq_lib_files" \
    -DZMQ_INCLUDE_DIRS="path/to/zmq_includes" \
    -DZMQ_CPPZMQ_DIR="path/to/cppzmq_source_files" \
    -DNANOBIND_LIBDIR="path/to/nanobind" \
    -DBLENDER_SDK_ROOT="path/to/lib-windows_x64" \
    -DBLENDER_VER="5.2"
	
ninja install
```

# Contributing

At the moment, we’re not able to accept external contributions.
However, if you have any questions or would like to explore potential collaboration, feel free to reach out to us.

# License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or(at your option) any later version.
See the [LICENSE](LICENSE) file for details.