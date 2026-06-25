// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#ifndef VRAY_FOR_BLENDER_BASE_TYPES_H
#define VRAY_FOR_BLENDER_BASE_TYPES_H

#include <algorithm>
#include <cmath>
#include <cstring>
#include <string>
#include <vector>
#include <tsl/robin_map.h>
#include <memory>
#include <initializer_list>
#include "vassert.h"

// Compile time max(A, B)
template <size_t A, size_t B>
struct compile_time_max {
	enum { value = (A > B ? A : B) };
};

// recursive template for max of variable number of template arguments
// general case - max of sizeof of the first type and recursive call for the rest
template <typename T, typename ... Q>
struct max_type_sizeof {
	enum { value = compile_time_max<sizeof(T), max_type_sizeof<Q...>::value>::value };
};

// base case for just 2 types
template <typename T, typename Q>
struct max_type_sizeof<T, Q> {
	enum { value = compile_time_max<sizeof(T), sizeof(Q)>::value };
};


namespace VRayBaseTypes {

const int VectorBytesCount  = 3 * sizeof(float);
const int Vector2BytesCount = 2 * sizeof(float);

enum CommitAction {
	CommitNone,
	CommitNow,
	CommitNowForce,
	CommitAutoOff,
	CommitAutoOn
};

// Values must match VRay::RendererOptions::RenderMode
enum RenderMode {
	RENDER_MODE_PRODUCTION = -1,
	RENDER_MODE_INTERACTIVE = 0,
	RENDER_MODE_INTERACTIVE_CUDA = 4,
	RENDER_MODE_INTERACTIVE_OPTIX = 7,
	RENDER_MODE_INTERACTIVE_METAL = 8,
	RENDER_MODE_PRODUCTION_CUDA = 104,
	RENDER_MODE_PRODUCTION_OPTIX = 107,
	RENDER_MODE_PRODUCTION_METAL = 108
};

enum VfbFlags {
	None		= 0x0,
	Show		= 0x1,
	AlwaysOnTop = 0x2,
};

// Values must match VRay::RenderElement::Type
enum RenderChannelType {
	RenderChannelTypeNone = -1,
	RenderChannelTypeFragColor = 1,
	RenderChannelTypeFragTransp,
	RenderChannelTypeFragRealtransp,
	RenderChannelTypeFragBackground,
	RenderChannelTypeFragZbuf,
	RenderChannelTypeFragRenderid,
	RenderChannelTypeFragNormal,
	RenderChannelTypeFragAlphatransp,
	RenderChannelTypeFragExtraaa,
	RenderChannelTypeFragWeight,
	RenderChannelTypeFragLast,
	RenderChannelTypeVfbAtmosphere = 100,
	RenderChannelTypeVfbDiffuse,
	RenderChannelTypeVfbReflect,
	RenderChannelTypeVfbRefract,
	RenderChannelTypeVfbSelfillum,
	RenderChannelTypeVfbShadow,
	RenderChannelTypeVfbSpecular,
	RenderChannelTypeVfbLighting,
	RenderChannelTypeVfbGi,
	RenderChannelTypeVfbCaustics,
	RenderChannelTypeVfbRawgi,
	RenderChannelTypeVfbRawlight,
	RenderChannelTypeVfbRawshadow,
	RenderChannelTypeVfbVelocity,
	RenderChannelTypeVfbRenderID,
	RenderChannelTypeVfbMtlid,
	RenderChannelTypeVfbNodeid,
	RenderChannelTypeVfbZdepth,
	RenderChannelTypeVfbReflectionFilter,
	RenderChannelTypeVfbRawReflection,
	RenderChannelTypeVfbRefractionFilter,
	RenderChannelTypeVfbRawRefraction,
	RenderChannelTypeVfbRealcolor,
	RenderChannelTypeVfbNormal,
	RenderChannelTypeVfbBackground,
	RenderChannelTypeVfbAlpha,
	RenderChannelTypeVfbColor,
	RenderChannelTypeVfbWirecolor,
	RenderChannelTypeVfbMatteshadow,
	RenderChannelTypeVfbTotallight,
	RenderChannelTypeVfbRawtotallight,
	RenderChannelTypeVfbBumpnormal,
	RenderChannelTypeVfbSamplerate,
	RenderChannelTypeVfbSss2,
	RenderChannelTypeDrbucket,
	RenderChannelTypeVfbVrmtlreflectgloss,
	RenderChannelTypeVfbVrmtlreflecthigloss,
	RenderChannelTypeVfbVrmtlrefractgloss,
	RenderChannelTypeVfbShademapExport,
	RenderChannelTypeVfbReflectAlhpha,
	RenderChannelTypeVfbVrmtlreflectior,
	RenderChannelTypeVfbMtlrenderid,
	RenderChannelTypeVfbNoiselevel,
	RenderChannelTypeVfbWorldposition,
	RenderChannelTypeVfbDenoised,
	RenderChannelTypeVfbWorldbumpnormal,
	RenderChannelTypeVfbDefocusamount,
	RenderChannelTypeVfbEffectsResult = 153,
	RenderChannelTypeVfbToon,
	RenderChannelTypeVfbRendertime = 157,
	RenderChannelTypeVfbCryptomatte,
	RenderChannelTypeVfbLightingAnalysis = 160,
	RenderChannelTypeVfbLightSelect = 163,
	RenderChannelTypeVfbVrmtlmetalness = 165,
	RenderChannelTypeVfbLightMix = 178,
	RenderChannelTypeVfbToonLighting = 180,
	RenderChannelTypeVfbToonSpecular,
	RenderChannelTypeVfbVrmtlsheencolor = 183,
	RenderChannelTypeVfbRawSheenReflection,
	RenderChannelTypeVfbSheenFilter,
	RenderChannelTypeVfbVrmtlsheenglossiness,
	RenderChannelTypeVfbSheenReflAlpha,
	RenderChannelTypeVfbCoatReflAlpha,
	RenderChannelTypeVfbVrmtlcoatcolor,
	RenderChannelTypeVfbRawCoatReflection,
	RenderChannelTypeVfbCoatFilter,
	RenderChannelTypeVfbVrmtlcoatglossiness,
	RenderChannelTypeVfbSheenReflection = 194,
	RenderChannelTypeVfbCoatReflection,
};


enum ValueType {
	ValueTypeUnknown = 0,

