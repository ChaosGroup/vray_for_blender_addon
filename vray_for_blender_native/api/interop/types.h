#pragma once

#include <boost/python.hpp>
#include <memory>
#include <span>
#include <string>

#include "export/assets/blender_types.h"
#include "utils.hpp"

#include "zmq_common.hpp"

namespace VRayForBlender::Interop
{


/// Immutable exporter setting
struct ExporterSettings
{
	using StrList = std::vector<std::string>;

	VrayZmqWrapper::ExporterType getExporterType() const;

	PROPERTY(int,  exporterType           , static_cast<int>(VrayZmqWrapper::ExporterType::PROD))  // Render type
	PROPERTY(int,  renderThreads          , -1)
	PROPERTY(bool, closeVfbOnStop         , false)
	PROPERTY(bool, drUse                  , false)	// Distributed rendering
	PROPERTY(bool, drRenderOnlyOnHosts    , false)  // Distributed rendering
	PROPERTY(std::string, remoteDispatcher, "")     // Distributed rendering
	PROPERTY_NO_DEFAULT(StrList, drHosts)           // Distributed rendering
	PROPERTY(bool, separateFiles          , false)  // Export to separate files
	PROPERTY(std::string, previewDir      , "")		// Folder for .exr material preview files
	void setDRHosts(py::object hosts);
};


/// View-dependent exporter settings
struct ViewSettings
{
	PROPERTY(int, vfbFlags            , 0)		// VRayBaseTypes::VfbFlags
	PROPERTY(int, viewportImageQuality, 0)
	PROPERTY(int, viewportImageType   , 0)	    // VRayBaseTypes::ImageType
	PROPERTY(int, renderMode          , 0)		// VRayBaseTypes::RenderMode
};


/// Command-line arguments with which to start the ZMQ Server process.
struct ZmqServerArgs
{
	PROPERTY(std::string, exePath        , "")
	PROPERTY(int, port				     , -1) // A special value which means 'use ephemeral port'
	PROPERTY(int, logLevel               , 2)
	PROPERTY(bool, enableQtLogs          , false)
	PROPERTY(bool, headlessMode          , false)
	PROPERTY(bool, noHeartbeat           , true)
	PROPERTY(int64_t, blenderPID         , 0)
	PROPERTY(std::string, dumpLogFile    , "")
	PROPERTY(std::string, vfbSettingsFile, "")
	PROPERTY(std::string, vrayLibPath    , "")
	PROPERTY(std::string, appSDKPath     , "")
	PROPERTY(std::string, pluginVersion  , "00000")
	PROPERTY(std::string, blenderVersion , "")

	std::string getAddress(int port) const;
};


struct UVAttrLayer
{
	const std::string name;
	const std::span<const float[2]> data;
};


struct AttrLayer {
	enum DataType {
		Byte,
		Float,
	};

	enum Domain {
		Point,
		Corner,
	};

	const std::string name;
	const DataType dataType = DataType::Float;
	const Domain domain = Domain::Point;
	const uint8_t * const data = nullptr;
	const size_t elementCount = 0;

	static inline float colorByteToFloat(uint8_t v)
	{
		static const float mult = 1.0f / 255.f;
		return static_cast<float>(v) * mult;
	}

	inline VRayBaseTypes::AttrVector getAttrVector(unsigned int idx) const {
		if (dataType == DataType::Byte) {
			const MLoopCol& color = reinterpret_cast<const MLoopCol*>(data)[idx];
			return VRayBaseTypes::AttrVector(colorByteToFloat(color.r), colorByteToFloat(color.g), colorByteToFloat(color.b));
		} else {
			using Vec4f = float[4];
			const auto& color = reinterpret_cast<const Vec4f*>(data)[idx];
			return VRayBaseTypes::AttrVector(color);
		}
	}
};

struct Subdiv
{
	PROPERTY(int, level, 0);
	PROPERTY(int, type, 0);
	PROPERTY(bool, enabled, false);
	PROPERTY(bool, useCreases, false);
};



struct MeshExportOptions
{
	PROPERTY(bool, mergeChannelVerts, false)
	PROPERTY(bool, forceDynamicGeometry, false)
	PROPERTY(bool, useSubsurfToOSD, false)
};


struct MeshData
{
	enum class NormalsDomain : int{
		Face   = 0,
		Point  = 1,
		Corner = 2
	};

	MeshData() = delete;

	explicit MeshData(boost::python::object meshData);

