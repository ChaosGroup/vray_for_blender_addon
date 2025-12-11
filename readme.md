# VRay for Blender building instructions

## 1. Clone Libraries
1. Clone the repository: [Blender Libraries](https://projects.blender.org/blender/lib-windows_x64.git).
2. Check out the branch **blender-v4.5-release** (or the respective branch for version 4.2, 4.3 or 4.4).

## 2. Build ZMQ
1. Clone the repository: [ZeroMQ Library](https://github.com/zeromq/libzmq.git).
2. Check out the tag **v4.3.4**.
3. Build the library by following the instructions provided in the repository.
4. Create a folder named `zmq_build` and organize the output files into the following structure:
```
zmq_build   
│
└───bin
│   └───libzmq-v142-mt-4_3_4.dll
|
└───include
│   └───zmq.h
│   └───zmq_utils.h
│   
└───lib/Release/v142
│   └───libzmq-v142-mt-4_3_4.lib
```

## 3. Get boost 1.82:
Get boost 1.82 library with Python 11 bindings. It can be obtained from the **blender-v4.3-release** branch of [Windows Blender Libraries](https://projects.blender.org/blender/lib-windows_x64/src/branch/blender-v4.3-release) or [MacOS Blender Libraries](https://projects.blender.org/blender/lib-macos_arm64/src/branch/blender-v4.3-release)

## 4. Create the install folder
Create the folder passed as the ADDON_PATH parameter to cmake in the next step.

## 5. Build 

* The `BLENDER_VER` parameter specifies the Blender version (currently 4.3, 4.4, 4.5 and 5.0 are supported) for which this build is intended.
* The path passed in 'ADDON_PATH' parameter must exist before the command is run


#### 5.1 Windows
Generate a Visual Studio project with the following command:

```bash 
cmake -S ./vray_for_blender_addon \
      -B ./build \
      -G "Visual Studio 17 2022" \
      -A x64 -DWITH_TESTS=0 \
      -DADDON_PATH="./install"  \
      -DBOOST_LIBDIR="path/to/boost" \
      -DZMQ_LIBDIR="path/to/zmq_build" \
      -DBLENDER_SDK_ROOT="path/to/lib-windows_x64" \
      -DBLENDER_VER="4.5"

```

*Build the plugin*

* Open build/VRayForBlender.sln solution in Visual Studio
* Build the ALL_BUILD project to produce the addon binaries
* Build the INSTALL project to copy the addon files to the install location

#### 5.2 MacOS

``` bash
cmake -S . \
     -B ./build \
     -G Ninja \
     -DCMAKE_OSX_ARCHITECTURES="arm64" \
     -DWITH_TESTS=0 \
     -DADDON_PATH="./install" \
     -DBOOST_LIBDIR="path/to/boost" \
     -DBLENDER_SDK_ROOT="path/to/lib-macos_arm64" \
     -DBLENDER_VER=4.5 
         
ninja install
```

# Contributing

At the moment, we’re not able to accept external contributions.
However, if you have any questions or would like to explore potential collaboration, feel free to reach out to us.
