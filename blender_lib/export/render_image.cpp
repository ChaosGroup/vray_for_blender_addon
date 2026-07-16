// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "render_image.h"
#include "vassert.h"
#include "utils/logger.hpp"

#include <cstring>
#include <algorithm>

#include "jpeglib.h"
#include <setjmp.h>

using namespace VRayForBlender;

namespace {
	void resetAlpha(float * data, int w, int h, int channels) {
		if (channels == 4) {
			for (int c = 3; c < w * h * channels; c+=4) {
				data[c] = 1.0f;
			}
		}
	}

	void clamp(float * data, int w, int h, int channels, float max, float val) {
		const int pixelCount = w * h;
		for (int p = 0; p < pixelCount; ++p) {
			float *pixel = data + (p * channels);
			switch (channels) {
			case 4:
			case 3: pixel[2] = pixel[2] > max ? val : pixel[2];
			case 2: pixel[1] = pixel[1] > max ? val : pixel[1];
			case 1: pixel[0] = pixel[0] > max ? val : pixel[0];
			}
		}
	}
}

void VRayForBlender::updateImageRegion(
	float* dest, ImageSize destSize, ImageRegion destRegion,
	const float* source, ImageSize sourceSize, ImageRegion sourceRegion, ImageRegion::Options options)
{
	vassert(destRegion.w == sourceRegion.w && destRegion.h == sourceRegion.h && "Source and Destination region's sizes must be equal");
	vassert(destSize.w >= destRegion.w && destSize.h >= destRegion.h && "Image region can't be bigger than dest size!");
	vassert(destRegion.x >= 0 && destRegion.y >= 0 && sourceRegion.x >= 0 && sourceRegion.y >= 0 && "Region's coords must be >= 0");
	vassert(destRegion.x + destRegion.w <= destSize.w && destRegion.y + destRegion.h <= destSize.h && "Destination region must fit inside destination size!");
	vassert(sourceSize.channels == destSize.channels && "Source and destination must have same number of channels");

	const int pixelSize = destSize.channels;

	const int destLeftPad = destRegion.x * pixelSize;
	const int destLineSize = destSize.w * pixelSize;

	const int sourceLineSize = sourceSize.w * pixelSize;
	const int sourceLeftPad = sourceRegion.x * pixelSize;

	const int destEnd = destSize.h - destRegion.y - 1;

	const int copyLineSize = destRegion.w * pixelSize;
	for (int c = 0; c < destRegion.h; c++) {
		float * destLine = dest + destLineSize * (destEnd - c) + destLeftPad;
		const float * sourceLine = source + sourceLineSize * (c + sourceRegion.y) + sourceLeftPad;

		memcpy(destLine, sourceLine, copyLineSize * sizeof(float));
		if (options & ImageRegion::Options::CLAMP) {
			::clamp(destLine, destRegion.w, 1, pixelSize, 1.0f, 1.0f);
		}
		if (options & ImageRegion::Options::RESET_ALPHA) {
			::resetAlpha(destLine, destRegion.w, 1, pixelSize);
		}
	}
}

void RenderImage::reset() {
	setPixels(nullptr);

	w = h = 0;
	channels = 0;
}


void RenderImage::updateRegion(const float *source, ImageRegion destRegion)
{
	updated += (float)(destRegion.w * destRegion.h) / std::max((float)(this->w * this->h), 1.f);

	ImageSize updateSize = {destRegion.w, destRegion.h, channels};
	updateImageRegion(writablePixels(), ImageSize{w, h, channels}, destRegion, source, updateSize, updateSize);
}


