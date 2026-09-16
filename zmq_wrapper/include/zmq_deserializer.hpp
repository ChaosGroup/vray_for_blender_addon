// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include "base_types.h"
#include "vassert.h"

namespace VrayZmqWrapper{

class DeserializerStream {
public:
	DeserializerStream() = delete;

	DeserializerStream(const char * data, size_t size)
	    : first(data)
	    , current(data)
	    , last(data + size)
	{}

	bool hasMore() const {
		return current < last;
	}

	void rewind() {
		current = first;
	}

	size_t getSize() const {
		return last - first;
	}

	size_t getRemaining() const {
		return last - current;
	}

	bool read(char * where, int size) {
		if (!forward(size)) {
			return false;
		}
		memcpy(where, current - size, size);
		return true;
	}

	const char * getCurrent() const {
		return current;
	}

	bool forward(size_t size) {
		const char * newPtr = current + size;
		if (newPtr > last || newPtr < first) {
			return false;
		}
		current = newPtr;
		return true;
	}

private:
	const char *first;
	const char *current;
	const char *last;
};


template <typename T>
DeserializerStream & operator>>(DeserializerStream & stream, T & value) {
	[[maybe_unused]] bool ok = stream.read(reinterpret_cast<char*>(&value), sizeof(value));
	vassert(ok && "Deserialization read failed: stream exhausted");
	return stream;
}


inline DeserializerStream & operator>>(DeserializerStream & stream, std::string & value) {
	int size = 0;
	stream >> size;
	vassert(size >= 0 && "Negative string size in deserialization");

	value.assign(stream.getCurrent(), static_cast<size_t>(size));
	stream.forward(value.size());

	return stream;
}


inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrSimpleType<std::string> & value) {
	stream >> value.value;
	return stream;
}


inline DeserializerStream & operator>> (DeserializerStream & stream, VRayBaseTypes::AttrPlugin & plugin) {
	return stream >> plugin.plugin >> plugin.output;
}

template <typename Q>
inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrList<Q> & list) {
	int size = 0;
	stream >> size;
	vassert(size >= 0 && "Negative list size in deserialization");

	const Q * src = reinterpret_cast<const Q *>(stream.getCurrent());
	list.getData()->assign(src, src + size);
	stream.forward(static_cast<size_t>(size) * sizeof(Q));

	return stream;
}


template <typename T>
inline void readListNonPOD(DeserializerStream & stream, VRayBaseTypes::AttrList<T> & list) {
	int size = 0;
	stream >> size;

	auto & data = *list.getData();
	data.clear();
	data.reserve(size);
	for (int c = 0; c < size; ++c) {
		T item;
		stream >> item;
		data.push_back(std::move(item));
	}
}

inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrList<VRayBaseTypes::AttrPlugin> & list) {
	readListNonPOD(stream, list);
	return stream;
}


inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrList<std::string> & list) {
	readListNonPOD(stream, list);
	return stream;
}

inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrList<VRayBaseTypes::AttrValue> & list) {
	readListNonPOD(stream, list);
	return stream;
}

inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrMapChannels & mapChannels) {
	int size = 0;
	stream >> size;
	mapChannels.data.clear();
	mapChannels.data.reserve(size);
	for (int c = 0; c < size; ++c) {
		VRayBaseTypes::AttrMapChannels::AttrMapChannel channel;
		stream >> channel.vertices >> channel.faces >> channel.name >> channel.channelId;
		mapChannels.data.push_back(std::move(channel));
	}
	return stream;
}



inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrImage & image) {
	stream >> image.imageType >> image.size >> image.width >> image.height >> image.x >> image.y;
	// Non-owning view into the ZMQ message buffer - avoids a memcpy+allocation.
	// Safe because the ZMQ message outlives processRendererOnImage() and all update()
	// calls that consume this data before handleMsg() returns.
	image.data = std::shared_ptr<char[]>(const_cast<char*>(stream.getCurrent()), [](char*) {});
	stream.forward(image.size);
	return stream;
}


inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrImageSet & set) {
	int count;
	stream >> set.sourceType >> count;
	VRayBaseTypes::RenderChannelType type;

	for (int c = 0; c < count; c++) {
		VRayBaseTypes::AttrImage img;
		stream >> type >> img;
		set.images.emplace(type, std::move(img));
	}

	int metaCount = 0;
	stream >> metaCount;
	for (int c = 0; c < metaCount; c++) {
		std::string key, value;
		stream >> key >> value;
		set.metadata.emplace(std::move(key), std::move(value));
	}
	return stream;
}


inline DeserializerStream & operator>>(DeserializerStream & stream, VRayBaseTypes::AttrValue & value) {
	stream >> value.type;
	value.defaultInitData();
	using namespace VRayBaseTypes;
	switch (value.type) {
	case ValueTypeInt: stream >> value.as<AttrSimpleType<int>>(); break;
	case ValueTypeFloat: stream >> value.as<AttrSimpleType<float>>(); break;
	case ValueTypeString: stream >> value.as<AttrSimpleType<std::string>>(); break;
	case ValueTypeColor: stream >> value.as<AttrColor>(); break;
	case ValueTypeAColor: stream >> value.as<AttrAColor>(); break;
	case ValueTypeVector: stream >> value.as<AttrVector>(); break;
	case ValueTypeVector2: stream >> value.as<AttrVector2>(); break;
	case ValueTypeMatrix: stream >> value.as<AttrMatrix>(); break;
	case ValueTypeTransform: stream >> value.as<AttrTransform>(); break;
	case ValueTypePlugin: stream >> value.as<AttrPlugin>(); break;
	case ValueTypeImageSet: stream >> value.as<AttrImageSet>(); break;
	case ValueTypeListInt: stream >> value.as<AttrListInt>(); break;
	case ValueTypeListFloat: stream >> value.as<AttrListFloat>(); break;
	case ValueTypeListColor: stream >> value.as<AttrListColor>(); break;
	case ValueTypeListVector: stream >> value.as<AttrListVector>(); break;
	case ValueTypeListVector2: stream >> value.as<AttrListVector2>(); break;
	case ValueTypeListMatrix: stream >> value.as<AttrListMatrix>(); break;
	case ValueTypeListTransform: stream >> value.as<AttrListTransform>(); break;
	case ValueTypeListString: stream >> value.as<AttrListString>(); break;
	case ValueTypeListPlugin: stream >> value.as<AttrListPlugin>(); break;
	case ValueTypeListValue: stream >> value.as<AttrListValue>(); break;
	case ValueTypeMapChannels: stream >> value.as<AttrMapChannels>(); break;
	default: vassert(!"Missing DeserializerStream::operator>> for some ValueType"); break;
	}
	return stream;
}

};  // end VrayZmqWrapper namespace

