# VRay for Blender building instructions

## 1. Get Blender SDK repo
1. Clone the repository: [Blender Libraries](https://projects.blender.org/blender/lib-windows_x64).
2. Check out the branch **blender-v4.5-release** (or the respective branch for other supported versions).


## 2. Get 3-rd party libraries
### 2.1 Boost
Get boost v 1.82. It can be obtained from the **blender-v4.3-release** branch of [Windows Blender Libraries](https://projects.blender.org/blender/lib-windows_x64/src/branch/blender-v4.3-release) or [MacOS Blender Libraries](https://projects.blender.org/blender/lib-macos_arm64/src/branch/blender-v4.3-release)

### 2.2 Python SDK
Nanobind and the `VRayBlenderLib` Python module are compiled against the CPython development files - the headers and, on Windows, the import library - so a Python SDK has to be in place before CMake is run.

The Python version is the one the target Blender embeds: **3.11** for Blender 4.5 and 5.0, **3.13** for Blender 5.1 and 5.2.

The build does not search for Python. It takes it from a fixed location under `BLENDER_SDK_ROOT`, named after that version, exactly as Blender's own build files expect it (`311`, `313`, ... on Windows; a single `python` root with version-tagged includes elsewhere):

| Platform      | Location (Python 3.11)          | Required contents                                          |
| ------------- | ------------------------------- | ---------------------------------------------------------- |
| Windows       | `<BLENDER_SDK_ROOT>/python/311` | `include/Python.h`, `libs/python311.lib`, `bin/python.exe`  |
| Linux / MacOS | `<BLENDER_SDK_ROOT>/python`     | `include/python3.11/Python.h`, `lib/`, `bin/python3.11`     |

For Python 3.13 the same layout applies with the version substituted: `python/313` and `libs/python313.lib` on Windows, `include/python3.13` and `bin/python3.13` on the other platforms.

### 2.3 Nanobind
Clone Nanobind v 2.11 from https://github.com/wjakob/nanobind (tag v.2.11.0), **including its submodules**:

```bash
git clone --recursive https://github.com/wjakob/nanobind.git
cd nanobind
git checkout v2.11.0
git submodule update --init --recursive
```

The `ext/robin_map` submodule is mandatory - its headers are used both by nanobind itself and directly by the V-Ray projects.

Nanobind is not built or installed as a separate step. Its sources are compiled into a static library by this project (one per Python version, using the Python SDK from 2.2), so it is enough to pass the root of the clone as `NANOBIND_LIBDIR` to the CMake command in step 4.

### 2.4 ZMQ
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
* The path passed in `BLENDER_SDK_ROOT` must contain the Python SDK described in 2.2 - CMake fails while configuring `VRayBlenderLib` if the Python headers are not there
* `NANOBIND_LIBDIR` is the root of the nanobind clone from 2.3, not an installed/built nanobind


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