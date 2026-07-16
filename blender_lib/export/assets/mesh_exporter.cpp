// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "mesh_exporter.h"

#include <array>
#include <charconv>
#include <base_types.h>

#include "api/interop/types.h"
#include "utils/mmh3.h"

#pragma warning(push, 0)
#include <tsl/robin_set.h>
#include <tsl/robin_map.h>
#pragma warning(pop)

using namespace VRayBaseTypes;
using namespace VRayForBlender;

template <typename KeyT, typename HashT = std::hash<KeyT>>
using HashSet = tsl::robin_set<KeyT, HashT>;

template <typename KeyT, typename ValT, typename HashT = std::hash<KeyT>>
using HashMap = tsl::robin_map<KeyT, ValT, HashT>;

using MeshData = Interop::MeshData;


struct ChanVertex
{
	ChanVertex() = default;

	ChanVertex(const AttrVector& vec) : v(vec) {}

	template <std::size_t size>
	explicit ChanVertex(const std::array<float, size> &data)
	{
		static_assert(std::min(std::size_t(3), size) * sizeof(float) <= sizeof(AttrVector));
		std::memcpy(&v, data.data(), std::min(std::size_t(3), size) * sizeof(float));
	}

	template <int size>
	static ChanVertex fromArray(const float (&data)[size])
	{
		std::array<float, size> a;
		::memcpy(a.data(), data, size * sizeof(float));
		return ChanVertex(a);
	}

	inline bool operator==(const ChanVertex &other) const {
		return (v.x == other.v.x) && (v.y == other.v.y) && (v.z == other.v.z);
	}

	AttrVector  v;
	mutable unsigned int index = 0;
};


struct MapVertexHash {
	inline std::size_t operator () (const ChanVertex &mv) const {
		MHash hash;
		MurmurHash3_x86_32(&mv.v, sizeof(AttrVector), 42, &hash);
		return static_cast<std::size_t>(hash);
	}
};

typedef HashSet<ChanVertex, MapVertexHash> ChannelSet;
typedef std::vector<ChannelSet>   ChannelMapList;


struct MapChannelBase {
	MapChannelBase(const MeshData& meshParameter, int numFacesParameter):
		mesh(meshParameter),
		numFaces(numFacesParameter),
		numChannels(int(mesh.uvLayers.size() + mesh.colorLayers.size()))
	{
	}

	virtual void init() {}
	virtual void initAttributes(AttrListString &mapChannelsNames, AttrMapChannels &mapChannels)=0;
	virtual bool needProcessFaces() const { return false; }
	virtual int  getMapFaceVertexIndex(int, const ChanVertex&) { return -1; }

	int getNumChannels() const {
		return numChannels;
	}

protected:
	const MeshData&  mesh; //< The mesh data passesd from python
	const int        numChannels; //< The number of map channels(uv maps and attributes)
	const int        numFaces; //< The number of faces in the mesh

};

/// Parse the explicit V-Ray channel ID from a UV layer name following the 'vray_channel_id_N' convention.
/// Returns N if the name matches, or -1 to indicate sequential positioning.
static int parseUVLayerChannelId(const std::string& name) {
	constexpr std::string_view prefix = "vray_channel_id_";
	if (name.size() > prefix.size() && name.compare(0, prefix.size(), prefix.data()) == 0) {
		int result = -1;
		const char* begin = name.data() + prefix.size();
		const char* end = name.data() + name.size();
		if (auto [ptr, ec] = std::from_chars(begin, end, result); ec == std::errc{} && ptr == end) {
			return result;
		}
	}
	return -1;
}


/// Convert EDGE-domain attribute to POINT domain by averaging adjacent edge values per vertex.
/// Matches Cycles' domain conversion: AttrDomain::Edge → AttrDomain::Point.
static std::vector<AttrVector> edgeToPointDomain(const Interop::AttrLayer& layer, const MeshData& mesh) {
	const size_t numVerts = mesh.vertices.size();
	std::vector<float> ax(numVerts, 0.f), ay(numVerts, 0.f), az(numVerts, 0.f);
	std::vector<int>   cnt(numVerts, 0);

	for (size_t ci = 0; ci < mesh.loops.size(); ++ci) {
		const unsigned int vi = mesh.loops[ci];
		const AttrVector ev = layer.getAttrVector(static_cast<unsigned int>(mesh.cornerEdges[ci]));
		ax[vi] += ev.x;
		ay[vi] += ev.y;
		az[vi] += ev.z;
		cnt[vi]++;
	}

	std::vector<AttrVector> result;
	result.reserve(numVerts);
	for (size_t vi = 0; vi < numVerts; ++vi) {
		if (cnt[vi] > 0) {
			const float inv = 1.f / static_cast<float>(cnt[vi]);
			result.emplace_back(ax[vi] * inv, ay[vi] * inv, az[vi] * inv);
		} else {
			result.emplace_back(0.f, 0.f, 0.f);
		}
	}
	return result;
}