	ValueTypeInt,
	ValueTypeFloat,
	ValueTypeDouble,
	ValueTypeColor,
	ValueTypeAColor,
	ValueTypeVector,
	ValueTypeVector2,
	ValueTypeMatrix,
	ValueTypeTransform,
	ValueTypeString,
	ValueTypePlugin,

	ValueTypeImageSet,

	ValueTypeList,

	ValueTypeListInt,
	ValueTypeListFloat,
	ValueTypeListColor,
	ValueTypeListVector,
	ValueTypeListVector2,
	ValueTypeListMatrix,
	ValueTypeListTransform,
	ValueTypeListString,
	ValueTypeListPlugin,

	ValueTypeListValue,

	ValueTypeMapChannels,
};

// Enum used when updating plugin parameter values.
enum PluginUpdateFlags : uint32_t {
	PluginValueForceUpdate = 1 << 0,
	PluginValueAsString    = 1 << 1,
	PluginValueAnimatable  = 1 << 2,
	PluginReCreateAttr     = 1 << 3,
};

/// Empty value type used to block export of a attribute
struct AttrIgnore {
	ValueType getType() const {
		return ValueType::ValueTypeUnknown;
	}
};

template <typename T>
struct AttrSimpleType {
	ValueType getType() const;
	AttrSimpleType(): value() {}
	AttrSimpleType(const T & val): value(val) {}
	AttrSimpleType(T && val): value(std::move(val)) {}

	operator const T & () const {
		return value;
	}

	operator T & () {
		return value;
	}

