# VRay for Blender building instructions

## 1. Clone Libraries
1. Clone the repository: [Blender Libraries](https://projects.blender.org/blender/lib-windows_x64.git).
2. Check out the branch **blender-v4.2-release**.

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

## 3. Build the plugin:
Build the addon with the following command
```bash 
cmake -S ./vray_for_blender_addon \
      -B ./build \
      -G "Visual Studio 17 2022" \
      -A x64 -DWITH_TESTS=0 \
      -DADDON_PATH="install/location/blender_vray/vray_blender"  \
      -DZMQ_ROOT="path/to/zmq_build" \
      -DLIBDIR="path/to/lib-windows_x64"

```