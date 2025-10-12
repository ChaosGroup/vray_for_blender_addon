#include "hair_exporter.h"

#include <array>
#include <Imath/ImathMatrix.h>

#include "api/interop/types.h"
#include "export/zmq_exporter.h"
#include "export/plugin_desc.hpp"
#include "vassert.h"


using namespace VRayBaseTypes;

namespace VRayForBlender::Assets
{

using BlTransform = Imath_3_1::Matrix44<float>;

// IMath's Vector class does not have the appropriate method
// to do the mutiplication in-place.
// This was copied over from Blender code. 
// TODO: Look for a better alternative
void mul_m4_v3(const float M[4][4], float r[3])
{
	const float x = r[0];
	const float y = r[1];

	r[0] = x * M[0][0] + y * M[1][0] + M[2][0] * r[2] + M[3][0];
	r[1] = x * M[0][1] + y * M[1][1] + M[2][1] * r[2] + M[3][1];
	r[2] = x * M[0][2] + y * M[1][2] + M[2][2] * r[2] + M[3][2];
}



/// @brief Get strand attributes
/// @param [in] hair 
/// @param [out] vertCounts - Point counts per strand 
/// @param [out] pointRadii  - Strand radius at each point
void  getStrandAttributes(const Interop::HairData& hair, std::vector<int>& pointCounts, std::vector<float>& pointRadii)
{
	const int pts = static_cast<int>(hair.points.size());
	int strands = 0;

	if (hair.segments != 0){
		// All strands have the same number of points
		const auto vertsPerStrand = hair.segments + 1;
		strands = pts / vertsPerStrand;
		pointCounts.assign(strands, vertsPerStrand);
	} else {
		strands = static_cast<int>(hair.strandSegments.size());
		pointCounts.assign(hair.strandSegments.data(), hair.strandSegments.data() + strands);
	}
	
	if (!hair.pointRadii.empty()) {
		// We have 'custom' strand radii per point
		pointRadii.assign(hair.pointRadii.data(), hair.pointRadii.data() + pts);
	} else {
		const auto vertsPerStrand = static_cast<float>(hair.segments + 1);
		const float widthFadeStep = hair.fadeWidth ? hair.width / vertsPerStrand : 0.f;
		
		for (int si = 0; si < strands; ++si) {
			float hairWidth = hair.width;
			for (size_t step = 0; step < pointCounts[si]; ++step){
				pointRadii.push_back(std::max(1e-6f, hairWidth));
				hairWidth -= widthFadeStep;
			}
		}
	}
}


/// @brief Get point coordinates for each strand
AttrListVector getStrandPoints(const HairData& hair, const std::vector<int>& pointCountsPerStrand)
{
	const int pts = static_cast<int>(hair.points.size());
	const int strands = static_cast<int>(pointCountsPerStrand.size());

	AttrListVector points;
	points.resize(pts);

	BlTransform hair_itm;
	::memcpy(hair_itm.getValue(), hair.matWorld.data(), sizeof(float) * 16);
	hair_itm.invert();

	const float* ptsPtr = reinterpret_cast<const float*>(hair.points.data());

	int vi = 0;
	
	for (int si = 0; si < strands; ++si) {
		for (size_t step = 0; step < pointCountsPerStrand[si]; ++step) {
			(*points)[vi].x = *(ptsPtr);
			(*points)[vi].y = *(ptsPtr + 1);
			(*points)[vi].z = *(ptsPtr + 2);
			ptsPtr += 3;

			// Note: the transform will always be identity for Curves hair, so 
			// it can be skipped
			mul_m4_v3(reinterpret_cast<const float(*)[4]>(hair_itm.getValue()), reinterpret_cast<float*>(&(*points)[vi++]));
		}
	}

	return points;
}


/// @brief Get UVs of root points for each strand
AttrListVector getStrandUVs(const HairData& hair, const std::vector<int>& pointCountsPerStrand)
{
	const int strands = static_cast<int>(pointCountsPerStrand.size());
	const int uvs = static_cast<int>(hair.uvs.size());

	AttrListVector strandUVs;
	
	if (!hair.uvs.empty()) {
		strandUVs.resize(uvs);
		const float* uvsPtr = reinterpret_cast<const float*>(hair.uvs.data());

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
AttrListVector getStrandColors(const HairData& hair, const std::vector<int>& pointCountsPerStrand)
{
	AttrListVector colors;
	
	if (!hair.vertColors.empty()) {
		colors.resize(static_cast<int>(hair.points.size()));
		const float* srcPtr = reinterpret_cast<const float*>(hair.vertColors.data());

		int vi = 0;
		const int strands = static_cast<int>(pointCountsPerStrand.size());

		for (int si = 0; si < strands; ++si) {
			// Set the same color for all vertices of the current strand
			const float clr[3] = { *srcPtr, *(srcPtr + 1), *(srcPtr + 2) };

			for (size_t step = 0; step < pointCountsPerStrand[si]; ++step){
				(*colors)[vi + step] = clr;
			}
			vi += pointCountsPerStrand[si];
			srcPtr += 3;
			
		}
	}

	return colors;
}


/// Export hair geometry. 
/// The incoming data is in the same format for both Particle and Curves hair
AttrValue exportGeomHair(const HairData& hair, ZmqExporter& exporter)
{
	std::vector<int> pointCountsPerStrand;
	std::vector<float> pointRadii;

	getStrandAttributes(hair, /*r*/pointCountsPerStrand, /*r*/pointRadii);

	AttrListVector  vertices = getStrandPoints(hair, pointCountsPerStrand);
	AttrListVector  strandUVs = getStrandUVs(hair, pointCountsPerStrand);
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