	T value;
};

template <typename Q>
struct AttrSimpleType<AttrSimpleType<Q>> {
	// intentionally left uninplemented - should not happen
	AttrSimpleType();
	AttrSimpleType(const AttrSimpleType<Q> & val);
};

/// Specialize bool as int since we dont have bool type
/// Specializing just getType is not enough because value member must be atleast int size
/// so writing and reading is okay
template <>
struct AttrSimpleType<bool> {
	ValueType getType() const {
		return ValueType::ValueTypeInt;
	}
	AttrSimpleType(): int_value(0) {}
	AttrSimpleType(const bool & val): int_value(0) { value = val; }

	operator const bool & () const { return value; }
	operator bool & () { return value; }

	union {
		int  int_value; // Ensures size and alignment match AttrSimpleType<int>
		bool value;     // Provides a valid bool reference
	};
};


template <>
inline ValueType AttrSimpleType<int>::getType() const {
	return ValueType::ValueTypeInt;
}

template <>
inline ValueType AttrSimpleType<float>::getType() const {
	return ValueType::ValueTypeFloat;
}

template <>
inline ValueType AttrSimpleType<double>::getType() const {
	return ValueType::ValueTypeDouble;
}

template <>
inline ValueType AttrSimpleType<std::string>::getType() const {
	return ValueType::ValueTypeString;
}

typedef AttrSimpleType<std::string> AttrString;
typedef AttrSimpleType<int> AttrInt;
typedef AttrSimpleType<float> AttrFloat;
typedef AttrSimpleType<double> AttrDouble;
typedef AttrSimpleType<bool> AttrBool;

/// Pixel data for an image or image region.
///
/// Ownership model for `data`:
///   AttrImage operates in two modes, both using shared_ptr<char[]>:
///
///   1. OWNING - the constructor or set() allocate a new buffer and memcpy into it.
///      The shared_ptr uses the default deleter (delete[]).  The AttrImage fully owns
///      the pixel memory and frees it when the last copy is destroyed.
///
///   2. NON-OWNING VIEW - viewOf() wraps a caller-owned buffer in a shared_ptr with
///      a no-op deleter [](char*){}.  No memory is freed when the AttrImage is destroyed.
///      The caller must guarantee the buffer outlives all uses of the AttrImage.
///
///      This is used in two places:
///        - Server side (sendImages / onBucketReady): VRay's VRayImage owns the pixels.
///          viewOf() avoids copying before serialization. The VRayImage stays alive
///          through serializeMessage() which completes synchronously.
///        - Client side (deserializer): the ZMQ message buffer owns the pixels.
///          A shared_ptr with no-op deleter points into the zmq::message_t payload.
///          The message stays alive for the entire handleMsg() -> processRendererOnImage()
///          -> update() call chain, which copies the data into its final destination
///          before handleMsg() returns and the ZMQ message is freed.
struct AttrImage {
	enum ImageType {
		NONE = 0,
		RGBA_REAL,
		RGB_REAL,
		BW_REAL,
		JPG
	};

	AttrImage()
	    : data(nullptr)
	    , size(0)
	    , width(0)
	    , height(0)
	    , x(-1)
	    , y(-1)
	    , imageType(NONE)
	{}

	/// Owning constructor: allocates a new buffer and copies imgData into it.
	AttrImage(const void *data, size_t size, AttrImage::ImageType type, int width, int height, int x = -1, int y = -1)
	    : data(nullptr)
	    , size(size)
	    , width(width)
	    , height(height)
	    , x(x)
	    , y(y)
	    , imageType(type)
	{
		set(data, size);
	}

	bool isBucket() const {
		return x != -1 && y != -1;
	}

	/// Owning: allocate a private buffer and copy imgData into it.
	void set(const void * imgData, size_t dataSize) {
		this->data.reset(new char[dataSize]);
		this->size = dataSize;
		::memcpy(this->data.get(), imgData, dataSize);
	}

