#pragma once

#include <string>


#define ZMQ_BUILD_DRAFT_API
#include "cppzmq/zmq.hpp"

#include "base_types.h"

namespace VrayZmqWrapper{

static const int ZMQ_PROTOCOL_VERSION = 2019;

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
	explicit ZmqException(const std::string& msg) : std::exception(msg.c_str())
	{}

	explicit ZmqException(const char* msg) : std::exception(msg)
	{}
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
	LAST
};

inline const char * getServerReturnCodeStr(int code) {
	const char * msg[] = {
		"OK", "General error", "No license", "Can't parse arguments", "Can't setup environment", "STD exception", "V-Ray exception"
	};
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
	FIRST_TYPE = 0,
	IPR_VIEWPORT = 0,
	IPR_VFB,
	PROD,
	PREVIEW,
	ANIMATION,
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
static const std::string SHARED_PORT_MAPPING_ID       = "endpoint";    // Listening endpoint info
static const std::string SHARED_IMG_BUFFER_MAPPING_ID = "img_buffer";  // Image transfer buffer
static const std::string SHARED_IMG_ID_MAPPING_ID	  = "img_buffer_id"; // The ID of the image transfer buffer

inline std::string getImageBufferID(int imgID) {
	return SHARED_IMG_ID_MAPPING_ID + "_" + std::to_string(imgID);
}

};  // end VrayZmqWrapper namespace 
