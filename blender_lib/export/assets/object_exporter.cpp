// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "object_exporter.h"

#include <numeric>

#include <nanobind/nanobind.h>
#include <nanobind/ndarray.h>
#include <nanobind/stl/string.h>

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
	const int N = inst.itemCount;

	AttrListTransform transforms(N);
	AttrListInt       instanceIds(N);
	AttrListPlugin    meshes;
	AttrListInt       indices(N);

	{
		nb::gil_scoped_acquire gil;

		static_assert(sizeof(AttrTransform) == 12 * sizeof(float), "AttrTransform layout must be 12 contiguous floats");
		auto tmsArr = nb::cast<nb::ndarray<float,   nb::c_contig>>(inst.tms);
		auto idsArr = nb::cast<nb::ndarray<int32_t, nb::c_contig>>(inst.ids);
		auto idxArr = nb::cast<nb::ndarray<int32_t, nb::c_contig>>(inst.indices);

		nb::list pyMeshes = nb::cast<nb::list>(inst.meshes);
		meshes.reserve(static_cast<int>(nb::len(pyMeshes)));
		for (auto m : pyMeshes)
			meshes.append(AttrPlugin(nb::cast<std::string>(m)));

		const float*   tmsSrc = tmsArr.data();
		const int32_t* idSrc  = idsArr.data();
		const int32_t* idxSrc = idxArr.data();
		nb::gil_scoped_release noGIL;

		memcpy(*transforms, tmsSrc, N * sizeof(AttrTransform));

		int* idDst = *instanceIds;
		constexpr int persistentIdBytes = 8 * sizeof(int32_t);
		for (int i = 0; i < N; ++i) {
			uint32_t hash = 0;
			MurmurHash3_x86_32(idSrc + i * 8, persistentIdBytes, 0, &hash);
			idDst[i] = static_cast<int>(hash);
		}

		memcpy(*indices, idxSrc, N * sizeof(int32_t));
	}

	AttrListValue sources;
	sources.append(meshes);
	sources.append(indices);

	PluginDesc instancerDesc(inst.name, "GeomInstancer");
	instancerDesc.add("transforms", transforms);
	instancerDesc.add("instance_ids", instanceIds);
	instancerDesc.add("sources", sources);

	// Export VRAY_EXPLICIT_INSTANCE_ID for randomized shading.
	if (N > 0) {
		AttrListValue explicitIdAttr;
		explicitIdAttr.append(AttrValue("VRAY_EXPLICIT_INSTANCE_ID"));
		explicitIdAttr.append(instanceIds);

		AttrListValue userAttributes;
		userAttributes.append(explicitIdAttr);
		instancerDesc.add("user_attributes", userAttributes);
	}

	return exporter.exportPlugin(instancerDesc);
}

} // end namespace VRayForBlender::Assets