struct MapChannelRaw: MapChannelBase
{
	MapChannelRaw(const MeshData& mesh, int numFaces):
		MapChannelBase(mesh, numFaces)
	{}

	virtual void initAttributes(AttrListString &mapChannelsNames, AttrMapChannels &mapChannels) override {
		if (numChannels) {
			// UV
			for (const auto& uvLayer : mesh.uvLayers) {
				AttrMapChannels::AttrMapChannel &mapChannel = mapChannels.data.emplace_back();
				mapChannel.name = uvLayer.name;
				mapChannel.channelId = parseUVLayerChannelId(uvLayer.name);
				mapChannel.vertices.reserve(numFaces * 3);
				mapChannel.faces.resize(numFaces * 3);

				// Fill vertex data
				std::vector<AttrVector>& vertsVec = *mapChannel.vertices.getData();
				for (const auto& face : mesh.loopTris) {
					for (int vi = 0; vi < 3; ++vi){
						const float *uv = uvLayer.data[face[vi]];
						vertsVec.emplace_back(uv[0], uv[1], 0.f);
					}
				}

				// Fill faces
				std::vector<int>& facesVec = *mapChannel.faces.getData();
				const int numFaceIds = static_cast<int>(facesVec.size());
				for (int i = 0; i < numFaceIds; ++i) {
					facesVec[i] = i;
				}
			}

			// COLOR
			for (const auto& colorLayer : mesh.colorLayers) {
				AttrMapChannels::AttrMapChannel &mapChannel = mapChannels.data.emplace_back();
				mapChannel.name = colorLayer.name;
				mapChannel.vertices.reserve(int(colorLayer.elementCount));
				mapChannel.faces.reserve(numFaces * 3);

				std::vector<AttrVector>& vertexData = *mapChannel.vertices.getData();
				if (colorLayer.domain == Interop::AttrLayer::Edge) {
					const auto perVertex = edgeToPointDomain(colorLayer, mesh);
					vertexData.insert(vertexData.end(), perVertex.begin(), perVertex.end());
				} else {
					for (int i = 0; i < int(colorLayer.elementCount); i++) {
						vertexData.push_back(colorLayer.getAttrVector(i));
					}
				}

				// Fill faces
				std::vector<int>& facesData = *mapChannel.faces.getData();
				switch (colorLayer.domain) {
					case Interop::AttrLayer::Corner:
						for (const auto& ltri : mesh.loopTris) {
							facesData.push_back(ltri[0]);
							facesData.push_back(ltri[1]);
							facesData.push_back(ltri[2]);
						}
						break;
					case Interop::AttrLayer::Point:
					case Interop::AttrLayer::Edge:
						for (const auto& ltri : mesh.loopTris) {
							facesData.push_back(mesh.loops[ltri[0]]);
							facesData.push_back(mesh.loops[ltri[1]]);
							facesData.push_back(mesh.loops[ltri[2]]);
						}
						break;
					case Interop::AttrLayer::Face:
						for (size_t fi = 0; fi < mesh.loopTris.size(); ++fi) {
							const unsigned int pi = mesh.loopTriPolys[fi];
							facesData.push_back(pi);
							facesData.push_back(pi);
							facesData.push_back(pi);
						}
						break;
				}
			}

			// Store channel names
			mapChannelsNames.resize(numChannels);
			int i = 0;
			for (const auto &mapChannel : mapChannels.data) {
				(*mapChannelsNames)[i++] = mapChannel.name;
			}
		}
	}
};

// An implementation for exporting merged map_channels(only used for production renders).
// Vertices which are exactly the same(have the same hash) are merged into one and
// exported with their respective face ids. The export will be a lot slower but sending
// the data over to the server, rendering and tree building will potentially be faster.
struct MapChannelMerge : MapChannelBase {
	MapChannelMerge(const MeshData& mesh, int numFaces) :
		MapChannelBase(mesh, numFaces)
	{}