namespace {

#ifdef _MSC_VER
	// A 'reminder' warning, not affecting operation
    // Caused by jmp_buf
    #pragma warning(push)
    #pragma warning(disable: 4324) // Structure was padded due to alignment specifier
#endif

struct JpegErrorManager {
	jpeg_error_mgr pub;
	jmp_buf setjmp_buffer;
};

#ifdef _MSC_VER
	#pragma warning (pop)
#endif

void jpegErrorExit(j_common_ptr cinfo) {
	JpegErrorManager * myerr = (JpegErrorManager*)cinfo->err;
	char jpegErrMsg[JMSG_LENGTH_MAX + 1];
	(*cinfo->err->format_message) (cinfo, jpegErrMsg);
	Logger::warning("Error in jpeg decompress [%1%]!", jpegErrMsg);
	longjmp(myerr->setjmp_buffer, 1);
}


void init_source(j_decompress_ptr) {}

boolean fill_input_buffer(j_decompress_ptr cinfo) {
	unsigned char *buf = (unsigned char *)cinfo->src->next_input_byte - 2;

	buf[0] = (JOCTET)0xFF;
	buf[1] = (JOCTET)JPEG_EOI;

	cinfo->src->next_input_byte = buf;
	cinfo->src->bytes_in_buffer = 2;

	return TRUE;
}

void skip_input_data(j_decompress_ptr cinfo, long num_bytes) {
	struct jpeg_source_mgr* src = (struct jpeg_source_mgr*) cinfo->src;

	if (num_bytes > 0) {
		src->next_input_byte += (size_t)num_bytes;
		src->bytes_in_buffer -= (size_t)num_bytes;
	}
}

void term_source(j_decompress_ptr) {}

void jpeg_mem_src_own(j_decompress_ptr cinfo, const unsigned char * buffer, int nbytes) {
	struct jpeg_source_mgr* src;

	if (cinfo->src == NULL) {   /* first time for this JPEG object? */
		cinfo->src = (struct jpeg_source_mgr *)
			(*cinfo->mem->alloc_small) ((j_common_ptr)cinfo, JPOOL_PERMANENT,
			sizeof(struct jpeg_source_mgr));
	}

	src = (struct jpeg_source_mgr*) cinfo->src;
	src->init_source = init_source;
	src->fill_input_buffer = fill_input_buffer;
	src->skip_input_data = skip_input_data;
	src->resync_to_restart = jpeg_resync_to_restart; /* use default method */
	src->term_source = term_source;
	src->bytes_in_buffer = nbytes;
	src->next_input_byte = (JOCTET*)buffer;
}
}

float * VRayForBlender::jpegToPixelData(unsigned char * data, int size, int &channels) {
	jpeg_decompress_struct jpegInfo;
	JpegErrorManager jpegError;

	jpegInfo.err = jpeg_std_error(&jpegError.pub);

	jpegError.pub.error_exit = jpegErrorExit;

// This is a 'reminder' warning, not an actual issue
#ifdef _MSC_VER
  #pragma warning(push)
  #pragma warning (disable:4611) // Interaction between '_setjmp' and C++ object destruction is non-portable
#endif

	if (setjmp(jpegError.setjmp_buffer)) {
		Logger::warning("Longjmp after jpeg error!");
		jpeg_destroy_decompress(&jpegInfo);
		return nullptr;
	}

#ifdef _MSC_VER
	#pragma warning (pop)
#endif

	jpeg_create_decompress(&jpegInfo);
	jpeg_mem_src_own(&jpegInfo, data, size);

	if (jpeg_read_header(&jpegInfo, TRUE) != JPEG_HEADER_OK) {
		jpeg_destroy_decompress(&jpegInfo);
		return nullptr;
	}

	jpegInfo.out_color_space = JCS_EXT_RGBX;

	if (!jpeg_start_decompress(&jpegInfo)) {
		jpeg_destroy_decompress(&jpegInfo);
		return nullptr;
	}

	channels = jpegInfo.output_components;
	int rowStride = jpegInfo.output_width * jpegInfo.output_components;
	float * imageData = new float[jpegInfo.output_height * rowStride];
	JSAMPARRAY buffer = (*jpegInfo.mem->alloc_sarray)((j_common_ptr)&jpegInfo, JPOOL_IMAGE, rowStride, 1);

	int c = 0;
	while (jpegInfo.output_scanline < jpegInfo.output_height) {
		jpeg_read_scanlines(&jpegInfo, buffer, 1);

		float * dest = imageData + c * rowStride;
		const unsigned char * source = buffer[0];
		const int scanlineLength = static_cast<int>(jpegInfo.image_width * jpegInfo.output_components);

		for (int r = 0; r < scanlineLength; ++r) {
			if ((r + 1) % 4 == 0) {
				dest[r] = 1.f;
			} else {
				dest[r] = source[r] / 255.f;
			}
		}

		++c;
	}

	jpeg_finish_decompress(&jpegInfo);
	jpeg_destroy_decompress(&jpegInfo);

	return imageData;
}


