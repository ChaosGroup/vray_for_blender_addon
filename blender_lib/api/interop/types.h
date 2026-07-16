// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <nanobind/nanobind.h>
#include <nanobind/stl/vector.h>
#include <memory>
#include <span>
#include <string>

#include "export/assets/blender_types.h"
#include "utils.hpp"

#include "zmq_common.hpp"

namespace nb = nanobind;

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
	PROPERTY(int, profilerMode            , 0)		// RayProfilerSettings::mode
	PROPERTY(int, profilerMaxDepth        , 1)		// RayProfilerSettings::maxDepth
	PROPERTY(std::string, profilerOutputDirectory, "")	// RayProfilerSettings::outputDirectory
	PROPERTY(std::string, profilerSceneName      , "")	// RayProfilerSettings::sceneName
	void setDRHosts(nb::object hosts);
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
	PROPERTY(std::string, licenseType    , "commercial") ///< Forwarded to VRayZmqServer via the -license flag. Accepted values: "commercial", "community".

	std::string getAddress(int port) const;
};


struct UVAttrLayer
{
	std::string name;
	std::span<const float[2]> data;
};


struct AttrLayer {
	enum DataType {
		ByteColor,    // MLoopCol (4 x uint8)
		FloatColor,   // MPropCol (4 x float)
		Float,        // float
		Int,          // int32_t
		Int8,         // int8_t
		Boolean,      // 1 byte
		FloatVector,  // float[3]
		Float2,       // float[2]
		Int32_2D,     // int32_t[2]
		Int16_2D,     // int16_t[2]
	};

	enum Domain {
		Point,
		Corner,
		Edge,
		Face,
	};

	std::string name;
	DataType dataType = DataType::FloatColor;
	Domain domain = Domain::Point;
	const uint8_t * data = nullptr;
	size_t elementCount = 0;

	static inline float colorByteToFloat(uint8_t v)
	{
		static const float mult = 1.0f / 255.f;
		return static_cast<float>(v) * mult;
	}

	inline VRayBaseTypes::AttrVector getAttrVector(unsigned int idx) const {
		switch (dataType) {
			case ByteColor: {
				const MLoopCol& c = reinterpret_cast<const MLoopCol*>(data)[idx];
				return VRayBaseTypes::AttrVector(colorByteToFloat(c.r), colorByteToFloat(c.g), colorByteToFloat(c.b));
			}
			case FloatColor: {
				const MPropCol& c = reinterpret_cast<const MPropCol*>(data)[idx];
				return VRayBaseTypes::AttrVector(c.color);
			}
			case Float: {
				const float v = reinterpret_cast<const float*>(data)[idx];
				return VRayBaseTypes::AttrVector(v, v, v);
			}
			case Int: {
				const float v = static_cast<float>(reinterpret_cast<const int32_t*>(data)[idx]);
				return VRayBaseTypes::AttrVector(v, v, v);
			}
			case Int8: {
				const float v = static_cast<float>(reinterpret_cast<const int8_t*>(data)[idx]);
				return VRayBaseTypes::AttrVector(v, v, v);
			}
			case Boolean: {
				const float v = data[idx] ? 1.f : 0.f;
				return VRayBaseTypes::AttrVector(v, v, v);
			}
			case FloatVector: {
				const float* v = reinterpret_cast<const float(*)[3]>(data)[idx];
				return VRayBaseTypes::AttrVector(v[0], v[1], v[2]);
			}
			case Float2: {
				const float* v = reinterpret_cast<const float(*)[2]>(data)[idx];
				return VRayBaseTypes::AttrVector(v[0], v[1], 0.f);
			}
			case Int32_2D: {
				const int32_t* v = reinterpret_cast<const int32_t(*)[2]>(data)[idx];
				return VRayBaseTypes::AttrVector(static_cast<float>(v[0]), static_cast<float>(v[1]), 0.f);
			}
			case Int16_2D: {
				const int16_t* v = reinterpret_cast<const int16_t(*)[2]>(data)[idx];
				return VRayBaseTypes::AttrVector(static_cast<float>(v[0]), static_cast<float>(v[1]), 0.f);
			}
		}
		return VRayBaseTypes::AttrVector(0.f, 0.f, 0.f);
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
	PROPERTY(bool, exportEdgeVisibility, false)
};


struct MeshData
{
	enum class NormalsDomain : int{
		Face   = 0,
		Point  = 1,
		Corner = 2
	};

	MeshData() = delete;

	explicit MeshData(nb::object meshData);