	/// Non-owning: wrap an external buffer with a no-op deleter.
	/// The caller must ensure the buffer outlives any use of the returned
	/// AttrImage (including serialization and deserialization on the receiving end).
	static AttrImage viewOf(void* imgData, size_t dataSize, ImageType type,
	                        int width, int height, int x = -1, int y = -1)
	{
		AttrImage img;
		img.data    = std::shared_ptr<char[]>(static_cast<char*>(imgData), [](char*) {});
		img.size    = dataSize;
		img.imageType = type;
		img.width   = width;
		img.height  = height;
		img.x       = x;
		img.y       = y;
		return img;
	}

	std::shared_ptr<char[]> data; ///< Pixel bytes. See class comment for ownership model.
	size_t size; ///< Size in bytes
	int width; ///< Width in pixels
	int height; ///< Height in pixels
	int x; ///< if positive - X of top left corner of bucket sub image, else negative for full
	int y; ///< if positive - Y of top left corner of bucket sub image, else negative for full
	ImageType imageType; ///< The format of the image data (JPG, RGBA, etc.)
};

enum ImageSourceType {
	ImageSourceInvalid,
	RtImageUpdate,
	ImageReady,
	BucketImageReady,
};

struct AttrImageSet {
	ValueType getType() const {
		return ValueType::ValueTypeImageSet;
	}

	AttrImageSet(ImageSourceType sourceType = ImageSourceInvalid)
	    : sourceType(sourceType)
	{}

	tsl::robin_map<RenderChannelType, AttrImage, std::hash<int>> images;
	ImageSourceType sourceType;
	std::unordered_map<std::string, std::string> metadata; ///< Key-value metadata (e.g. Cryptomatte manifest, keyed
	                                                       ///< by "cryptomatte" or "cryptomatte.<instanceName>" for multi-instance).
};

struct AttrColor {

	ValueType getType() const {
		return ValueType::ValueTypeColor;
	}

	AttrColor():
	    r(0.0f),
	    g(0.0f),
	    b(0.0f)
	{}

	AttrColor(const float &r, const float &g, const float &b):
	    r(r),
	    g(g),
	    b(b)
	{}

	AttrColor(float c):
		r(c),
		g(c),
		b(c)
	{}

	AttrColor(float color[4]):
		r(color[0]),
		g(color[1]),
		b(color[2])
	{}

	float r;
	float g;
	float b;
};


struct AttrAColor {

	ValueType getType() const {
		return ValueType::ValueTypeAColor;
	}

	inline AttrAColor():
	    alpha(1.0f)
	{}

	inline AttrAColor(const AttrColor &c, const float &a=1.0f):
	    color(c),
	    alpha(a)
	{}

	AttrColor  color;
	float      alpha;
};


struct AttrVector {

	ValueType getType() const {
		return ValueType::ValueTypeVector;
	}

	inline AttrVector():
	    x(0.0f),
	    y(0.0f),
	    z(0.0f)
	{}

	inline AttrVector(const float vector[3]):
		x(vector[0]),
		y(vector[1]),
		z(vector[2])
	{}

	inline AttrVector(float _x, float _y, float _z):
		x(_x),
		y(_y),
		z(_z)
	{}

	inline AttrVector operator - (const AttrVector &other) const {
		return AttrVector(x - other.x, y - other.y, z - other.z);
	}

	inline bool operator == (const AttrVector &other) const {
		return (x == other.x) && (y == other.y) && (z == other.z);
	}

	inline float len() const {
		return sqrtf(x * x + y * y + z * z);
	}

	inline void set(const float &_x, const float &_y, const float &_z) {
		x = _x;
		y = _y;
		z = _z;
	}

	inline void set(float vector[3]) {
		x = vector[0];
		y = vector[1];
		z = vector[2];
	}

	float x;
	float y;
	float z;
};


struct AttrVector2 {

	ValueType getType() const {
		return ValueType::ValueTypeVector2;
	}

	AttrVector2():
	    x(0.0f),
	    y(0.0f)
	{}

