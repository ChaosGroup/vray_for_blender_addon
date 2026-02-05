#include "hair_exporter.h"

#include <Imath/ImathMatrix.h>

#include "api/interop/types.h"
#include "export/zmq_exporter.h"
#include "export/plugin_desc.hpp"
#include "export/assets/blender_types.h"
#include <vector>


using namespace VRayBaseTypes;

namespace VRayForBlender::Assets
{

using BlTransform = Imath_3_1::Matrix44<float>;

void mul_m4_v3(const float M[4][4], float* r)
{
	const float x = r[0];
	const float y = r[1];
	const float z = r[2];

	r[0] = x * M[0][0] + y * M[1][0] + z * M[2][0] + M[3][0];
	r[1] = x * M[0][1] + y * M[1][1] + z * M[2][1] + M[3][1];
	r[2] = x * M[0][2] + y * M[1][2] + z * M[2][2] + M[3][2];
}

/// @brief Get strand attributes
/// @param [in] hair
/// @param [out] vertCounts - Point counts per strand
/// @param [out] pointRadii - Strand radius at each point
void getStrandAttributes(const Interop::HairData& hair, std::vector<int>& pointCounts, std::vector<float>& pointRadii) {
	const int pts = static_cast<int>(hair.points.size());
	int strands = 0;

	strands = static_cast<int>(hair.strandSegments.size());
	pointCounts.assign(hair.strandSegments.data(), hair.strandSegments.data() + strands);

	if (!hair.pointRadii.empty()) {
		pointRadii.assign(hair.pointRadii.data(), hair.pointRadii.data() + pts);
	}
}


/// @brief Get point coordinates for each strand
AttrListVector getStrandPoints(const HairData& hair, const std::vector<int>& pointCountsPerStrand) {
	AttrListVector points;
	const int pts = static_cast<int>(hair.points.size());
	const int strands = static_cast<int>(pointCountsPerStrand.size());

	points.resize(pts);

	const float* ptsPtr = reinterpret_cast<const float*>(hair.points.data());
	AttrVector* resPtr = *points;

	for (int si = 0; si < strands; ++si) {
		std::memcpy(resPtr, ptsPtr, pointCountsPerStrand[si] * sizeof(AttrVector));
		ptsPtr += 3 * pointCountsPerStrand[si];
		resPtr += pointCountsPerStrand[si];
	}

	return points;
}


/// @brief Get UVs of root points for each strand
AttrListVector getStrandUVs(const HairData& hair, int strands) {
	const int uvs = static_cast<int>(hair.uvs.size() / 2);

	AttrListVector strandUVs;

	if (!hair.uvs.empty()) {
		strandUVs.resize(uvs);
		const float* uvsPtr = hair.uvs.data();

		int uvi = 0;
		for (int si = 0; si < strands; ++si) {
			(*strandUVs)[si].x = uvsPtr[uvi];
			(*strandUVs)[si].y = uvsPtr[uvi+1];
			(*strandUVs)[si].z = 0.0f;
			uvi += 2;
		}
	}

	return strandUVs;
}


/// @brief Get colors ( from the vertex_colors layer ) of root points for each strand
AttrListVector getStrandColors(const HairData& hair, const std::vector<int>& pointCountsPerStrand) {
	AttrListVector colors;

	if (!hair.vertColors.empty()) {
		colors.reserve(static_cast<int>((hair.totalParticles - hair.firstToExport) * hair.maxSteps));
		const float* srcPtr = hair.vertColors.data();

		int vi = 0;
		const int strands = static_cast<int>(pointCountsPerStrand.size());

		for (int si = 0; si < strands; ++si) {
			// Set the same color for all vertices of the current strand
			const float clr[3] = { *srcPtr, *(srcPtr + 1), *(srcPtr + 2) };

			for (size_t step = 0; step < pointCountsPerStrand[si]; ++step){
				colors.append(clr);
			}
			vi += pointCountsPerStrand[si];
			srcPtr += 3;
		}
	}

	return colors;
}

static inline float sq(float f) { return f * f; }

AttrValue exportParticleHair(const HairData& hair, ZmqExporter& exporter) {
	const int strandCount = hair.totalParticles - hair.firstToExport;
	AttrListInt pointCounts;
	pointCounts.reserve(strandCount);

	AttrListFloat pointRadii;
	const int pointCount = strandCount * hair.maxSteps;
	pointRadii.reserve(pointCount);

	AttrListVector points;
	points.reserve(pointCount);

	const int totalCurrent = hair.psys->totcached;
	const int totalChild = hair.psys->totchildcache;
	ParticleCacheKey *cache = nullptr;

	// Implementation of the function of Cycles, responsible for interpolating the hair strand radius
	// from root to tip, based on the shape parameter.
	// Link to the implementation in Blender's source code:
	// https://projects.blender.org/blender/blender/src/commit/5ba668135aaf7cb79081482cb7839a64bbb47457/intern/cycles/blender/curves.cpp#L32
	float shapeExp = 0.0f;
	if (hair.shape < 0.0f)
		shapeExp = 1.0f + hair.shape;
	else if (hair.shape > 0.0f && hair.shape != 1.0f)
		shapeExp = 1.0f / (1.0f - hair.shape);
	else if (hair.shape == 1.0f)
		shapeExp = 10000.0f;

	for (int part = hair.firstToExport; part < hair.totalParticles; part++) {
		int maxSegments = 0;
		if (part < totalCurrent && hair.psys->pathcache) {
			cache = hair.psys->pathcache[part];
			maxSegments = cache->segments;
		} else if (part < totalCurrent + totalChild && hair.psys->childcache) {
			cache = hair.psys->childcache[part - totalCurrent];

			if (cache->segments < 0)
				maxSegments = 0;
			else
				maxSegments = cache->segments;
		} else {
			continue;
		}

		int step = 0;
		const int maxSteps = std::min(maxSegments + 1, hair.maxSteps);
		for (step = 0; step < maxSteps; step++) {
			const auto& pt = (cache + step)->co;
			// Sometimes blender just starts returning points at (0, 0, 0)...
			const float lenSq = sq(pt[0]) + sq(pt[1]) + sq(pt[2]);
			if (lenSq < 1e-5)
				break;

			float point[3];
			std::memcpy(point, pt, sizeof(float[3]));
			mul_m4_v3(hair.psys->imat, point);
			points.append(point);
		}
		int pointsForStrand = step;
		if (pointsForStrand > 2) {
			const AttrVector& last = (*points)[points.getCount() - 1];
			const AttrVector& prev = (*points)[points.getCount() - 2];
			// In some cases it's possible for the last 2 points of a strand to be exactly the same.
			// Such strands will cause V-Ray GPU to not render any hairs for the current object.
			const float distSq = sq(last.x - prev.x) + sq(last.y - prev.y) + sq(last.z - prev.z);
			if (distSq < 1e-5) {
				points.getData()->pop_back();
				pointsForStrand--;
			}
		}
		pointCounts.append(pointsForStrand);

		if (hair.shape == 0) {
			const float shapeStep = (hair.tipRadius - hair.rootRadius) / (pointsForStrand - 1);
			for (int j = 0; j < pointsForStrand; ++j) {
				pointRadii.append(hair.rootRadius + shapeStep * j);
			}
		} else {
			for (int j = 0; j < pointsForStrand; ++j) {
				const float t = 1.0f - (float)j / (pointsForStrand - 1);
				const float val = std::pow(t, shapeExp) * (hair.rootRadius - hair.tipRadius) + hair.tipRadius;
				pointRadii.append(val);
			}
		}
	}

	const AttrListVector strandUVs = getStrandUVs(hair, strandCount);
	const AttrListVector colors = getStrandColors(hair, *pointCounts.getData());
	PluginDesc hairDesc(hair.name, "GeomMayaHair");
	hairDesc.add("num_hair_vertices", pointCounts);
	hairDesc.add("hair_vertices", points);
	hairDesc.add("widths", pointRadii);
	if (strandUVs.getCount() > 0) {
		hairDesc.add("strand_uvw", strandUVs);
	}
	if (colors.getCount() > 0) {
		hairDesc.add("colors", colors);
	}

	hairDesc.add("widths_in_pixels", hair.widthsInPixels);
	hairDesc.add("geom_splines", hair.useHairBSpline);

	return exporter.exportPlugin(hairDesc);
}


/// Export hair geometry.
/// The incoming data is in the same format for both Particle and Curves hair
AttrValue exportGeomHair(const HairData& hair, ZmqExporter& exporter) {
	if (hair.type == "PARTICLES") {
		return exportParticleHair(hair, exporter);
	}

	std::vector<int> pointCountsPerStrand;
	const int strandCount = int(pointCountsPerStrand.size());
	std::vector<float> pointRadii;

	getStrandAttributes(hair, /*r*/pointCountsPerStrand, /*r*/pointRadii);

	AttrListVector  vertices = getStrandPoints(hair, pointCountsPerStrand);
	AttrListVector  strandUVs = getStrandUVs(hair, strandCount);
	AttrListVector  strandColors = getStrandColors(hair, pointCountsPerStrand);
	AttrListFloat	radii(std::move(pointRadii));
	AttrListInt		pointCounts(std::move(pointCountsPerStrand));


	PluginDesc hairDesc(hair.name, "GeomMayaHair");
	hairDesc.add("num_hair_vertices", pointCounts);
	hairDesc.add("hair_vertices", vertices);
	hairDesc.add("widths", radii);
	if (strandUVs.getCount() > 0) {
		hairDesc.add("strand_uvw", strandUVs);
	}
	if (strandColors.getCount() > 0) {
		hairDesc.add("colors", strandColors);
	}

	hairDesc.add("widths_in_pixels", hair.widthsInPixels);
	hairDesc.add("geom_splines", hair.useHairBSpline);

	return exporter.exportPlugin(hairDesc);
}

} // end namespace VRayForBlender::Assets
