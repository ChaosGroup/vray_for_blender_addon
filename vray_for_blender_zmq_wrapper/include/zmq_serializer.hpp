// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <vector>
#include <string>

#include "base_types.h"
#include "vassert.h"

namespace VrayZmqWrapper{

class SerializerStream {
public:

	SerializerStream() {
	}

	void write(const char * data, size_t size) {
		if (size == 0) {
			return;
		}
		const size_t prevSize = stream.size();
		stream.resize(size + stream.size());
		memcpy(&stream[prevSize], data, size);
	}

	size_t getSize() const {
		return stream.size();
	}

	char * getData() {
		return stream.data();
	}

private:
	std::vector<char> stream;
};


template <typename T>
inline SerializerStream & operator<<(SerializerStream & stream, const T & value) {
	stream.write(reinterpret_cast<const char*>(&value), sizeof(value));
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const std::string & value) {
	stream << static_cast<int>(value.size());
	stream.write(value.c_str(), value.size());
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrSimpleType<std::string> & value) {
	stream << value.value;
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrPlugin & plugin) {
	return stream << plugin.plugin << plugin.output;
}

template <typename Q>
inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrList<Q> & list) {
	stream << list.getCount();
	stream.write(reinterpret_cast<const char *>(list.getData()->data()), list.getCount() * sizeof(Q));
	return stream;
}

template <typename T>
inline void writeListNonPOD(SerializerStream & stream, const VRayBaseTypes::AttrList<T> & list) {
	stream << list.getCount();
	if (!list.empty()) {
		for (auto & item : *(list.getData())) {
			stream << item;
		}
	}
}


// specialization for list of NON POD types
inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrList<VRayBaseTypes::AttrPlugin> & list) {
	writeListNonPOD(stream, list);
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrList<std::string> & list) {
	writeListNonPOD(stream, list);
	return stream;
}



inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrList<VRayBaseTypes::AttrValue> & list) {
	writeListNonPOD(stream, list);
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrMapChannels & map) {
	stream << static_cast<int>(map.data.size());
	for (auto & channel : map.data) {
		stream << channel.vertices << channel.faces << channel.name;
	}
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrInstancer::Item & instItem) {
	return stream << instItem.index << instItem.tm << instItem.vel << instItem.node;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrInstancer & inst) {
	stream << inst.frameNumber << inst.data.getCount();
	if (!inst.data.empty()) {
		for (auto & item : *(inst.data.getData())) {
			stream << item;
		}
	}
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrImage & image) {
	stream << image.imageType << image.size << image.width << image.height << image.x << image.y;
	stream.write(image.data.get(), image.size);
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrImageSet & set) {
	stream << set.sourceType << static_cast<int>(set.images.size());
	for (const auto &img : set.images) {
		stream << img.first << img.second;
	}
	return stream;
}


inline SerializerStream & operator<<(SerializerStream & stream, const VRayBaseTypes::AttrValue & value) {
	stream << value.type;
	using namespace VRayBaseTypes;
	switch(value.type) {
	case ValueTypeInt: stream << value.as<AttrSimpleType<int>>(); break;
	case ValueTypeFloat: stream << value.as<AttrSimpleType<float>>(); break;
	case ValueTypeString: stream << value.as<AttrSimpleType<std::string>>(); break;
	case ValueTypeColor: stream << value.as<AttrColor>(); break;
	case ValueTypeAColor: stream << value.as<AttrAColor>(); break;
	case ValueTypeVector: stream << value.as<AttrVector>(); break;
	case ValueTypeVector2: stream << value.as<AttrVector2>(); break;
	case ValueTypeMatrix: stream << value.as<AttrMatrix>(); break;
	case ValueTypeTransform: stream << value.as<AttrTransform>(); break;
	case ValueTypePlugin: stream << value.as<AttrPlugin>(); break;
	case ValueTypeImageSet: stream << value.as<AttrImageSet>(); break;
	case ValueTypeListInt: stream << value.as<AttrListInt>(); break;
	case ValueTypeListFloat: stream << value.as<AttrListFloat>(); break;
	case ValueTypeListColor: stream << value.as<AttrListColor>(); break;
	case ValueTypeListVector: stream << value.as<AttrListVector>(); break;
	case ValueTypeListVector2: stream << value.as<AttrListVector2>(); break;
	case ValueTypeListMatrix: stream << value.as<AttrListMatrix>(); break;
	case ValueTypeListTransform: stream << value.as<AttrListTransform>(); break;
	case ValueTypeListString: stream << value.as<AttrListString>(); break;
	case ValueTypeListPlugin: stream << value.as<AttrListPlugin>(); break;
	case ValueTypeListValue: stream << value.as<AttrListValue>(); break;
	case ValueTypeInstancer: stream << value.as<AttrInstancer>(); break;
	case ValueTypeMapChannels: stream << value.as<AttrMapChannels>(); break;
	default: vassert(!"Missing SerializerStream::operator<< for some ValueType");
	}
	return stream;
}

};  // end VrayZmqWrapper namespace 

