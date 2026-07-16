// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <utility>
#include <vector>
#include <cassert>
#include <algorithm>
#include <memory>

namespace VRayForBlender {

struct ImageSize {
	int w, h;
	int channels;
};


struct ImageRegion {
	ImageRegion(ImageSize sz)
		: x(0), y(0), w(sz.w), h(sz.h) {}

	ImageRegion(int x, int y, int w, int h)
		: x(x), y(y), w(w), h(h) {}

	int x, y;
	int w, h;

	enum Options {
		NONE        = 0,
		RESET_ALPHA = 1 << 0, ///< reset alpha to 1.0
		CLAMP       = 1 << 1, ///< clamp values to [0, 1]

		FROM_RENDERER = RESET_ALPHA | CLAMP, ///< convinient default for images from vray
	};
};


/// Copy a region from one image to a region in another image
/// @dest and @source must not overlap, @sourceRegion and @destRegion must have same size
/// @param dest - memory for the destination
/// @param destSize - size of the destination memory
/// @param destRegion - the region in the destination image
/// @param source - pointer to source memory
/// @param sourceSize - size of the source image
/// @param sourceRegion - the region in the source image
/// @param options - specifies additional actions to be performed on the destination
void updateImageRegion(
	float* dest, ImageSize destSize, ImageRegion destRegion,
	const float* source, ImageSize sourceSize, ImageRegion sourceRegion,
	ImageRegion::Options options = ImageRegion::Options::NONE
);


struct RenderImage {
	RenderImage()
		: pixels(nullptr)
		, w(0)
		, h(0)
		, channels(0)
		, updated(0)
	{}

	RenderImage(const RenderImage&) = default;
	RenderImage& operator=(const RenderImage&) = default;

	RenderImage(RenderImage&& other) noexcept = default;
	RenderImage& operator=(RenderImage&& other) noexcept = default;

	virtual ~RenderImage() = default;

	operator bool () const { return !!m_pixels; }

	void    reset    ();

	void   updateRegion(const float* source, ImageRegion destRegion);

	void   resetUpdated() { updated = 0.f; }

	/// Set the pixel buffer. Updates both the owning holder and the const read pointer.
	void setPixels(std::shared_ptr<float[]> holder) {
		m_pixels = std::move(holder);
		pixels = m_pixels.get();
	}

	/// Non-owning: wrap an external buffer (e.g. Blender's RenderPass).
	/// The caller is responsible for the buffer's lifetime.
	void setPixelsNonOwning(float* buffer) {
		m_pixels = std::shared_ptr<float[]>(buffer, [](float*) {});
		pixels = buffer;
	}

	/// Mutable access to the pixel buffer for writing (memcpy, memset, updateRegion).
	float* writablePixels() { return m_pixels.get(); }

public:

	const float* pixels; ///< Read-only pointer to pixel data. Use writablePixels() for mutation.

	int    w; ///< width in pixels
	int    h; ///< height in pixels
	int    channels; ///< channels count (usually 1, 3 or 4)
	float  updated; ///< will hold % of updated area

private:
	std::shared_ptr<float[]> m_pixels; ///< Owns (or ref-counts) the pixel buffer.
};

float* jpegToPixelData(unsigned char* data, int size, int& channels);

} // namespace VRayForBlender