	AttrVector2(const float vector[2]):
		x(vector[0]),
		y(vector[1])
	{}

	float x;
	float y;
};


struct AttrMatrix {

	ValueType getType() const {
		return ValueType::ValueTypeMatrix;
	}

	AttrMatrix() {}

	AttrMatrix(const float tm[3][3]):
	    v0(tm[0]),
	    v1(tm[1]),
	    v2(tm[2])
	{}

	AttrMatrix(const float tm[4][4]):
	    v0(tm[0]),
	    v1(tm[1]),
	    v2(tm[2])
	{}

	AttrVector v0;
	AttrVector v1;
	AttrVector v2;
};


struct AttrTransform {

	ValueType getType() const {
		return ValueType::ValueTypeTransform;
	}

	AttrTransform() {}

	AttrTransform(const float tm[4][4]):
	    m(tm),
	    offs(tm[3])
	{}

	static AttrTransform identity() {
		static float tm[4][4] = {
			{1, 0, 0, 0},
			{0, 1, 0, 0},
			{0, 0, 1, 0},
			{0, 0, 0, 1},
		};
		return AttrTransform(tm);
	}

	static AttrTransform zero() {
		static float tm[4][4] = {
			{0, 0, 0, 0},
			{0, 0, 0, 0},
			{0, 0, 0, 0},
			{0, 0, 0, 0},
		};
		return AttrTransform(tm);
	}
	AttrMatrix m;
	AttrVector offs;
};

struct AttrValue;
struct AttrPlugin {

	ValueType getType() const {
		return ValueType::ValueTypePlugin;
	}

	AttrPlugin() {}
	AttrPlugin(const std::string &name, const std::string& outputName="") :
	    plugin(name), output(outputName)
	{}

	operator bool () const {
		return !plugin.empty();
	}

	AttrPlugin& operator=(const std::string &name) {
		plugin = name;
		return *this;
	}

	AttrPlugin & operator=(const AttrValue &);

	std::string output;
	std::string plugin;
};


template <typename T>
struct AttrList {
	typedef std::vector<T>            DataType;
	typedef std::shared_ptr<DataType> DataArrayPtr;

	ValueType getType() const ;

	AttrList(DataType && data)
	    : m_Ptr(new DataType(std::move(data)))
	{}

	AttrList(std::initializer_list<T> items) {
		m_Ptr = DataArrayPtr(new DataType(items));
	}

	AttrList() {
		init();
	}

	explicit AttrList(const int &size) {
		init();
		resize(size);
	}

	void init() {
		m_Ptr = DataArrayPtr(new DataType);
	}

	void resize(int cnt) {
		m_Ptr.get()->resize(cnt);
	}

	void reserve(int cnt) {
		m_Ptr.get()->reserve(cnt);
	}

	void append(const T &value) {
		m_Ptr.get()->push_back(value);
	}

	void fill(const T &value, int count) {
		auto* vec = m_Ptr.get();
		const size_t oldSize = vec->size();
		vec->resize(oldSize + count);
		std::fill_n(vec->data() + oldSize, count, value);
	}

	void prepend(const T &value) {
		m_Ptr.get()->insert(m_Ptr.get()->begin(), value);
	}

	int getCount() const {
		return static_cast<int>(m_Ptr.get()->size());
	}

	// NOTE: Won't work for AttrList<std::string>
	int getBytesCount() const {
		return getCount() * sizeof(T);
	}

	inline T* operator * () {
		return m_Ptr.get()->data();
	}

	inline const T* operator * () const {
		return m_Ptr.get()->data();
	}

	inline operator bool () const {
		return m_Ptr && m_Ptr.get()->size();
	}

	bool empty() const {
		return !m_Ptr || (m_Ptr.get()->size() == 0);
	}

	inline const DataArrayPtr getData() const {
		return m_Ptr;
	}

