// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "hair_exporter.h"

#include "api/interop/types.h"
#include "export/zmq_exporter.h"
#include "export/plugin_desc.hpp"
#include "export/assets/blender_types.h"
#include <memory>
#include <vector>

using namespace VRayBaseTypes;

namespace VRayForBlender::Assets
{

// Curves with no 'radius' attribute (e.g. grooms built from geometry-nodes profile modifiers)
// render at 0.005 in Cycles (intern/cycles/blender/curves.cpp); GeomMayaHair would take empty
// widths as 0 and render nothing.
static constexpr float CURVES_FALLBACK_RADIUS = 0.005f;

/// @brief Get strand attributes
/// @param [in] hair
/// @param [out] pointCounts - Point counts per strand
/// @param [out] pointRadii - Strand radius at each point
void getStrandAttributes(const Interop::HairData& hair, std::vector<int>& pointCounts, std::vector<float>& pointRadii) {
	const bool isCurves = !hair.strandOffsets.empty();

	if (isCurves) {
		// CURVES: per-strand counts are diffs of Blender's raw curve_offsets.
		const int strands = static_cast<int>(hair.strandOffsets.size()) - 1;
		const int* offsets = hair.strandOffsets.data();
		pointCounts.resize(strands);
		int* out = pointCounts.data();
		int prev = strands > 0 ? offsets[0] : 0;
		for (int i = 0; i < strands; ++i) {
			const int next = offsets[i + 1];
			*out++ = next - prev;
			prev = next;
		}
	} else {
		// PARTICLES: pre-computed per-strand counts
		const int strands = static_cast<int>(hair.strandSegments.size());
		pointCounts.assign(hair.strandSegments.data(), hair.strandSegments.data() + strands);
	}

	const int pts = static_cast<int>(hair.points.size());
	if (!hair.pointRadii.empty()) {
		pointRadii.assign(hair.pointRadii.data(), hair.pointRadii.data() + pts);
	} else if (isCurves) {
		pointRadii.assign(pts, CURVES_FALLBACK_RADIUS);
	}
}


/// @brief Get point coordinates for each strand
AttrListVector getStrandPoints(const HairData& hair) {
	// Range construct: resize()+memcpy would value-initialize every element first, and
	// AttrVector's user-provided default ctor makes that a loop rather than a memset.
	const AttrVector* src = reinterpret_cast<const AttrVector*>(hair.points.data());
	return AttrListVector(std::vector<AttrVector>(src, src + hair.points.size()));
}


/// @brief Get UVs of root points for each strand
AttrListVector getStrandUVs(const HairData& hair, int strands) {
	if (hair.uvs.empty()) {
		return AttrListVector();
	}

	// 2 floats in, 3 out. reserve+emplace_back skips resize()'s value-init pass.
	const int count = std::min(strands, static_cast<int>(hair.uvs.size() / 2));
	std::vector<AttrVector> uvs;
	uvs.reserve(count);

	const float* src = hair.uvs.data();
	for (int si = 0; si < count; ++si, src += 2) {
		uvs.emplace_back(src[0], src[1], 0.0f);
	}

	return AttrListVector(std::move(uvs));
}


/// @brief Get colors ( from the vertex_colors layer ) of root points for each strand
AttrListColor getStrandColors(const HairData& hair, const std::vector<int>& pointCountsPerStrand) {
	AttrListColor colors;

	if (!hair.vertColors.empty()) {
		const int strands = static_cast<int>(pointCountsPerStrand.size());

		// totalParticles/maxSteps are 0 on the curves path, so the old expression reserved
		// nothing and fill() reallocated per strand.
		int totalPoints = 0;
		for (int c : pointCountsPerStrand)
			totalPoints += c;
		colors.reserve(totalPoints);

		const float* srcPtr = hair.vertColors.data();

		for (int si = 0; si < strands; ++si) {
			// Set the same color for all vertices of the current strand
			const AttrColor clr{srcPtr[0], srcPtr[1], srcPtr[2]};
			colors.fill(clr, pointCountsPerStrand[si]);
			srcPtr += 3;
		}
	}

	return colors;
}

static inline float sq(float f) { return f * f; }

AttrValue exportParticleHair(const HairData& hair, ZmqExporter& exporter) {
	const int strandCount = hair.totalParticles - hair.firstToExport;
	const int maxStepsCap = hair.maxSteps;
	const int pointCount = strandCount * maxStepsCap;

	// Sized to the upper bound, filled through cursors, shrunk at the end.
	AttrListInt    pointCounts(strandCount);
	AttrListFloat  pointRadii(pointCount);
	AttrListVector points(pointCount);

	int* const        countsBegin = *pointCounts;
	float* const      radiiBegin  = *pointRadii;
	AttrVector* const pointsBegin = *points;
	int*        countsOut = countsBegin;
	float*      radiiOut  = radiiBegin;
	AttrVector* pointsOut = pointsBegin;

	const ParticleSystem* const psys = hair.psys;
	const int totalCurrent = psys->totcached;
	const int totalChild = psys->totchildcache;
	ParticleCacheKey* const* const pathcache = psys->pathcache;
	ParticleCacheKey* const* const childcache = psys->childcache;
	const auto& imat = psys->imat;

	const float rootRadius = hair.rootRadius;
	const float tipRadius = hair.tipRadius;
	const bool linearShape = (hair.shape == 0);

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

	// Radius at point j depends only on (j, n), so cache a row per strand length instead of
	// a pow() per point. Row n is at offset n*(n-1)/2, n entries.
	std::vector<float> radiusLut(static_cast<size_t>(maxStepsCap) * (maxStepsCap + 1) / 2);
	std::vector<char> lutReady(static_cast<size_t>(maxStepsCap) + 1, 0);

	auto radiusRow = [&](int n) -> const float* {
		float* const row = radiusLut.data() + static_cast<size_t>(n) * (n - 1) / 2;
		if (!lutReady[n]) {
			const float invSteps = n > 1 ? 1.0f / (n - 1) : 0.0f;
			if (linearShape) {
				const float shapeStep = (tipRadius - rootRadius) * invSteps;
				for (int j = 0; j < n; ++j)
					row[j] = rootRadius + shapeStep * j;
			} else {
				for (int j = 0; j < n; ++j) {
					const float t = 1.0f - j * invSteps;
					row[j] = std::pow(t, shapeExp) * (rootRadius - tipRadius) + tipRadius;
				}
			}
			lutReady[n] = 1;
		}
		return row;
	};

	for (int part = hair.firstToExport; part < hair.totalParticles; part++) {
		const ParticleCacheKey* cache = nullptr;
		if (part < totalCurrent && pathcache) {
			cache = pathcache[part];
		} else if (part < totalCurrent + totalChild && childcache) {
			cache = childcache[part - totalCurrent];
		} else {
			continue;
		}

		// Both arrays are allocated for the full particle count, but Blender leaves an entry
		// null for any strand it did not actually generate.
		if (!cache)
			continue;

		const int maxSegments = std::max(0, cache->segments);

		int step = 0;
		AttrVector last{}, prev{};
		const int maxSteps = std::min(maxSegments + 1, maxStepsCap);
		for (step = 0; step < maxSteps; step++) {
			const float* const pt = (cache + step)->co;
			// Sometimes blender just starts returning points at (0, 0, 0)...
			const float x = pt[0], y = pt[1], z = pt[2];
			const float lenSq = sq(x) + sq(y) + sq(z);
			if (lenSq < 1e-5f)
				break;

			prev = last;
			last.x = x * imat[0][0] + y * imat[1][0] + z * imat[2][0] + imat[3][0];
			last.y = x * imat[0][1] + y * imat[1][1] + z * imat[2][1] + imat[3][1];
			last.z = x * imat[0][2] + y * imat[1][2] + z * imat[2][2] + imat[3][2];
			*pointsOut++ = last;
		}
		int pointsForStrand = step;
		if (pointsForStrand > 2) {
			// In some cases it's possible for the last 2 points of a strand to be exactly the same.
			// Such strands will cause V-Ray GPU to not render any hairs for the current object.
			const float distSq = sq(last.x - prev.x) + sq(last.y - prev.y) + sq(last.z - prev.z);
			if (distSq < 1e-5f) {
				--pointsOut;
				pointsForStrand--;
			}
		}
		*countsOut++ = pointsForStrand;

		if (pointsForStrand > 0) {
			std::memcpy(radiiOut, radiusRow(pointsForStrand), pointsForStrand * sizeof(float));
			radiiOut += pointsForStrand;
		}
	}

	points.resize(static_cast<int>(pointsOut - pointsBegin));
	pointRadii.resize(static_cast<int>(radiiOut - radiiBegin));
	pointCounts.resize(static_cast<int>(countsOut - countsBegin));

	const AttrListVector strandUVs = getStrandUVs(hair, strandCount);
	const AttrListColor colors = getStrandColors(hair, *pointCounts.getData());
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
	std::vector<float> pointRadii;

	getStrandAttributes(hair, /*r*/pointCountsPerStrand, /*r*/pointRadii);

	const int strandCount = int(pointCountsPerStrand.size());
	AttrListVector  vertices = getStrandPoints(hair);
	AttrListVector  strandUVs = getStrandUVs(hair, strandCount);
	AttrListColor   strandColors = getStrandColors(hair, pointCountsPerStrand);
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
