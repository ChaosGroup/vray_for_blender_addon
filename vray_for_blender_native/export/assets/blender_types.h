#pragma once

// Copied verbatim from blender/source/blender/imbuf/IMB_imbuf_types.h
enum ImBufOwnership {
	/* The ImBuf simply shares pointer with data owned by someone else, and will not perform any
	 * memory management when the ImBuf frees the buffer. */
	IB_DO_NOT_TAKE_OWNERSHIP = 0,

	/* The ImBuf takes ownership of the buffer data, and will use MEM_freeN() to free this memory
	 * when the ImBuf needs to free the data. */
	IB_TAKE_OWNERSHIP = 1,
};



// Copied verbatim from blender/source/blender/imbuf/IMB_imbuf_types.h
struct ImBufByteBuffer {
	uint8_t* data;
	ImBufOwnership ownership;

	struct ColorSpace* colorspace;
};


// This struct is referenced by a pointer, so no implementation is needed
struct ColorSpace {
};


// Copied verbatim from blender/source/blender/imbuf/IMB_imbuf_types.h
struct ImBufFloatBuffer {
	float* data;
	ImBufOwnership ownership;

	struct ColorSpace* colorspace;
};


// This structure is copied from blender/source/blender/imbuf/IMB_imbuf_types.h
// but is TRUNCATED to include only the fields that we are accessing
struct ImBuf {
	/* dimensions */
	/** Width and Height of our image buffer.
	 * Should be 'unsigned int' since most formats use this.
	 * but this is problematic with texture math in `imagetexture.c`
	 * avoid problems and use int. - campbell */
	int x, y;

	/** Active amount of bits/bit-planes. */
	unsigned char planes;
	/** Number of channels in `rect_float` (0 = 4 channel default) */
	int channels;

	/* flags */
	/** Controls which components should exist. */
	int flags;

	/* pixels */

	/**
	 * Image pixel buffer (8bit representation):
	 * - color space defaults to `sRGB`.
	 * - alpha defaults to 'straight'.
	 */
	ImBufByteBuffer byte_buffer;

	/**
	 * Image pixel buffer (float representation):
	 * - color space defaults to 'linear' (`rec709`).
	 * - alpha defaults to 'premul'.
	 * \note May need gamma correction to `sRGB` when generating 8bit representations.
	 * \note Formats that support higher more than 8 but channels load as floats.
	 */
	ImBufFloatBuffer float_buffer;

	// TRUNCATED HERE 
};


// Copied verbatim from blender/source/blender/render/RE_pipeline.h
struct RenderPass {
	struct RenderPass* next, * prev;
	int channels;
	char name[64];   /* amount defined in IMB_openexr.h */
	char chan_id[8]; /* amount defined in IMB_openexr.h */

	/* Image buffer which contains data of this pass.
	 *
	 * The data can be either CPU side stored in ibuf->float_buffer, or a GPU-side stored in
	 * ibuf->gpu (during rendering, i.e.).
	 *
	 * The pass data storage is lazily allocated, and until data is actually provided (via either CPU
	 * buffer of GPU texture) the ibuf is not allocated. */
	struct ImBuf* ibuf;

	int rectx, recty;

	char fullname[64]; /* EXR_PASS_MAXNAME */
	char view[64];     /* EXR_VIEW_MAXNAME */
	int view_id;       /* quick lookup */

	char _pad0[4];
};


///////////////////////////////////////////////////////////////////////////////
/////////////////////       DNA Definitions        ////////////////////////////
///////////////////////////////////////////////////////////////////////////////


/**
 * \note While alpha is not currently in the 3D Viewport,
 * this may eventually be added back, keep this value set to 255.
 */
typedef struct MLoopCol {
	unsigned char r, g, b, a;
} MLoopCol;