	virtual void init() override {
		if (numChannels) {
			for (const Interop::UVAttrLayer& uvLayer : mesh.uvLayers) {
				ChannelSet& uvSet = channelsData.emplace_back();
				for (const auto& face : mesh.loopTris) {
					for (int vi = 0; vi < 3; vi++) {
						// Use auto reference so it doesn't decay to float*(because then we can't
						// call fromArray(...). This should probably be reworked...
						const auto& uv = uvLayer.data[face[vi]];
						uvSet.insert(ChanVertex::fromArray(uv));
					}
				}
			}
			for (const Interop::AttrLayer& colorLayer : mesh.colorLayers) {
				ChannelSet& colorSet = channelsData.emplace_back();

				if (colorLayer.domain == Interop::AttrLayer::Edge) {
					for (const auto& v : edgeToPointDomain(colorLayer, mesh)) {
						colorSet.insert(v);
					}
				} else {
					for (int i = 0; i < int(colorLayer.elementCount); i++) {
						colorSet.insert(colorLayer.getAttrVector(i));
					}
				}
			}
		}
	}

	virtual void initAttributes(AttrListString& mapChannelNames, AttrMapChannels& mapChannels) override {
		if (numChannels) {
			auto processMapList = [&](const std::string& channelName, int channelIdx, int channelId = -1) {
				AttrMapChannels::AttrMapChannel& mapChannel = mapChannels.data.emplace_back();
				mapChannel.name = channelName;
				mapChannel.channelId = channelId;
				ChannelSet &channelSet=channelsData[channelIdx];
				mapChannel.vertices.reserve(static_cast<int>(channelSet.size()));
				mapChannel.faces.resize(numFaces * 3);

				unsigned int face = 0;
				std::vector<AttrVector>& vertexData = *mapChannel.vertices.getData();
				for (const ChanVertex& mapVertex : channelSet) {
					// Set vertex index for lookup from faces
					mapVertex.index = face++;

					// Store channel vertex
					vertexData.push_back(mapVertex.v);
				}
			};

			int mapChannelIndex = 0;
			for (const Interop::UVAttrLayer& uvLayer : mesh.uvLayers) {
				processMapList(uvLayer.name, mapChannelIndex++, parseUVLayerChannelId(uvLayer.name));
			}
			for (const Interop::AttrLayer& colorLayer : mesh.colorLayers) {
				processMapList(colorLayer.name, mapChannelIndex++);
			}

			mapChannelNames.resize(numChannels);
			int i = 0;
			for (const auto& mapChannel : mapChannels.data) {
				(*mapChannelNames)[i++] = mapChannel.name;
			}
		}
	}

	virtual bool needProcessFaces() const override { return true; }

	virtual int getMapFaceVertexIndex(int layerIndex, const ChanVertex& cv) override {
		return channelsData[layerIndex].find(cv)->index;
	}

private:
	ChannelMapList channelsData;
};


