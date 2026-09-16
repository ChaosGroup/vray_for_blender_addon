// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "object_exporter.h"

#include <numeric>
#include <utility>
#include <vector>

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
// resize() would zero every element first - AttrVector's default ctor is user-provided.
AttrListVector arrayToAttrListVec3(const float* src, int vecCount)
{
	std::vector<AttrVector> vec;
	vec.reserve(vecCount);

	for (int i = 0; i < vecCount; ++i, src += 3) {
		vec.emplace_back(src[0], src[1], src[2]);
	}

	return AttrListVector(std::move(vec));
}


AttrListVector arrayToAttrListVec2(const float* src, int vecCount)
{
	std::vector<AttrVector> vec;
	vec.reserve(vecCount);

	// z is written explicitly - the previous version relied on resize() zeroing it.
	for (int i = 0; i < vecCount; ++i, src += 2) {
		vec.emplace_back(src[0], src[1], 0.0f);
	}

	return AttrListVector(std::move(vec));
}


template <class T>
AttrList<T> arrayToAttrList(const T* src, int count)
{
	// Range construct: resize() would value-initialize before the copy overwrote it.
	return AttrList<T>(std::vector<T>(src, src + count));
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

	AttrListTransform transforms;
	AttrListInt       instanceIds(N);
	AttrListPlugin    meshes;
	AttrListInt       indices;

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

		// Range construct: AttrTransform's default ctor makes sizing an element-wise loop.
		const auto* tmSrc = reinterpret_cast<const AttrTransform*>(tmsSrc);
		transforms = AttrListTransform(std::vector<AttrTransform>(tmSrc, tmSrc + N));
		indices    = AttrListInt(std::vector<int>(idxSrc, idxSrc + N));

		int* idDst = *instanceIds;
		constexpr int persistentIdBytes = 8 * sizeof(int32_t);
		for (int i = 0; i < N; ++i) {
			uint32_t hash = 0;
			MurmurHash3_x86_32(idSrc + i * 8, persistentIdBytes, 0, &hash);
			idDst[i] = static_cast<int>(hash);
		}
	}

	// Per-instance user attributes (Blender instance-domain attributes).
	std::vector<std::pair<std::string, AttrValue>> userAttrValues;
	if (N > 0) {
		nb::gil_scoped_acquire gil;

		for (auto item : nb::cast<nb::list>(inst.userAttrs)) {
			auto entry = nb::cast<nb::tuple>(item);
			const auto attrName = nb::cast<std::string>(entry[0]);
			const auto kind = static_cast<Interop::InstancerUserAttrKind>(nb::cast<int>(entry[1]));

			switch (kind) {
			case Interop::InstancerUserAttrKind::Int: {
				auto arr = nb::cast<nb::ndarray<int32_t, nb::c_contig>>(entry[2]);
				vassert(static_cast<int>(arr.shape(0)) == N);
				AttrListInt values(N);
				memcpy(*values, arr.data(), N * sizeof(int32_t));
				userAttrValues.emplace_back(attrName, values);
				break;
			}
			case Interop::InstancerUserAttrKind::Float: {
				auto arr = nb::cast<nb::ndarray<float, nb::c_contig>>(entry[2]);
				vassert(static_cast<int>(arr.shape(0)) == N);
				AttrListFloat values(N);
				memcpy(*values, arr.data(), N * sizeof(float));
				userAttrValues.emplace_back(attrName, values);
				break;
			}
			case Interop::InstancerUserAttrKind::Color: {
				static_assert(sizeof(AttrVector) == 3 * sizeof(float), "AttrVector layout must be 3 contiguous floats");
				auto arr = nb::cast<nb::ndarray<float, nb::c_contig>>(entry[2]);
				vassert(static_cast<int>(arr.shape(0)) == N);
				AttrListVector values(N);
				memcpy(*values, arr.data(), N * sizeof(AttrVector));
				userAttrValues.emplace_back(attrName, values);
				break;
			}
			default:
				vassert(!"Unknown InstancerUserAttrKind");
			}
		}
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

		for (const auto& [attrName, values] : userAttrValues) {
			AttrListValue attr;
			attr.append(AttrValue(attrName));
			attr.append(values);
			userAttributes.append(attr);
		}

		instancerDesc.add("user_attributes", userAttributes);
	}

	return exporter.exportPlugin(instancerDesc);
}

} // end namespace VRayForBlender::Assets