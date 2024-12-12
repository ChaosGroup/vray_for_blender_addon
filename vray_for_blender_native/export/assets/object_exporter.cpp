#include "object_exporter.h"

#include <numeric>

#include "export/plugin_desc.hpp"
#include "export/zmq_exporter.h"

#include "api/interop/types.h"
#include "utils/assert.h"


using namespace VRayBaseTypes;


namespace
{
	
// TODO: Move the conversion helpers to some utils file
AttrListVector arrayToAttrListVec3(const float* src, int vecCount)
{
	AttrListVector list;
	list.resize(vecCount);

	const auto* srcPtr = src;

	for (size_t i = 0; i < vecCount; ++i) {
		(*list)[i].x = *srcPtr;
		(*list)[i].y = *(srcPtr + 1);
		(*list)[i].z = *(srcPtr + 2);
		srcPtr += 3;
	}

	return list;
}


AttrListVector arrayToAttrListVec2(const float* src, int vecCount)
{
	AttrListVector list;
	list.resize(vecCount);

	const auto* srcPtr = src;

	for (int i = 0; i < vecCount; ++i) {
		(*list)[i].x = *srcPtr;
		(*list)[i].y = *(srcPtr + 1);
		srcPtr += 2;
	}

	return list;
}


template <class T>
AttrList<T> arrayToAttrList(const T* src, int count)
{
	std::vector<T> vec;
	vec.resize(count);
	::memcpy(vec.data(), src, count * sizeof(T));

	return AttrList<T>(std::move(vec));
}

} // end anonymous namespace




namespace VRayForBlender::Assets
{

using PointCloudData     = Interop::PointCloudData;
using InstancerCloudData = Interop::InstancerData;

AttrValue exportPointCloud(const PointCloudData& pc, ZmqExporter& exporter)
{	
	const int numPts	= static_cast<int>(pc.points.size());
	const int numClrs	= static_cast<int>(pc.colors.size());
	const int numRadii	= static_cast<int>(pc.radii.size());

	VRAY_ASSERT((numClrs == 0) || (numClrs == numPts));
	VRAY_ASSERT((numRadii == 0) || (numRadii == numPts));

	auto positions	= arrayToAttrListVec3(reinterpret_cast<const float*>(pc.points.data()), numPts);
	auto colors		= arrayToAttrListVec3(reinterpret_cast<const float*>(pc.colors.data()), numClrs);
	auto radii		= arrayToAttrList<float>(reinterpret_cast<const float*>(pc.radii.data()), numRadii);
	
	// TODO: UVs are not directly used by GeomPatricleSystem, figure out what to do with them 
	
	// Generate particle ids - consecutive numbers
	std::vector<int> ids(numPts);
	std::iota(ids.begin(), ids.end(), 1);

	PluginDesc pcDesc(pc.name, "GeomParticleSystem");
	pcDesc.add("render_type", pc.renderType);
	pcDesc.add("point_radii", AttrBool{!radii.empty()});
	pcDesc.add("point_world_size", AttrBool{true});
	pcDesc.add("positions", positions);
	pcDesc.add("radii", radii);
	pcDesc.add("ids", AttrList<int>(std::move(ids)));
	pcDesc.add("colors", colors);
	
	return exporter.exportPlugin(pcDesc);
}


AttrValue exportInstancer(const InstancerData& inst, ZmqExporter& exporter)
{
	static const float FLOAT_DELTA = 0.0001f;

	AttrInstancer instancer;
	instancer.frameNumber = exporter.getCurrentFrame() + FLOAT_DELTA;
	// instancer.data.resize(inst.itemCount);

	using Matrix = float[4][4];

	// Unpack data from buffer. 
	// TODO: consider replacing with a well-known protocol, e.g. ProtocolBuffers
	const size_t matByteSize	= sizeof(float) * 16;
	const size_t tmOffset		= sizeof(int);
	const size_t velOffset		= tmOffset + matByteSize;
	const size_t objNameOffset	= velOffset + matByteSize;
	const size_t objNameDataOffset	= objNameOffset + sizeof(int);
	
	const char* ptr = &inst.data[0];

	for (int i = 0; i < inst.itemCount; ++i){
		AttrInstancer::Item item;

		item.index = *reinterpret_cast<const int*>(ptr); 
		item.tm = AttrTransform(*reinterpret_cast<const Matrix*>(ptr + tmOffset));
		item.vel = AttrTransform(*reinterpret_cast<const Matrix*>(ptr + velOffset));

		const int nameLen = *reinterpret_cast<const int*>(ptr + objNameOffset);
		std::string name;
		name.resize(nameLen);
		::memcpy(name.data(), ptr + objNameDataOffset, nameLen);

		item.node = AttrPlugin(name);
		
		ptr += objNameDataOffset + nameLen;

		instancer.data.append(item);
	}



	PluginDesc instancerDesc(inst.name, "Instancer2");
	instancerDesc.add("instances", instancer);
	instancerDesc.add("visible", true);
	instancerDesc.add("use_time_instancing", false);
	instancerDesc.add("shading_needs_ids", true);

	return exporter.exportPlugin(instancerDesc);
}

} // end namespace VRayForBlender::Assets