namespace VRayForBlender::Assets
{

static void fillCreases(const MeshData& mesh, PluginDesc& pluginDesc);

/// Resolve final channel IDs for all map channels.
/// Channels with an explicit ID (from 'vray_channel_id_N' UV layers) keep it.
/// Channels left at -1 are assigned sequential IDs starting above the highest
/// explicit ID so they can never collide with a reserved one.
static void resolveChannelIds(AttrMapChannels& mapChannels) {
	int maxExplicitId = -1;
	for (const auto& channel : mapChannels.data) {
		if (channel.channelId >= 0) {
			maxExplicitId = std::max(maxExplicitId, channel.channelId);
		}
	}
	int nextId = maxExplicitId + 1;
	for (auto& channel : mapChannels.data) {
		if (channel.channelId < 0) {
			channel.channelId = nextId++;
		}
	}
}

/// @brief Export all geometry and data layers/channels for a single Blender object of type 'MESH'
/// as a 'GeomStaticMesh' pugin
/// @param mesh - mesh data
/// @param pluginDesc - GeomStaticMesh plugin descriptor
void fillMeshData(const MeshData& mesh, PluginDesc &pluginDesc)
{
	pluginDesc.add("osd_subdiv_enable", mesh.subdiv.enabled);
	if (mesh.subdiv.enabled){
		pluginDesc.add("osd_subdiv_uvs", mesh.subdiv.useCreases);
		pluginDesc.add("osd_subdiv_level", mesh.subdiv.level);
		pluginDesc.add("osd_subdiv_type", mesh.subdiv.type);
		// TODO : VRAY MISSING SUBSURF UVs
	}

	fillGeometry(mesh, pluginDesc);
	fillChannelsData(mesh, pluginDesc);
	fillCreases(mesh, pluginDesc);

	pluginDesc.add("dynamic_geometry", mesh.options.forceDynamicGeometry);
}


static void fillEdgeVisibility(const MeshData& mesh, AttrListInt& edgeVisibility) {
	const int numTriangles = static_cast<int>(mesh.loopTris.size());
	int* evPtr = *edgeVisibility;

	int bitBuffer = 0;
	int bitCount = 0;
	int evIdx = 0;

	auto getEdgeKey = [](unsigned int a, unsigned int b) {
		return (a < b) ? (((uint64_t)a << 32) | b) : (((uint64_t)b << 32) | a);
	};
	auto checkBitCount = [&] {
		if (bitCount >= 30) {
			evPtr[evIdx++] = bitBuffer;
			bitBuffer = 0;
			bitCount = 0;
		}
	};

	HashMap<uint64_t, int> edgeCounts; // Map to store all edges of a single face
	for (int fi = 0; fi < numTriangles; ) {
		const unsigned int pi = mesh.loopTriPolys[fi];
		int nextFi = fi;
		while (nextFi < numTriangles && mesh.loopTriPolys[nextFi] == pi) {
			nextFi++;
		}
		if (nextFi - fi == 1) {
			// For triangle faces we can skip the more complex checks below.
			bitBuffer |= (0b111 << bitCount);
			bitCount += 3;
			checkBitCount();
			fi = nextFi;
			continue;
		}
		// Triangles in [fi, nextFi) belong to the same polygon pi.
		// An edge is an "original" edge of the polygon if it belongs to exactly one triangle in this set.
		for (int i = fi; i < nextFi; i++) {
			const auto& ltri = mesh.loopTris[i];
			const unsigned int v0 = mesh.loops[ltri[0]];
			const unsigned int v1 = mesh.loops[ltri[1]];
			const unsigned int v2 = mesh.loops[ltri[2]];

			edgeCounts[getEdgeKey(v0, v1)]++;
			edgeCounts[getEdgeKey(v1, v2)]++;
			edgeCounts[getEdgeKey(v2, v0)]++;
		}

		for (int i = fi; i < nextFi; i++) {
			const auto& ltri = mesh.loopTris[i];
			const unsigned int v0 = mesh.loops[ltri[0]];
			const unsigned int v1 = mesh.loops[ltri[1]];
			const unsigned int v2 = mesh.loops[ltri[2]];

			int triBits = 0;
			if (edgeCounts[getEdgeKey(v0, v1)] == 1) triBits |= (1 << 0);
			if (edgeCounts[getEdgeKey(v1, v2)] == 1) triBits |= (1 << 1);
			if (edgeCounts[getEdgeKey(v2, v0)] == 1) triBits |= (1 << 2);

			bitBuffer |= (triBits << bitCount);
			bitCount += 3;

			checkBitCount();
		}

		fi = nextFi;
		edgeCounts.clear();
	}

	if (bitCount > 0) {
		evPtr[evIdx++] = bitBuffer;
	}
}


void fillFaces(const MeshData& mesh, AttrListInt& faces, AttrListInt& faceMtlIDs) {
	// fi - current face index
	// vi - current vertex index
	int* facesPtr = *faces;
	for (int fi = 0; fi < int(mesh.loopTris.size()); fi++) {
		// Face
		const auto& ltri = mesh.loopTris[fi];

		// Face vertex indices
		const unsigned int fvi0 = mesh.loops[ltri[0]];
		const unsigned int fvi1 = mesh.loops[ltri[1]];
		const unsigned int fvi2 = mesh.loops[ltri[2]];

		// Faces as 3 vertex indices each
		*facesPtr++ = fvi0;
		*facesPtr++ = fvi1;
		*facesPtr++ = fvi2;
	}
	int* faceMtlIdsPtr = *faceMtlIDs;
	if (!mesh.polyMtlIndices.empty()) {
		for (int fi = 0; fi < int(mesh.loopTris.size()); fi++) {
			// Polygon index
			const unsigned int polyIdx = mesh.loopTriPolys[fi];
			// Face material ID (local polygon index + optional global proxy slot offset)
			faceMtlIdsPtr[fi] = static_cast<int>(mesh.polyMtlIndices[polyIdx] + mesh.mtlIdOffset);
		}
	}
}


void fillFaceNormalsFromFaces(const MeshData& mesh, AttrListInt& faceNormals) {
	int* normalsFacePtr = *faceNormals;
	for (int fi = 0; fi < int(mesh.loopTris.size()); fi++) {
		const unsigned int polyIdx = mesh.loopTriPolys[fi];

		*normalsFacePtr++ = polyIdx;
		*normalsFacePtr++ = polyIdx;
		*normalsFacePtr++ = polyIdx;
	}
}


void fillFaceNormalsFromVertices(const MeshData& mesh, AttrListInt& faceNormals) {
	int* normalsFacePtr = *faceNormals;
	for (int fi = 0; fi < int(mesh.loopTris.size()); fi++) {

		// Face
		const auto& ltri = mesh.loopTris[fi];

		// Face vertex indices
		const unsigned int fvi0 = mesh.loops[ltri[0]];
		const unsigned int fvi1 = mesh.loops[ltri[1]];
		const unsigned int fvi2 = mesh.loops[ltri[2]];

		*normalsFacePtr++ = fvi0;
		*normalsFacePtr++ = fvi1;
		*normalsFacePtr++ = fvi2;
	}
}


void fillFaceNormalsFromCorners(const MeshData& mesh, AttrListInt& faceNormals) {
	// Corner normals are ordered the same way as the face vertices
	std::memcpy(*faceNormals, mesh.loopTris.data(), mesh.loopTris.size() * sizeof(unsigned int[3]));
}


static float creaseToSharpness(float crease) {
	if (crease >= 1.0f) return 10.0f;
	return crease / (1.0f - crease);
}


static void fillCreases(const MeshData& mesh, PluginDesc& pluginDesc) {
	if (!mesh.edgeCreases.empty()) {
		AttrListInt   ev;
		AttrListFloat es;

		for (int e = 0; e < static_cast<int>(mesh.edgeCreases.size()); ++e) {
			const float crease = mesh.edgeCreases[e];
			if (crease > 0.0f) {
				ev.append(mesh.edgeVertices[e][0]);
				ev.append(mesh.edgeVertices[e][1]);
				es.append(creaseToSharpness(crease));
			}
		}
		if (ev.getCount() > 0) {
			pluginDesc.add("edge_creases_vertices",  ev);
			pluginDesc.add("edge_creases_sharpness", es);
		}
	}

	if (!mesh.vertexCreases.empty()) {
		AttrListInt   vv;
		AttrListFloat vs;

		for (int v = 0; v < static_cast<int>(mesh.vertexCreases.size()); ++v) {
			const float crease = mesh.vertexCreases[v];
			if (crease > 0.0f) {
				vv.append(v);
				vs.append(creaseToSharpness(crease));
			}
		}
		if (vv.getCount() > 0) {
			pluginDesc.add("vertex_creases_vertices",  vv);
			pluginDesc.add("vertex_creases_sharpness", vs);
		}
	}
}


void fillGeometry(const MeshData& mesh, PluginDesc& pluginDesc) {
	const auto numFaces = static_cast<int>(mesh.loopTris.size());
	AttrListVector  vertices(static_cast<int>(mesh.vertices.size()));
	AttrListVector  normals(static_cast<int>(mesh.normals.size())); // Normals list
	AttrListInt     faces(numFaces * 3);					        // Face vertex indices
	AttrListInt     faceNormals(numFaces * 3);			            // Normals per face vertex - indices
	AttrListInt     faceMtlIDs(numFaces);				            // Material index per face

	std::memcpy(*vertices, mesh.vertices.data(), mesh.vertices.size() * sizeof(float) * 3);
	std::memcpy(*normals, mesh.normals.data(), mesh.normals.size() * sizeof(float) * 3);

	fillFaces(mesh, faces, faceMtlIDs);

	if (mesh.options.exportEdgeVisibility) {
		AttrListInt edgeVisibility((numFaces + 9) / 10); // 10 tris per int
		fillEdgeVisibility(mesh, edgeVisibility);
		pluginDesc.add("edge_visibility", edgeVisibility);
	}

	switch (mesh.normalsDomain){
		case MeshData::NormalsDomain::Face:
			fillFaceNormalsFromFaces(mesh, faceNormals);
			break;

		case MeshData::NormalsDomain::Point:
			fillFaceNormalsFromVertices(mesh, faceNormals);
			break;

		case MeshData::NormalsDomain::Corner:
			fillFaceNormalsFromCorners(mesh, faceNormals);
			break;

		default:
			vassert(!"Invalid normals domain type");
	}

	pluginDesc.add("vertices", vertices);
	pluginDesc.add("faces", faces);
	pluginDesc.add("normals", normals);
	pluginDesc.add("faceNormals", faceNormals);
	pluginDesc.add("face_mtlIDs", faceMtlIDs);
}


void fillChannelsData(const MeshData& mesh, PluginDesc &pluginDesc) {
	const int numFaces = static_cast<int>(mesh.loopTris.size());

	AttrListString  mapChannelNames;
	AttrMapChannels mapChannels;
	MapChannelMerge mergeMapChannel(mesh, numFaces);
	MapChannelRaw rawMapChannel(mesh, numFaces);

	MapChannelBase *channelsData = nullptr;
	if (mesh.options.mergeChannelVerts) {
		channelsData = &mergeMapChannel;
	} else {
		channelsData = &rawMapChannel;
	}

	// Init channels with the actual per-vertex values of uvs and colors
	channelsData->init();
	channelsData->initAttributes(mapChannelNames, mapChannels);

	if (channelsData->getNumChannels() && channelsData->needProcessFaces()) {
		// Now that we have all per-vertex values stored, set indices into the created lists
		// to the face(tris) vertices
		int mapChannelIndex = 0;
		for (const auto& uvLayer : mesh.uvLayers) {
			// Store tris' UVs as indices into the UV map for each UV layer
			int channelVertIndex = 0;
			vassert(mapChannelIndex < int(mapChannels.data.size()));
			vassert(uvLayer.name == mapChannels.data[mapChannelIndex].name);
			int* uvFaces = *mapChannels.data[mapChannelIndex].faces;
			for (const auto& face : mesh.loopTris) {
				for (size_t vi = 0; vi < 3; ++vi) {
					// Use auto reference so it doesn't decay to float*(because then we can't
					// call fromArray(...). This should probably be reworked...
					const auto& uv = uvLayer.data[face[vi]];
					const int faceId = channelsData->getMapFaceVertexIndex(mapChannelIndex, ChanVertex::fromArray(uv));
					uvFaces[channelVertIndex++] = faceId;
				}
			}
			vassert(channelVertIndex == mapChannels.data[mapChannelIndex].faces.getCount() && "Vertex index of UV layer is out of range");
			mapChannelIndex++;
		}

		for (const auto& colorLayer : mesh.colorLayers) {
			// Store tris' vertex colors as indices into the vertex colors map for each color layer
			int channelVertIndex = 0;
			vassert(mapChannelIndex < int(mapChannels.data.size()));
			vassert(colorLayer.name == mapChannels.data[mapChannelIndex].name);
			AttrListInt& colorData = mapChannels.data[mapChannelIndex].faces;
			auto& facesData = *colorData.getData();

			const std::vector<AttrVector> edgePerVertex =
				(colorLayer.domain == Interop::AttrLayer::Edge) ? edgeToPointDomain(colorLayer, mesh) : std::vector<AttrVector>{};

			auto resolveDataIndex = [&](const auto& face, size_t vi, size_t fi) -> unsigned int {
				switch (colorLayer.domain) {
					case Interop::AttrLayer::Corner: return face[vi];
					case Interop::AttrLayer::Point:  return mesh.loops[face[vi]];
					case Interop::AttrLayer::Edge:   return mesh.loops[face[vi]];
					case Interop::AttrLayer::Face:   return mesh.loopTriPolys[fi];
				}
				return 0;
			};

			for (size_t fi = 0; fi < mesh.loopTris.size(); ++fi) {
				const auto& face = mesh.loopTris[fi];
				for (size_t vi = 0; vi < 3; ++vi) {
					const unsigned int dataIdx = resolveDataIndex(face, vi, fi);
					const AttrVector vertexColor = (colorLayer.domain == Interop::AttrLayer::Edge)
						? edgePerVertex[dataIdx]
						: colorLayer.getAttrVector(dataIdx);
					const int faceId = channelsData->getMapFaceVertexIndex(mapChannelIndex, vertexColor);
					facesData[channelVertIndex++] = faceId;
				}
			}
			mapChannelIndex++;
			vassert(channelVertIndex == colorData.getCount() && "Vertex index of color layer is out of range");
		}
	}

	if (channelsData->getNumChannels() ) {
		resolveChannelIds(mapChannels);
		pluginDesc.add("map_channels_names", mapChannelNames);
		pluginDesc.add("map_channels", mapChannels);
	}
}

} // end namespace VRayForBlender::Assets