	inline DataArrayPtr getData() {
		return m_Ptr;
	}

private:
	DataArrayPtr m_Ptr;
};

typedef AttrList<int>           AttrListInt;
typedef AttrList<float>         AttrListFloat;
typedef AttrList<AttrColor>     AttrListColor;
typedef AttrList<AttrVector>    AttrListVector;
typedef AttrList<AttrVector2>   AttrListVector2;
typedef AttrList<AttrPlugin>    AttrListPlugin;
typedef AttrList<std::string>   AttrListString;
typedef AttrList<AttrMatrix>    AttrListMatrix;
typedef AttrList<AttrTransform> AttrListTransform;


template <>
inline ValueType AttrListInt::getType() const {
	return ValueType::ValueTypeListInt;
}

template <>
inline ValueType AttrListFloat::getType() const {
	return ValueType::ValueTypeListFloat;
}

template <>
inline ValueType AttrListColor::getType() const {
	return ValueType::ValueTypeListColor;
}

template <>
inline ValueType AttrListVector::getType() const {
	return ValueType::ValueTypeListVector;
}

template <>
inline ValueType AttrListVector2::getType() const {
	return ValueType::ValueTypeListVector2;
}

template <>
inline ValueType AttrListPlugin::getType() const {
	return ValueType::ValueTypeListPlugin;
}

template <>
inline ValueType AttrListString::getType() const {
	return ValueType::ValueTypeListString;
}

template <>
inline ValueType AttrListMatrix::getType() const {
	return ValueType::ValueTypeListMatrix;
}

template <>
inline ValueType AttrListTransform::getType() const {
	return ValueType::ValueTypeListTransform;
}


struct AttrMapChannels {

	ValueType getType() const {
		return ValueType::ValueTypeMapChannels;
	}

	struct AttrMapChannel {
		AttrListVector vertices;
		AttrListInt    faces;
		std::string    name;
		int            channelId = -1; // Explicit channel ID; -1 means use list position
	};
	typedef std::vector<AttrMapChannel> MapChannelsList;

	MapChannelsList data;
};


const int ATTR_DATA_SIZE = max_type_sizeof<
AttrColor,
AttrAColor,
AttrVector,
AttrVector2,
AttrMatrix,
AttrTransform,
AttrPlugin,
AttrList<int>, // all lists have same sizeof
AttrMapChannels,
AttrImage,
AttrImageSet,
AttrSimpleType<int>,
AttrSimpleType<float>,
AttrSimpleType<double>,
AttrSimpleType<std::string>>::value;

struct AttrValue;
typedef AttrList<AttrValue> AttrListValue;


struct AttrValue {
	AttrValue():
		type(ValueTypeUnknown) {}

	template <typename T>
	AttrValue(const T & attrValue) {
		new(asPtr<T>())T(attrValue); // ctor on memmory
		type = as<T>().getType();
	}

	AttrValue(const std::string & attrValue) {
		type = ValueTypeString;
		new(asPtr<AttrSimpleType<std::string>>())AttrSimpleType<std::string>(attrValue);
	}

	AttrValue(std::string && attrValue) {
		type = ValueTypeString;
		new(asPtr<AttrSimpleType<std::string>>())AttrSimpleType<std::string>(std::move(attrValue));
	}

	AttrValue(const char * attrValue) {
		type = ValueTypeString;
		new(asPtr<AttrSimpleType<std::string>>())AttrSimpleType<std::string>(attrValue ? attrValue : "");
	}

	AttrValue(const int & attrValue) {
		type = ValueTypeInt;
		new(asPtr<AttrSimpleType<int>>())AttrSimpleType<int>(attrValue);
	}

	AttrValue(const bool & attrValue) {
		type = ValueTypeInt;
		new(asPtr<AttrSimpleType<int>>())AttrSimpleType<int>(attrValue);
	}

	AttrValue(const float & attrValue) {
		type = ValueTypeFloat;
		new(asPtr<AttrSimpleType<float>>())AttrSimpleType<float>(attrValue);
	}

	ValueType getType() const {
		return type;
	}

