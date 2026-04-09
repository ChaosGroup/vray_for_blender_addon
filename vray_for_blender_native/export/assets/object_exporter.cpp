// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "object_exporter.h"

#include <numeric>

#include "export/plugin_desc.hpp"
#include "export/zmq_exporter.h"

#include "api/interop/types.h"
#include "vassert.h"
#include "utils/mmh3.h"


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

	vassert((numClrs == 0) || (numClrs == numPts));
	vassert((numRadii == 0) || (numRadii == numPts));

	auto positions	= arrayToAttrList<AttrVector>(reinterpret_cast<const AttrVector*>(pc.points.data()), numPts);
	auto colors		= arrayToAttrList<AttrColor>(reinterpret_cast<const AttrColor*>(pc.colors.data()), numClrs);
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
	AttrInstancer instancer;
	instancer.frameNumber = exporter.getCurrentFrame();
	instancer.data.resize(inst.itemCount);

	using Matrix = float[4][4];

	// Unpack data from buffer.
	const char* ptr = &inst.data[0];

	for (int i = 0; i < inst.itemCount; ++i){
		AttrInstancer::Item& item = (*instancer.data)[i];

		// Read persistentId and hash it. We assume it is always 8 ints long.
		const int persistentIdSize = 8 * sizeof(int);

		uint32_t hash = 0;
		MurmurHash3_x86_32(ptr, persistentIdSize, 0, &hash);
		item.index = static_cast<int>(hash);

		ptr += persistentIdSize;

		// Read transformation
		item.tm = AttrTransform(*reinterpret_cast<const Matrix*>(ptr));
		ptr += sizeof(Matrix);

		item.vel = AttrTransform::zero();

		// Read node name
		const int nameLen = *reinterpret_cast<const int*>(ptr);
		ptr += sizeof(int);

		item.node = AttrPlugin(std::string(ptr, nameLen));
		ptr += nameLen;
	}

	PluginDesc instancerDesc(inst.name, "Instancer2");
	instancerDesc.add("instances", instancer);
	instancerDesc.add("visible", true);
	instancerDesc.add("use_time_instancing", false);
	instancerDesc.add("shading_needs_ids", true);

	return exporter.exportPlugin(instancerDesc);
}

} // end namespace VRayForBlender::Assets