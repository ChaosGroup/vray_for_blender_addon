#include "mesh_exporter.h"

#include <array>
#include <thread>
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
	ChanVertex()
		: index(0)
	{}

	template <int size>
	ChanVertex(const std::array<float, size> &data)
		: index(0)
	{
		float *dest = &v.x;
		for (int c = 0; c < std::min(3, size); c++) {
			dest[c] = data[c];
		}
	}

	// TODO: Optimize this, should probably be a new constructor
	template <int size>
	static ChanVertex fromArray(const float data[size])
	{
		std::array<float, size> a;
		::memcpy(a.data(), data, size * sizeof(float));
		return ChanVertex(a);
	}

	static ChanVertex fromRGBbytes(char r, char g, char b)
	{
		const float clr[3] = { clrByteToFlt(r), clrByteToFlt(g), clrByteToFlt(b) };
		return ChanVertex::fromArray<3>(clr);
	}

	bool operator == (const ChanVertex &other) const {
		return (v.x == other.v.x) && (v.y == other.v.y) && (v.z == other.v.z);
	}
	
	static inline float clrByteToFlt(char v)
	{	
		static const float mult = 1.0f / 255.f;
		return static_cast<float>(v) * mult;
	}

	AttrVector   v;
	mutable int  index;
};


struct MapVertexHash {
	std::size_t operator () (const ChanVertex &mv) const {
		MHash hash;
		MurmurHash3_x86_32(&mv.v, sizeof(AttrVector), 42, &hash);
		return static_cast<std::size_t>(hash);
	}
};

typedef HashSet<ChanVertex, MapVertexHash>  ChanSet;
typedef HashMap<std::string, ChanSet>       ChanMap;



struct MapChannelBase {
	MapChannelBase(const MeshData& mesh, int numFaces):
		mesh(mesh),
		num_faces(numFaces)
	{
		num_channels = boost::numeric_cast<int>(mesh.uvLayers.size() + mesh.colorLayers.size());
	}

	virtual void init()=0;
	virtual void initAttributes(AttrListString &map_channels_names, AttrMapChannels &map_channels)=0;
	virtual bool needProcessFaces() const { return false; }
	virtual int  getMapFaceVertexIndex(const std::string&, const ChanVertex&) { return -1; }

	int numChannels() const {
		return num_channels;
	}

protected:
	const MeshData&  mesh;
	int       num_channels;
	int       num_faces;

};

struct MapChannelRaw: MapChannelBase
{
	MapChannelRaw(const MeshData& mesh, int numFaces):
		MapChannelBase(mesh, numFaces)
	{}

	virtual void init() override {}
	virtual void initAttributes(AttrListString &map_channels_names, AttrMapChannels &map_channels) override {

		if (num_channels) {

			// UV 
			for (const auto& uvLayer : mesh.uvLayers) {

				AttrMapChannels::AttrMapChannel &map_channel = map_channels.data[uvLayer.name];
				map_channel.name = uvLayer.name;
				map_channel.vertices.resize(num_faces * 3);
				map_channel.faces.resize(num_faces * 3);

				// Fill data
				size_t chanVertIndex = 0;
				for( const auto& face : mesh.loopTris ) {
					for(size_t vi = 0; vi < 3; ++vi){
						const float *uv = uvLayer.data[face[vi]];
						(*map_channel.vertices)[chanVertIndex++] = AttrVector(uv[0], uv[1], 0.f);
					}
				}			
			}

			// COLOR
			for (const auto& colorLayer : mesh.colorLayers) {

				AttrMapChannels::AttrMapChannel &map_channel = map_channels.data[colorLayer.name];
				map_channel.name = colorLayer.name;
				map_channel.vertices.resize(num_faces * 3);
				map_channel.faces.resize(num_faces * 3);

				// Fill data
				size_t chanVertIndex = 0;
				for( const auto& face : mesh.loopTris ) {
					for(size_t vi = 0; vi < 3; ++vi){
						const MLoopCol& loop = colorLayer.data[face[vi]];
						(*map_channel.vertices)[chanVertIndex++] = AttrVector(	ChanVertex::clrByteToFlt(loop.r), 
																				ChanVertex::clrByteToFlt(loop.g), 
																				ChanVertex::clrByteToFlt(loop.b));
					}
				}	
			}


			// Setup face data
			for (auto &mcIt : map_channels.data) {
				for (int i = 0; i < int(mcIt.second.faces.getData()->size()); ++i) {
					(*mcIt.second.faces)[i] = i;
				}
			}

			// Store channel names
			map_channels_names.resize(static_cast<int>(map_channels.data.size()));
			int i = 0;
			for (const auto &mcIt : map_channels.data) {
				(*map_channels_names)[i++] = mcIt.second.name;
			}
		}
	}
};




