# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
import gpu
import numpy as np

from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender import debug

# Long edge cap for the captured image, balancing detail against upload size.
# 3ds Max sends a JPEG viewport preview; we match the format.
_MAX_DIMENSION = 2048
_OUTPUT_NAME   = "veras_viewport.jpg"


def _findView3D(context: bpy.types.Context):
    """ Return (area, region, rv3d, space) for the 3D viewport to capture, or Nones. """
    area = context.area if (context.area and context.area.type == 'VIEW_3D') else None
    if area is None:
        area = next((a for a in context.screen.areas if a.type == 'VIEW_3D'), None)
    if area is None:
        return None, None, None, None

    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    space  = area.spaces.active
    rv3d   = space.region_3d if space else None
    return area, region, rv3d, space


def captureViewportToFile(context: bpy.types.Context):
    """ Render the active 3D viewport offscreen as a clean shaded image (overlays hidden,
        current view) and save it as a JPEG in the V4B temp directory.

        Must be called on the main thread from an operator (it needs the GPU context).
        Returns the absolute file path on success, or None if the viewport could not be
        captured.
    """
    area, region, rv3d, space = _findView3D(context)
    if not (area and region and rv3d and space):
        return None

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