	template <typename T>
	T * asPtr() {
		return reinterpret_cast<T*>(data);
	}

	template <typename T>
	const T * asPtr() const {
		return reinterpret_cast<const T*>(data);
	}

	template <typename T>
	T & as() {
		return *asPtr<T>();
	}

	template <typename T>
	const T & as() const {
		return *asPtr<T>();
	}

	template <typename T>
	T convertTo() const {
		return *reinterpret_cast<T*>(data);
	}

	AttrValue & operator=(const AttrValue & o) {
		if (this != & o) {
			destroyData();
			copyInitData(o);
		}
		return *this;
	}

	AttrValue(const AttrValue & o) {
		copyInitData(o);
	}

	void defaultInitData() {
		vassert(type != ValueTypeUnknown && "Cannot default init unknown type!");
		switch(type) {
		case ValueTypeString:        new(asPtr<AttrSimpleType<std::string>>())AttrSimpleType<std::string>(); break;
		case ValueTypePlugin:        new(asPtr<AttrPlugin>())AttrPlugin(); break;
		case ValueTypeListInt:       new(asPtr<AttrListInt>())AttrListInt(); break;
		case ValueTypeListFloat:     new(asPtr<AttrListFloat>())AttrListFloat(); break;
		case ValueTypeListColor:     new(asPtr<AttrListColor>())AttrListColor(); break;
		case ValueTypeListVector:    new(asPtr<AttrListVector>())AttrListVector(); break;
		case ValueTypeListVector2:   new(asPtr<AttrListVector2>())AttrListVector2(); break;
		case ValueTypeListMatrix:    new(asPtr<AttrListMatrix>())AttrListMatrix(); break;
		case ValueTypeListTransform: new(asPtr<AttrListTransform>())AttrListTransform(); break;
		case ValueTypeListString:    new(asPtr<AttrListString>())AttrListString(); break;
		case ValueTypeListPlugin:    new(asPtr<AttrListPlugin>())AttrListPlugin(); break;
		case ValueTypeListValue:     new(asPtr<AttrListValue>())AttrListValue(); break;
		case ValueTypeMapChannels:   new(asPtr<AttrMapChannels>())AttrMapChannels(); break;
		case ValueTypeImageSet:      new(asPtr<AttrImageSet>())AttrImageSet(); break;
		default: memset(data, 0, ATTR_DATA_SIZE); break;
		}
	}

	void copyInitData(const AttrValue & other) {
		type = other.type;
		switch(other.type) {
		case ValueTypeString:        new(asPtr<AttrSimpleType<std::string>>())AttrSimpleType<std::string>(other.as<AttrSimpleType<std::string>>()); break;
		case ValueTypePlugin:        new(asPtr<AttrPlugin>())AttrPlugin(other.as<AttrPlugin>()); break;
		case ValueTypeListInt:       new(asPtr<AttrListInt>())AttrListInt(other.as<AttrListInt>()); break;
		case ValueTypeListFloat:     new(asPtr<AttrListFloat>())AttrListFloat(other.as<AttrListFloat>()); break;
		case ValueTypeListColor:     new(asPtr<AttrListColor>())AttrListColor(other.as<AttrListColor>()); break;
		case ValueTypeListVector:    new(asPtr<AttrListVector>())AttrListVector(other.as<AttrListVector>()); break;
		case ValueTypeListVector2:   new(asPtr<AttrListVector2>())AttrListVector2(other.as<AttrListVector2>()); break;
		case ValueTypeListMatrix:    new(asPtr<AttrListMatrix>())AttrListMatrix(other.as<AttrListMatrix>()); break;
		case ValueTypeListTransform: new(asPtr<AttrListTransform>())AttrListTransform(other.as<AttrListTransform>()); break;
		case ValueTypeListString:    new(asPtr<AttrListString>())AttrListString(other.as<AttrListString>()); break;
		case ValueTypeListPlugin:    new(asPtr<AttrListPlugin>())AttrListPlugin(other.as<AttrListPlugin>()); break;
		case ValueTypeListValue:     new(asPtr<AttrListValue>())AttrListValue(other.as<AttrListValue>()); break;
		case ValueTypeMapChannels:   new(asPtr<AttrMapChannels>())AttrMapChannels(other.as<AttrMapChannels>()); break;
		case ValueTypeImageSet:      new(asPtr<AttrImageSet>())AttrImageSet(other.as<AttrImageSet>()); break;
		default: memcpy(data, other.data, ATTR_DATA_SIZE); break; // others are POD so we can memcpy
		}
	}