struct MapChannelMerge : MapChannelBase
{
	MapChannelMerge(const MeshData& mesh, int numFaces) :
		MapChannelBase(mesh, numFaces)
	{}

	virtual void init() override {
		if (num_channels) {
			for (const auto& face : mesh.loopTris) {
				for (const auto& uvLayer : mesh.uvLayers) {
					ChanSet& uvSet = chan_data[uvLayer.name];

					for (size_t vi = 0; vi < 3; ++vi) {
						const float *uv = uvLayer.data[face[vi]];
						uvSet.insert(ChanVertex::fromArray<2>(uv));
					}
				}

				for (const auto& colorLayer : mesh.colorLayers) {
					ChanSet& colSet = chan_data[colorLayer.name];

					for (size_t vi = 0; vi < 3; ++vi) {
						const MLoopCol& loop = colorLayer.data[face[vi]];
						colSet.insert(ChanVertex::fromRGBbytes(loop.r, loop.g, loop.b));
					}
				}
			}
		}
	}

	virtual void initAttributes(AttrListString& map_channels_names, AttrMapChannels& map_channels) override {
		if (num_channels) {
			for (ChanMap::iterator setsIt = chan_data.begin(); setsIt != chan_data.end(); ++setsIt) {
				const std::string& chanName = setsIt->first;
				ChanSet& chanData = setsIt->second;

				// Setup channel data storage
				AttrMapChannels::AttrMapChannel& map_channel = map_channels.data[chanName];
				map_channel.name = chanName;
				map_channel.vertices.resize(static_cast<int>(chanData.size()));
				map_channel.faces.resize(num_faces * 3);

				int f = 0;
				for (ChanSet::iterator setIt = chanData.begin(); setIt != chanData.end(); ++setIt, ++f) {
					const ChanVertex& map_vertex = *setIt;

					// Set vertex index for lookup from faces
					map_vertex.index = f;

					// Store channel vertex
					(*map_channel.vertices)[f] = map_vertex.v;
				}
			}

			// Store channel names
			map_channels_names.resize(num_channels);
			int i = 0;
			for (const auto& mcIt : map_channels.data) {
				(*map_channels_names)[i++] = mcIt.second.name;
			}
		}
	}

	virtual bool needProcessFaces() const override { return true; }

	virtual int getMapFaceVertexIndex(const std::string& layerName, const ChanVertex& cv) override {
		return chan_data[layerName].find(cv)->index;
	}

private:
	ChanMap  chan_data;

};