	std::string                      name;
	NormalsDomain				     normalsDomain;		// Type of normals in the 'normals' field
	std::span<const float[3]>        vertices;			// Mesh vertices
	std::span<const unsigned int>    loops;				// Loop indices of faces
	std::span<const unsigned int[3]> loopTris;			// Loop triangles
	std::span<const unsigned int>    loopTriPolys;		// Loop triangle polygon indices
	std::span<const unsigned int>    polyMtlIndices;	// Material index per polygon
	std::span<const float[3]>        normals;			// Normals

	std::vector<UVAttrLayer> uvLayers;
	std::vector<AttrLayer> colorLayers;

	Subdiv subdiv;
	MeshExportOptions options;

public:
	boost::python::object ref;              // Reference to the original Python object
};

using MeshDataPtr = std::shared_ptr<MeshData>;


struct HairData
{
	explicit HairData(boost::python::object obj);

	std::string        name;
	std::string        type;                    // "CURVES" or "PARTICLES"
	int                segments       = 0;      // Number of segments per strand ( vertices - 1 )
	float              width          = 0.f;    // Width of the strand at the start point
	bool               fadeWidth      = false;  // Is the strand getting thinner?
	bool               widthsInPixels = false;  // Is strand width specified in pixels?
	bool               useHairBSpline = false;
	std::vector<float> matWorld;

	std::span<const float[3]> points;           // Array of strand points, float[3] per point
	std::span<const float>    pointRadii;       // Array of float per point
	std::span<const int>      strandSegments;   // Array of int per point
	std::span<const float[2]> uvs;              // Array of UVs, float[2] per point
	std::span<const float[3]> vertColors;       // Array of colors, float[3] per point
	boost::python::object ref;                  // Lifetime ref
};

using HairDataPtr = std::shared_ptr<HairData>;


struct PointCloudData
{
	explicit PointCloudData(boost::python::object obj);

	std::string name;
	int         renderType;

	std::span<const float[3]> points;  // Array of float[3] per point
	std::span<const float[2]> uvs;     // Array of float[2] per point
	std::span<const float>    radii;   // Array of float per point
	std::span<const float[3]> colors;  // Array of float[3] per point
	boost::python::object     ref;     // Lifetime ref
};

using PointCloudDataPtr = std::shared_ptr<PointCloudData>;


struct InstancerData
{
	explicit InstancerData(boost::python::object obj);

	std::string           name;
	int                   frame     = 0;
	int                   itemCount = 0;
	std::span<const char> data;
	boost::python::object ref;
};

using InstancerDataPtr = std::shared_ptr<InstancerData>;


struct InstanceData
{
	explicit InstanceData(const boost::python::object &obj);

	int                   index;
	std::string           nodePlugin;
	boost::python::object tm;
	boost::python::object vel;
};


struct SmokeData
{

	explicit SmokeData(const boost::python::object &obj);

	std::string        name;
	std::string        cacheDir;
	std::vector<float> transform;
	std::vector<int>   domainRes;

	boost::python::object ref;
};

using SmokeDataPtr = std::shared_ptr<SmokeData>;


/// Info for the ExportScene render action
struct ExportSceneSettings
{
	PROPERTY(bool, compressed    , true)
	PROPERTY(bool, hexArrays     , true)
	PROPERTY(bool, hexTransforms , false)
	PROPERTY(bool, separateFiles , false)
	PROPERTY(bool, cloudExport   , false)
	PROPERTY(std::string, hostAppString, "")
	PROPERTY(std::string, filePath, "")

	// A comma-separated list of categories from
	// {"view", "lights", "geometry", "nodes", "materials", "textures", "bitmaps", "render_elements"}
	PROPERTY(std::string, pluginTypes, "")
};

struct HostInfo {
	PROPERTY(std::string, vrayVersion, "")
	PROPERTY(std::string, buildVersion, "")
	PROPERTY(std::string, blenderVersion, "")
};

struct CosmosAssetSettings
{
	PROPERTY(std::string, assetType, "")
	PROPERTY(std::string, matFile, "")
	PROPERTY(std::string, objFile, "")
	PROPERTY(std::string, lightFile, "")
	PROPERTY(std::string, packageId, "")
	PROPERTY(int, revisionId, 0)
	PROPERTY(py::dict, locationsMap, py::dict())
	PROPERTY(bool, isAnimated, false)
};

} // VRayForBlender::Interop
