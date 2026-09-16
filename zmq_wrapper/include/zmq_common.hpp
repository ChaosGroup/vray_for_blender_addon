// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <string>


#include <zmq.hpp>

#include "base_types.h"

namespace VrayZmqWrapper{

static const int ZMQ_PROTOCOL_VERSION = 2049;

static const int CONNECT_TIMEOUT		= 2000;	// ms
static const int SOCKET_IO_TIMEOUT		= 100;  // ms
static const int DEFAULT_PING_INTERVAL	= 1000; // ms
static const int NO_PING				= 0;    // special value
static const int HEARBEAT_TIMEOUT		= DEFAULT_PING_INTERVAL * 3;
static const int HEARTBEAT_NO_TIMEOUT   = 0;    // special value

using RoutingId = uint64_t;

inline int shorten(const RoutingId& id) {
	return id % 1000;
}

/// Exception generated or rethrown by custom ZMQ code
class ZmqException : public std::exception {
public:
	explicit ZmqException(const std::string& msg) : m_message(msg.c_str())
	{}

	explicit ZmqException(const char* msg) : m_message(msg)
	{}

	const char* what() const noexcept override {
		return m_message.c_str();
	}
private:
	std::string m_message;
};


#define CHECK_ZMQ(exp, msg)	if (!(exp)) { throw ZmqException(msg); }


/// Timeout settings for ZMQ connections
struct ZmqTimeouts
{
	int connect    = CONNECT_TIMEOUT;
	int send       = SOCKET_IO_TIMEOUT;
	int recv       = SOCKET_IO_TIMEOUT;
	int ping       = DEFAULT_PING_INTERVAL;
	int inactivity = HEARBEAT_TIMEOUT;
	int handshake  = CONNECT_TIMEOUT;
};


/// Message types
enum class ControlMessage : int {
	INVALID = 0,
	CONNECT = 10,    // Sent by the handshake initiator
	CONNECTED = 11,  // Sent as reply to a CONNECT
	DATA = 20,       // Message with payload
	PING = 30,       // Heartbeat ping
	PONG = 31,       // Heartbeat response to ping
	ERR = 40         // The peer returned an error
};

enum class ServerReturnCode : int {
	OK = 0,
	GENERAL_ERROR = 1,
	NO_LICENSE = 2,
	WRONG_ARGS = 3,
	ENV_ERROR = 4,
	STD_EXCEPT = 5,
	VR_EXCEPT = 6,
	OPERATION_TIMEOUT = 7,
	NOT_SUPPORTED = 8,
	LAST
};

inline const char * getServerReturnCodeStr(int code) {
	const char * msg[] = {
		"OK", "General error", "No license", "Can't parse arguments", "Can't setup environment", "STD exception", "V-Ray exception",
		"Operation timeout", "Not supported"
	};
	static_assert(std::size(msg) == (int)ServerReturnCode::LAST, "msg array must have one entry per ServerReturnCode");
	int i=(int)code;
	if(i>=0 && i<(int)ServerReturnCode::LAST)
		return msg[(int)code];
	return "Unknown error";
}


using Version = int;

struct HandshakeMsg{
	Version protoVersion;
	int workerType;
};

// Indicates render procedure type
enum class ExporterType {
	INVALID = -1,
	FIRST_TYPE = 0,
	IPR_VIEWPORT = 0,
	IPR_VFB,
	PROD,
	PREVIEW,
	ANIMATION,
	VANTAGE_LIVE_LINK,
	SCENE_IMPORT,   ///< Non-rendering worker: reads a .vrscene and streams plugin data back to the client.
	SCATTER_PREVIEW,///< Non-rendering worker: computes Chaos Scatter preview transforms via readScatterData.
	TYPES_COUNT
};


// Convenience converters to ZMQ message_t
template <class T>
inline zmq::message_t toZmqMsg(const T& t){
	return zmq::message_t(&t, sizeof(t));
}

template <>
inline zmq::message_t toZmqMsg(const std::string& t){
	return zmq::message_t(t);
}


// Convenience string builder
template<typename T>
inline void BuildMsg(std::ostream& ss, const T& t) {
	ss << t << " ";
}

template<typename T, typename ...Args>
inline void BuildMsg(std::ostream& ss, const T& t, Args&&... args) {
	BuildMsg(ss, t);
	BuildMsg(ss, std::forward<Args>(args)...);
}

template <class ...TArgs>
inline std::string Msg(TArgs&&... args) {
	std::ostringstream ss;
	BuildMsg(ss, args...);
	return ss.str();
}


// The ids of the memory mappings between the server and the host.
// These should be the same in both client and server.
static const std::string SHARED_PORT_MAPPING_ID       = "endp";    // Listening endpoint info
static const std::string SHARED_IMG_BUFFER_MAPPING_ID = "imgbuf";  // Image transfer buffer
static const std::string SHARED_IMG_ID_MAPPING_ID     = "imgid"; // The ID of the image transfer buffer
static const std::string SHARED_ELEM_ID_MAPPING_ID    = "elem";  // The ID of the per-element transfer buffer

inline std::string getImageBufferID(int imgID) {
	return SHARED_IMG_ID_MAPPING_ID + "_" + std::to_string(imgID);
}

/// Per-render-element SHM region name. There is exactly one element buffer per renderer
/// (shared by all elements in the frame, written/read serially), so the name is fixed --
/// the server destroys + recreates under the same name when the buffer needs to grow.
inline std::string getElementBufferID() {
	return SHARED_ELEM_ID_MAPPING_ID;
}


/// Routing key for per-instance render-element images shared across the IPC protocol:
/// V-Ray plugin `name` + sub-layer index (Cryptomatte rank, ObjectSelect 0/1/2, 0 otherwise).
/// Both server and client identify element images by this pair; defining it once here
/// keeps the two sides in sync without duplicating the FNV combiner.
struct PerInstanceKey {
	std::string instanceName;
	int         subIndex = 0;

	bool operator==(const PerInstanceKey& o) const {
		return subIndex == o.subIndex && instanceName == o.instanceName;
	}
};

struct PerInstanceKeyHash {
	size_t operator()(const PerInstanceKey& k) const {
		size_t h = std::hash<std::string>{}(k.instanceName);
		h ^= std::hash<int>{}(k.subIndex) + 0x9e3779b97f4a7c15ULL + (h << 6) + (h >> 2);
		return h;
	}
};

};  // end VrayZmqWrapper namespace
