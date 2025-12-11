#include "mesh_exporter.h"

#include <array>
#include <unordered_set>
#include <unordered_map>
#include <base_types.h>

#include "api/interop/types.h"
#include "utils/mmh3.h"


using namespace VRayBaseTypes;
using namespace VRayForBlender;

template <typename KeyT, typename HashT = std::hash<KeyT>>
using HashSet = std::unordered_set<KeyT, HashT>;

template <typename KeyT, typename ValT, typename HashT = std::hash<KeyT>>
using HashMap = std::unordered_map<KeyT, ValT, HashT>;

using MeshData = Interop::MeshData;


struct ChanVertex
{
	ChanVertex() { }

	ChanVertex(const AttrVector& vec) : v(vec) {}

	template <std::size_t size>
	ChanVertex(const std::array<float, size> &data)
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
		numChannels(boost::numeric_cast<int>(mesh.uvLayers.size() + mesh.colorLayers.size()))
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
				for (int i = 0; i < int(numFaceIds); ++i) {
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
				for (int i = 0; i < int(colorLayer.elementCount); i++) {
					vertexData.push_back(colorLayer.getAttrVector(i));
				}

				// Fill faces
				if (colorLayer.domain == Interop::AttrLayer::Corner) {
					std::vector<int>& facesData = *mapChannel.faces.getData();
					for (int fi = 0; fi < static_cast<int>(mesh.loopTris.size()); ++fi) {
						const auto& ltri = mesh.loopTris[fi];
						facesData.push_back(ltri[0]);
						facesData.push_back(ltri[1]);
						facesData.push_back(ltri[2]);
					}
				} else {
					std::vector<int>& facesData = *mapChannel.faces.getData();
					for (const auto& face : mesh.loopTris) {
						const unsigned int fvi0 = mesh.loops[face[0]];
						const unsigned int fvi1 = mesh.loops[face[1]];
						const unsigned int fvi2 = mesh.loops[face[2]];
						facesData.push_back(fvi0);
						facesData.push_back(fvi1);
						facesData.push_back(fvi2);
					}
				}
			}

			// Store channel names
			mapChannelsNames.resize(static_cast<int>(mapChannels.data.size()));
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

				for (int i = 0; i < int(colorLayer.elementCount); i++) {
					colorSet.insert(colorLayer.getAttrVector(i));
				}
			}
		}
	}

	virtual void initAttributes(AttrListString& mapChannelNames, AttrMapChannels& mapChannels) override {
		if (numChannels) {
			auto processMapList = [&](const std::string& channelName, int channelIdx) {
				AttrMapChannels::AttrMapChannel& mapChannel = mapChannels.data.emplace_back();
				mapChannel.name = channelName;
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
				processMapList(uvLayer.name, mapChannelIndex++);
			}
			for (const Interop::AttrLayer& colorLayer : mesh.colorLayers) {
				processMapList(colorLayer.name, mapChannelIndex++);
			}

			// Store channel names
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

	pluginDesc.add("dynamic_geometry", mesh.options.forceDynamicGeometry);
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
			// Face material ID
			faceMtlIdsPtr[fi] = mesh.polyMtlIndices[polyIdx];
		}
	}
}


void fillFaceNormalsFromFaces(const MeshData& mesh, AttrListInt& faceNormals) {
	int* normalsFacePtr = *faceNormals;
	for (int fi = 0; fi < mesh.loopTris.size(); fi++) {
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
	int* normalsFacePtr = *faceNormals;
	for (int fi = 0; fi < int(mesh.loopTris.size()); fi++) {
		const auto& face = mesh.loopTris[fi];
		// Corner normals are ordered the same way as the face vertices
		std::memcpy(normalsFacePtr, face, sizeof(face));
		normalsFacePtr+=3;
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
			if (colorLayer.domain == Interop::AttrLayer::Corner) {
				auto& facesData = *colorData.getData();
				for (const auto& face : mesh.loopTris) {
					for (size_t vi = 0; vi < 3; ++vi) {
						const AttrVector& vertexColor = colorLayer.getAttrVector(face[vi]);
						const int faceId = channelsData->getMapFaceVertexIndex(mapChannelIndex, vertexColor);
						facesData[channelVertIndex++] = faceId;
					}
				}
			} else {
				auto& facesData = *colorData.getData();
				for (const auto& face : mesh.loopTris) {
					for (size_t vi = 0; vi < 3; ++vi) {
						const unsigned int faceVertexIdx = mesh.loops[face[vi]];
						const AttrVector& vertexColor = colorLayer.getAttrVector(faceVertexIdx);
						const int faceId = channelsData->getMapFaceVertexIndex(mapChannelIndex, vertexColor);
						facesData[channelVertIndex++] = faceId;
					}
				}
			}
			mapChannelIndex++;
			vassert(channelVertIndex == colorData.getCount() && "Vertex index of color layer is out of range");
		}
	}

	if (channelsData->getNumChannels() ) {
		pluginDesc.add("map_channels_names", mapChannelNames);
		pluginDesc.add("map_channels", mapChannels);
	}
}

} // end namespace VRayForBlender::Assets