	std::string                      name;
	NormalsDomain				     normalsDomain;		// Type of normals in the 'normals' field
	std::span<const float[3]>        vertices;			// Mesh vertices
	std::span<const unsigned int>    loops;				// Loop indices of faces
	std::span<const unsigned int[3]> loopTris;			// Loop triangles
	std::span<const unsigned int>    loopTriPolys;		// Loop triangle polygon indices
	std::span<const int>             cornerEdges;		// Edge index per loop/corner (Blender '.corner_edge')
	std::span<const float>           edgeCreases;		// Raw Blender crease float, one per edge (0..1)
	std::span<const int[2]>          edgeVertices;		// Blender int2, vertex index pair per edge
	std::span<const float>           vertexCreases;	// Raw Blender crease float, one per vertex (0..1)
	std::span<const unsigned int>    polyMtlIndices;	// Material index per polygon
	int                              mtlIdOffset = 0; // Added per face in fillFaces (proxy export)
	std::span<const float[3]>        normals;			// Normals

	std::vector<UVAttrLayer> uvLayers;
	std::vector<AttrLayer> colorLayers;

	Subdiv subdiv;
	MeshExportOptions options;

public:
	nb::object ref;              // Reference to the original Python object
};

using MeshDataPtr = std::shared_ptr<MeshData>;


struct HairData
{
	explicit HairData(nb::object obj);

	std::string        name;
	std::string        type;                    // "CURVES" or "PARTICLES"
	bool               widthsInPixels = false;  // Is strand width specified in pixels?
	bool               useHairBSpline = false;
	std::vector<float> matWorld;

	std::span<const float[3]> points;           // Array of strand points, float[3] per point
	std::span<const float>    pointRadii;       // Array of float per point (DataArray for CURVES, ndarray for PARTICLES)
	std::span<const int>      strandSegments;   // Per-strand point counts (PARTICLES only)
	std::span<const int>      strandOffsets;    // Raw curve_offsets from Blender, size = strands+1 (CURVES only)
	std::span<const float> uvs;                 // Array of UVs, float[2] per point
	std::span<const float> vertColors;          // Array of colors, float[3] per point

	// Particle hair
	ParticleSystem *psys = nullptr;             // A pointer to the particle system for particle hair.
	int firstToExport = 0;                      // The index of the first particle to export.
	int totalParticles = 0;                     // The index of the last particle to export.
	int maxSteps = 0;                           // The max number of hair subdivisions.
	float shape = 0.0f;                         // The value of the shape parameter of the psys.
	float rootRadius = 0.0f;                    // The root radius of the psys.
	float tipRadius = 0.0f;                     // THe tip radius of the psys.

	nb::object ref;                  // Lifetime ref
};

using HairDataPtr = std::shared_ptr<HairData>;


struct PointCloudData
{
	explicit PointCloudData(nb::object obj);

	std::string name;
	int         renderType;

	std::span<const float[3]> points;  // Array of float[3] per point
	std::span<const float[2]> uvs;     // Array of float[2] per point
	std::span<const float>    radii;   // Array of float per point
	std::span<const float[3]> colors;  // Array of float[3] per point
	nb::object                ref;     // Lifetime ref
};

using PointCloudDataPtr = std::shared_ptr<PointCloudData>;


struct InstancerData
{
	explicit InstancerData(nb::object obj);

	std::string  name;
	int          itemCount = 0;
	nb::object   ids;       // ndarray<int32, (N,8)> — persistent IDs
	nb::object   tms;       // ndarray<float32, (N,12)> — transforms in AttrTransform layout
	nb::object   meshes;    // list[str] — unique mesh plugin names
	nb::object   indices;   // ndarray<int32, (N,)> — per-instance mesh index
	nb::object   ref;
};

using InstancerDataPtr = std::shared_ptr<InstancerData>;


struct SmokeData
{

	explicit SmokeData(const nb::object &obj);

	std::string        name;
	std::string        cacheDir;
	std::vector<float> transform;
	std::vector<int>   domainRes;

	nb::object ref;
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

/// Settings for .vrmesh export (maps to VrayZmqWrapper::ProxyExportSettings / VRay::ProxyCreateParams).
struct ProxyExportSettings
{
	PROPERTY(std::string, filePath, "")
	PROPERTY(int, elementsPerVoxel, 64)
	PROPERTY(int, previewFaces, 10000)
	PROPERTY(int, previewType, 3)
	PROPERTY(bool, animOn, false)
	PROPERTY(int, startFrame, 0)
	PROPERTY(int, endFrame, 0)
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
	PROPERTY(nb::dict, locationsMap, nb::dict())
	PROPERTY(bool, isAnimated, false)
	// Plane dimensions in centimeters for ParallaxInterior assets; zero otherwise.
	PROPERTY(double, planeWidth, 0.0)
	PROPERTY(double, planeHeight, 0.0)
	// Triplanar mapping option selected in the Cosmos browser. When true, material
	// textures are wrapped in TexTriPlanar on import.
	PROPERTY(bool, applyTriplanarMapping, false)
	// Real-world texture dimensions in centimeters supplied by Cosmos. Used to derive
	// the triplanar size; zero when unknown.
	PROPERTY(float, texRealWorldWidth, 0.0f)
	PROPERTY(float, texRealWorldHeight, 0.0f)
};

} // VRayForBlender::Interop
