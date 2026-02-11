// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#define ZMQ_BUILD_DRAFT_API

#include <string>
#include <vector>

#include "cppzmq/zmq.hpp"
#include "base_types.h"
#include "zmq_serializer.hpp"
#include "zmq_deserializer.hpp"


namespace VrayZmqWrapper{

namespace vray = VRayBaseTypes;

namespace details {
	/// Specializations for correct serialization of enum values without the need to cast
	/// them to integral values.

	template <class T>
	static SerializerStream& serialize (SerializerStream& s, const T& value, std::false_type) {
		return s << value;
	}

	template <class T>
	static SerializerStream& serialize (SerializerStream& s, const T& value, std::true_type) {
		return s << static_cast<typename std::underlying_type<T>::type>(value);
	}

	template <class T>
	static DeserializerStream& deserialize (DeserializerStream& s, T& value, std::true_type) {
		typename std::underlying_type<T>::type enumValue;
		s >> enumValue;
		value = static_cast<T>(enumValue);
		return s;
	}

	template <class T>
	static DeserializerStream& deserialize (DeserializerStream& s, T& value, std::false_type) {
		return s >> value;
	}
}

/// Facade for the enum serialization specialization templates using the same operator (&&)
/// for both serialization and deserialization.
template <class T>
static SerializerStream& operator&& (SerializerStream& s, const T& value) {
	return details::serialize(s, value, std::is_enum<T>{});
}

template <class T>
static DeserializerStream& operator&& (DeserializerStream& s, T& value) {
	return details::deserialize(s, value, std::is_enum<T>{});
}


/// Define a protocol message struct. The name of the struct is the message type prefixed by 'Msg'.
///  @param msgType - the name of one of MsgType enum values
///  @param data - a sequence of one or more field declarations of serializable types
/// 
/// Example usage:
/// PROTO_MESSAGE(PluginUpdate,
///   std::string pluginName;
///   std::string propertyName;
///   vray::AttrValue propertyValue;
/// );
///
#define PROTO_MESSAGE(msgType, data)\
struct Msg##msgType { \
	data \
	MsgType getType() const {return MsgType::msgType;} \
}

/// Define a protocol message struct with no serializable fields. The name of the struct is the message type prefixed by 'Msg'.
/// param msgType - the name of one of MsgType enum values
/// 
/// Example usage:
/// EMPTY_PROTO_MESSAGE(RenderStart);
#define EMPTY_PROTO_MESSAGE(msgType)\
struct Msg##msgType{ \
	MsgType getType() const {return MsgType::msgType;} \
}


/// The following definitions implement both serialization and deserialization
/// using the same operator (&&). The type of operation is determined by the 
/// type of function arguments. The goal is to to only have a single definition
/// for both serialization and deserialization for most serailizable types, thus
/// avoiding errors from mismatched serialization order of fields.
///
/// Example usage:
/// SERIALIZE_MESSAGE(PluginUpdate,
///   PARAM(pluginName)
///   PARAM(propertyName)
///   PARAM(propertyValue)
/// );
#define SERIALIZE_MESSAGE(msgType, data) \
static SerializerStream& operator&& (SerializerStream& s, const Msg##msgType& msg) { \
	return s data; \
}\
static DeserializerStream& operator&& (DeserializerStream& s, Msg##msgType& msg) {\
	return s data; \
}\


#define SERIALIZE_EMPTY_MESSAGE(msgType) \
static SerializerStream& operator&& (SerializerStream& s, const Msg##msgType&) { \
	return s; \
}\
static DeserializerStream& operator&& (DeserializerStream& s, Msg##msgType&) {\
	return s; \
}\

#define SERIALIZE_STRUCT(Type, data) \
static SerializerStream& operator&& (SerializerStream& s, const Type& msg) { \
	return s data; \
}\
static DeserializerStream& operator&& (DeserializerStream& s, Type& msg) {\
	return s data; \
}\


/// Adds a serializable field to a SERIALIZE_MESSAGE or SERIALIZE_STRUCT macro.
#define PARAM(param) && msg.param


/// Serialize a protocol message including its type.
template <typename TMsg>
static zmq::message_t serializeMessage(const TMsg& msg) {
	SerializerStream stream;
	stream && msg.getType() && msg;
	return zmq::message_t(stream.getData(), stream.getSize());
}

/// Deserialize a protocol message excluding its type. The type should have been
/// deserialized already in order to determine the type of message to read.
template <typename TMsg>
static TMsg deserializeMessage(DeserializerStream& s) {
	TMsg msg;
	s && msg;
	return msg;
}

};  // end VrayZmqWrapper namespace 

