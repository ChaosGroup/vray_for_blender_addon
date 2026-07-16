# ZMQ Wrapper
ZMQ Wrapper is a library which provides an implementation of a custom protocol and communication agents for connectivity between _VRay for Blender_ and _VRay ZMQ Server_. 

## Components

- base_types.h -  defines the supported types
- zmq_message.hpp - defines the VRayMessage type which wraps an arbitrary protocol message
- zmq_serializer/zmq_deserializer.cpp - serialization utilities
- zmq_agent/zmq_router - implementation of the DEALER / ROUTER ZMQ pattern
    
## Usage
Include the library as source in your project.

## Overview

### Agent / Router
**ZmqAgent(Blender)**  <-- interprocess --> **ZmqRouter(ZmqServer)** <-- inprocess --> **ZmqAgent(ZmqServer)**

A pair of ZmqAgents can communicate through a tunnel created by ZmqRouter. The initiating side connects to the router and provides a unique (for the router session) ID. The router creates a peer ZmqAgent with the same ID and starts tunneling messages in both directions.

A protocol message consists of 3 parts - the routing ID, the payload type and the payload itself. Each message part is a separate zmq::message_t object. Protocol messages not conforming to this rule result in the router sending an error message back to the sending side. An error reply is also sent upon any communication error or heartbeat interval timeout. 

ZMQ protocol implementats silent reconnects on the underlying communication protocol wherever possible. For this reason, errors reported from ZmqAgent or ZmqRouter may be considered irrecoverable with respect to the established tunnel. After an error has occured, the ZmqAgent cannot be reused, but has to be destroyed. Given that both peers will be notified of the error, the whole tunnel has to be re-established.

The communication starts with a handshake message carrying the current protocol version implemented by the initiating side ( the client ). If the protocol version is supported by the router, it crates a new ZmqAgent in worker mode and starts relaying the messages between the two agents. When run, the worker agent replies to the handshake and from this moment on, the communcation is symmetrical for both sides.

### Heartbeats
Each agent may decide to periodically send a heartbeat on the connection used for payload data. Each side of the communication may use different intervals and timeouts. After reciving a PING message, an agent will unconditionally respond with a PONG.

This makes is possible to monitor both payload and monitoring connections ( by simply not sending any payload data other than PINGs ).  


## Testing

To build and run tests:

1. Clone Catch2 from https://github.com/catchorg/Catch2
2. Run cmake, replacing the value for {CATCH_ROOT} with the root folder of the cloned Catch2 repo
```
cmake -G "Visual Studio 17 2022" -A x64 -DLIBS_ROOT=../vray_for_blender_libs -DCATCH2_ROOT="{CATCH_ROOT}"
```
3. Build and run the VS project in one of the release configurations (we don't maintain a debug build of libzmq)