namespace VRayForBlender::Assets
{

/// @brief Export all geometry and data layers/channels for a single Blender object of type 'MESH'
/// as a 'GeomStaticMesh' pugin
/// @param mesh - mesh data
/// @param options 
/// @param pluginDesc - GeomStaticMesh plugin descriptor
/// @return TODO - remove if not used
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



void fillFaces(const MeshData& mesh, AttrListInt& faces, AttrListInt& face_mtlIDs) {
	// fi - current face index 
	// vi - current vertex index
	for (size_t fi = 0, vi = 0; fi < mesh.loopTris.size(); ++fi, vi += 3) {

		// Face
		const auto& ltri = mesh.loopTris[fi];

		// Face vertex indices
		const auto fvi0 = mesh.loops[ltri[0]];
		const auto fvi1 = mesh.loops[ltri[1]];
		const auto fvi2 = mesh.loops[ltri[2]];

		// Faces as 3 vertex indices each
		(*faces)[vi] = fvi0;
		(*faces)[vi + 1] = fvi1;
		(*faces)[vi + 2] = fvi2;


		// Polygon index
		const auto pi = mesh.loopTriPolys[fi];

		// Face material ID
		if( !mesh.polyMtlIndices.empty())
			(*face_mtlIDs)[fi] = mesh.polyMtlIndices[pi];
		

	}
}


void fillFaceNormalsFromFaces(const MeshData& mesh, AttrListInt& faceNormals) {

	for (size_t fi = 0, vi = 0; fi < mesh.loopTris.size(); ++fi, vi += 3) {

		const auto pi = mesh.loopTriPolys[fi];

		(*faceNormals)[vi] = pi;
		(*faceNormals)[vi + 1] = pi;
		(*faceNormals)[vi + 2] = pi;
	}
}


void fillFaceNormalsFromVertices(const MeshData& mesh, AttrListInt& faceNormals) {
	for (size_t fi = 0, vi = 0; fi < mesh.loopTris.size(); ++fi, vi += 3) {

		// Face
		const auto& ltri = mesh.loopTris[fi];

		// Face vertex indices
		const auto fvi0 = mesh.loops[ltri[0]];
		const auto fvi1 = mesh.loops[ltri[1]];
		const auto fvi2 = mesh.loops[ltri[2]];

		(*faceNormals)[vi] = fvi0;
		(*faceNormals)[vi + 1] = fvi1;
		(*faceNormals)[vi + 2] = fvi2;
	}
}


void fillFaceNormalsFromCorners(const MeshData& mesh, AttrListInt& faceNormals) {
	
	for (size_t fi = 0, vi = 0; fi < mesh.loopTris.size(); ++fi, vi += 3) {

		// Face
		const auto& ltri = mesh.loopTris[fi];

		// Corner normals are ordered the same way as the face vertices
		(*faceNormals)[vi]     = ltri[0];
		(*faceNormals)[vi + 1] = ltri[1];
		(*faceNormals)[vi + 2] = ltri[2];
	}
}


void fillGeometry(const MeshData& mesh, PluginDesc& pluginDesc) {

	const auto numFaces = static_cast<int>(mesh.loopTris.size());
	AttrListVector  vertices(static_cast<int>(mesh.vertices.size()));
	AttrListVector  normals(static_cast<int>(mesh.normals.size())); // Normals pool
	AttrListInt     faces(numFaces*3);					// Face vertex indices
	AttrListInt     faceNormals(numFaces * 3);			// Normals per face vertex - indices
	AttrListInt     face_mtlIDs(numFaces);				// Material index per face

	{
		size_t i = 0;
		for (const auto& v : mesh.vertices) {

			(*vertices)[i].x = v[0];
			(*vertices)[i].y = v[1];
			(*vertices)[i].z = v[2];
			++i;
		}
	}

	{
		size_t i = 0;
		for (const auto& n : mesh.normals) {

			(*normals)[i].x = n[0];
			(*normals)[i].y = n[1];
			(*normals)[i].z = n[2];
			++i;
		}
	}

	fillFaces(mesh, faces, face_mtlIDs);

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
	pluginDesc.add("face_mtlIDs", face_mtlIDs);
}


void fillChannelsData(const MeshData& mesh, PluginDesc &pluginDesc)
{
	const auto numFaces = static_cast<int>(mesh.loopTris.size());

	AttrListString  map_channels_names;
	AttrMapChannels map_channels;
	MapChannelMerge mergeMapChannel(mesh, numFaces);
	MapChannelRaw rawMapChannel(mesh, numFaces);
	
	MapChannelBase *channels_data = nullptr;
	if (mesh.options.mergeChannelVerts) {
		channels_data = &mergeMapChannel;
	} else {
		channels_data = &rawMapChannel;
	}

	// Init channels with the actual per-vertex values of uvs and colors 
	channels_data->init();
	channels_data->initAttributes(map_channels_names, map_channels);

	
	if (channels_data->numChannels() && channels_data->needProcessFaces()) {
		

		// Now that we have all per-vertex values stored, set indices into the created lists 
		// to the face(tris) vertices
	
		for (const auto& uvLayer : mesh.uvLayers) {
			// Store tris' UVs as indices into the UV map for each UV layer
			int channel_vert_index = 0;
			AttrListInt &uvData = map_channels.data[uvLayer.name].faces;
			for( const auto& face : mesh.loopTris ) {

				for (size_t vi = 0; vi < 3; ++vi) {
					const float *uv = uvLayer.data[face[vi]];
					const int v = channels_data->getMapFaceVertexIndex(uvLayer.name, ChanVertex::fromArray<2>(uv));
					(*uvData)[channel_vert_index++] = v;
				}
			}
			vassert(channel_vert_index == uvData.getCount() && "Vertex index of UV layer is out of range");
		}

		for (const auto& colorLayer : mesh.colorLayers) {
			// Store tris' vertex colors as indices into the vertex colors map for each color layer
			int channel_vert_index = 0;
			AttrListInt& colorData = map_channels.data[colorLayer.name].faces;
			for (const auto& face : mesh.loopTris) {

				for (size_t vi = 0; vi < 3; ++vi) {
					const MLoopCol& loop = colorLayer.data[face[vi]];
					const int v = channels_data->getMapFaceVertexIndex(colorLayer.name, ChanVertex::fromRGBbytes(loop.r, loop.g, loop.b));
					(*colorData)[channel_vert_index++] = v;
				}
			}
			vassert(channel_vert_index == colorData.getCount() && "Vertex index of color layer is out of range");
		}
	}

	if (channels_data->numChannels() ) {
		pluginDesc.add("map_channels_names", map_channels_names);
		pluginDesc.add("map_channels", map_channels);
	}
}

} // end namespace VRayForBlender::Assets