	void destroyData() {
		// only ones that need dtor called, make sure to update here if other type needs
		switch (type) {
		case ValueTypeString:        as<AttrSimpleType<std::string>>().~AttrSimpleType<std::string>(); break;
		case ValueTypePlugin:        as<AttrPlugin>().~AttrPlugin(); break;
		case ValueTypeListInt:       as<AttrListInt>().~AttrListInt(); break;
		case ValueTypeListFloat:     as<AttrListFloat>().~AttrListFloat(); break;
		case ValueTypeListColor:     as<AttrListColor>().~AttrListColor(); break;
		case ValueTypeListVector:    as<AttrListVector>().~AttrListVector(); break;
		case ValueTypeListVector2:   as<AttrListVector2>().~AttrListVector2(); break;
		case ValueTypeListMatrix:    as<AttrListMatrix>().~AttrListMatrix(); break;
		case ValueTypeListTransform: as<AttrListTransform>().~AttrListTransform(); break;
		case ValueTypeListString:    as<AttrListString>().~AttrListString(); break;
		case ValueTypeListPlugin:    as<AttrListPlugin>().~AttrListPlugin(); break;
		case ValueTypeListValue:     as<AttrListValue>().~AttrListValue(); break;
		case ValueTypeMapChannels:   as<AttrMapChannels>().~AttrMapChannels(); break;
		case ValueTypeImageSet:      as<AttrImageSet>().~AttrImageSet(); break;
		default: break; // nothing to do
		}
		memset(data, 0, ATTR_DATA_SIZE);
		type = ValueTypeUnknown;
	}

	~AttrValue() {
		destroyData();
	}

	ValueType type;
	uint8_t data[ATTR_DATA_SIZE];

	const char *getTypeAsString() const {
		switch (type) {
		case ValueTypeInt:           return "Int";
		case ValueTypeFloat:         return "Float";
		case ValueTypeColor:         return "Color";
		case ValueTypeAColor:        return "AColor";
		case ValueTypeVector:        return "Vector";
		case ValueTypeTransform:     return "Transform";
		case ValueTypeString:        return "String";
		case ValueTypePlugin:        return "Plugin";
		case ValueTypeListInt:       return "ListInt";
		case ValueTypeListFloat:     return "ListFloat";
		case ValueTypeListColor:     return "ListColor";
		case ValueTypeListVector:    return "ListVector";
		case ValueTypeListMatrix:    return "ListMatrix";
		case ValueTypeListTransform: return "ListTransform";
		case ValueTypeListString:    return "ListString";
		case ValueTypeListPlugin:    return "ListPlugin";
		case ValueTypeListValue:     return "ListValue";
		case ValueTypeMapChannels:   return "Map Channels";
		default:
			break;
		}
		return "Unknown";
	}

	operator bool() const {
		bool valid = true;
		if (type == ValueTypeUnknown) {
			valid = false;
		} else if (type == ValueTypePlugin) {
			valid = !!(as<AttrPlugin>());
		}
		return valid;
	}
};


template <>
inline ValueType AttrListValue::getType() const {
	return ValueType::ValueTypeListValue;
}



inline AttrPlugin & AttrPlugin::operator=(const AttrValue & val) {
	if (val.type == ValueTypePlugin) {
		*this = val.as<AttrPlugin>();
	}
	return *this;
}
} // namespace VRayBaseTypes

#endif // VRAY_FOR_BLENDER_BASE_TYPES_H

