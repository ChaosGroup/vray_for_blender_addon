# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
import gpu
import numpy as np

from vray_blender.lib.blender_utils import findViewport, WindowScope
from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender import debug

# Long edge cap for the captured image, balancing detail against upload size.
# 3ds Max sends a JPEG viewport preview; we match the format.
_MAX_DIMENSION = 2048
_OUTPUT_NAME   = "veras_viewport.jpg"


def captureViewportToFile(context: bpy.types.Context):
    """ Render the active 3D viewport offscreen as a clean shaded image (overlays hidden,
        current view) and save it as a JPEG in the V4B temp directory.

        Must be called on the main thread from an operator (it needs the GPU context).
        Returns the absolute file path on success, or None if the viewport could not be
        captured.
    """
    # The window the operator was invoked from, so the capture is the viewport the user is
    # looking at rather than one on another screen.
    if (viewport := findViewport(context, WindowScope.ACTIVE_ONLY)) is None:
        return None

    region, rv3d, space = viewport.region, viewport.rv3d, viewport.space

    scale  = min(1.0, float(_MAX_DIMENSION) / max(region.width, region.height))
    width  = max(1, int(region.width * scale))
    height = max(1, int(region.height * scale))

    # Capture a clean shaded image: hide overlays for the duration of the draw.
    showOverlaysPrev = space.overlay.show_overlays
    space.overlay.show_overlays = False

    offscreen = gpu.types.GPUOffScreen(width, height)
    try:
        offscreen.draw_view3d(
            context.scene,
            context.view_layer,
            space,
            region,
            rv3d.view_matrix,
            rv3d.window_matrix,
            do_color_management=True,
        )
        with offscreen.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            buffer = framebuffer.read_color(0, 0, width, height, 4, 0, 'UBYTE')
    except Exception as ex:
        debug.printError(f"Veras: failed to capture the 3D viewport: {ex}")
        return None
    finally:
        offscreen.free()
        space.overlay.show_overlays = showOverlaysPrev

    buffer.dimensions = width * height * 4
    pixels = np.asarray(buffer, dtype=np.uint8).astype(np.float32) / 255.0

    image = bpy.data.images.new("vray_veras_viewport", width, height)
    try:
        # The pixels are already display-referred (do_color_management=True). Store them as
        # 'Non-Color' so saving does not apply a second view transform.
        image.colorspace_settings.name = 'Non-Color'
        image.pixels.foreach_set(pixels)

        outPath = os.path.join(getV4BTempDir(), _OUTPUT_NAME)
        image.filepath_raw = outPath
        image.file_format  = 'JPEG'
        image.save()
    except Exception as ex:
        debug.printError(f"Veras: failed to save the viewport capture: {ex}")
        return None
    finally:
        bpy.data.images.remove(image)

    return outPath if os.path.exists(outPath) else None
