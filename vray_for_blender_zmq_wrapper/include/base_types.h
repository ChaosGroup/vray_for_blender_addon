#ifndef VRAY_FOR_BLENDER_BASE_TYPES_H
#define VRAY_FOR_BLENDER_BASE_TYPES_H

#include <cmath>
#include <vector>
#include <string>
#include <cstring>
#include <unordered_map>
#include <memory>
#include <cassert>

#include <initializer_list>

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
	RENDER_MODE_PRODUCTION_CUDA = 104,
	RENDER_MODE_PRODUCTION_OPTIX = 107,
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
	RenderChannelTypeVfbLightSelect = 163,
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

	ValueTypeInstancer,
	ValueTypeMapChannels,
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
	AttrSimpleType(): value() {}
	AttrSimpleType(const bool & val): value(val) {}

	operator const bool & () const {
		return reinterpret_cast<const bool &>(value);
	}

	operator bool & () {
		return reinterpret_cast<bool&>(value);
	}

	int value;
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

	void set(const void * imgData, size_t dataSize) {
		this->data.reset(new char[dataSize]);
		this->size = dataSize;
		::memcpy(this->data.get(), imgData, dataSize);
	}

	std::shared_ptr<char> data; ///< Image bytes data
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

	std::unordered_map<RenderChannelType, AttrImage, std::hash<int>> images;
	ImageSourceType sourceType;
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

	AttrAColor():
	    alpha(1.0f)
	{}

	AttrAColor(const AttrColor &c, const float &a=1.0f):
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

	AttrVector():
	    x(0.0f),
	    y(0.0f),
	    z(0.0f)
	{}

	AttrVector(const float vector[3]):
		x(vector[0]),
		y(vector[1]),
		z(vector[2])
	{}

	AttrVector(const float &_x, const float &_y, const float &_z):
		x(_x),
		y(_y),
		z(_z)
	{}

	AttrVector operator - (const AttrVector &other) const {
		return AttrVector(x - other.x, y - other.y, z - other.z);
	}

	bool operator == (const AttrVector &other) const {
		return (x == other.x) && (y == other.y) && (z == other.z);
	}

	float len() const {
		return sqrtf(x * x + y * y + z * z);
	}

	void set(const float &_x, const float &_y, const float &_z) {
		x = _x;
		y = _y;
		z = _z;
	}

	void set(float vector[3]) {
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

	void resize(const int &cnt) {
		m_Ptr.get()->resize(cnt);
	}

	void append(const T &value) {
		m_Ptr.get()->push_back(value);
	}

	void prepend(const T &value) {
		m_Ptr.get()->insert(0, value);
	}

	int getCount() const {
		return static_cast<int>(m_Ptr.get()->size());
	}

	// NOTE: Won't work for AttrList<std::string>
	int getBytesCount() const {
		return getCount() * sizeof(T);
	}

	T* operator * () {
		return &m_Ptr.get()->at(0);
	}

	const T* operator * () const {
		return &m_Ptr.get()->at(0);
	}

	operator bool () const {
		return m_Ptr && m_Ptr.get()->size();
	}

	bool empty() const {
		return !m_Ptr || (m_Ptr.get()->size() == 0);
	}

	const DataArrayPtr getData() const {
		return m_Ptr;
	}

	DataArrayPtr getData() {
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
	};
	typedef std::unordered_map<std::string, AttrMapChannel> MapChannelsMap;

	MapChannelsMap data;
};


struct AttrInstancer {

	ValueType getType() const {
		return ValueType::ValueTypeInstancer;
	}

	struct Item {
		int            index;
		AttrTransform  tm;
		AttrTransform  vel;
		AttrPlugin     node;
	};
	typedef AttrList<Item> Items;

	float frameNumber;
	Items data;
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
AttrInstancer,
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
		assert(type != ValueTypeUnknown && "Cannot default init unknown type!");
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
		case ValueTypeInstancer:     new(asPtr<AttrInstancer>())AttrInstancer(); break;
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
		case ValueTypeInstancer:     new(asPtr<AttrInstancer>())AttrInstancer(other.as<AttrInstancer>()); break;
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
		case ValueTypeInstancer:     as<AttrInstancer>().~AttrInstancer(); break;
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
		case ValueTypeInstancer:     return "Instancer2";
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

