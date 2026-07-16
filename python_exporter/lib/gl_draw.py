# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gpu
import numpy
from gpu_extras.batch import batch_for_shader
from vray_blender.lib.camera_utils import Size, ViewParams


# UVs for a quad with bottom-left origin. Shared between the fullscreen
# batch and the camera-offset batch.
_QUAD_UVS = ((0, 0), (1, 0), (1, 1), (0, 1))

# Cached full-screen quad batch (no crop, no offset). Rebuilt only when the
# shader is recreated.
_fullscreenBatch = None


def _getFullscreenBatch(shader):
    """ Return a cached batch for drawing a full-viewport quad. """
    global _fullscreenBatch
    if _fullscreenBatch is None:
        _fullscreenBatch = batch_for_shader(
            shader, 'TRI_FAN',
            {
                "position": ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)),
                "uv": _QUAD_UVS,
            },
        )
    return _fullscreenBatch


class DrawData:
    """ DrawData uploads a texture to the GPU and draws it
        on demand as a screen quad
    """

    # The shader is created just once
    shader = None

    def __init__(self, image: numpy.ndarray, viewParams: ViewParams):
        # Target render size
        self.imgW = image.shape[0]
        self.imgH = image.shape[1]
        self.viewParams = viewParams

        pixels = gpu.types.Buffer('FLOAT', image.shape[0] * image.shape[1] * image.shape[2], image.data)

        ## Generate texture
        self.texture = gpu.types.GPUTexture((self.imgW, self.imgH), format='RGBA32F', data=pixels)

        if not DrawData.shader:
            DrawData.shader = DrawData._createScreenQuadShader()

        self._cachedBatch = None
        self._cachedWindowSize = None


    def __del__(self):
        del self.texture


    @staticmethod
    def _createScreenQuadShader():
        """ Create a shader to draw the textured screen quad we need for displaying in the viewport
            images received from V-Ray.
        """
        global _fullscreenBatch
        _fullscreenBatch = None

        vertOut = gpu.types.GPUStageInterfaceInfo("vray_draw_viewport")
        vertOut.smooth('VEC2', "uvInterpolated")

        shaderInfo = gpu.types.GPUShaderCreateInfo()
        shaderInfo.sampler(0, 'FLOAT_2D', "image")
        shaderInfo.vertex_in(0, 'VEC2', "position")
        shaderInfo.vertex_in(1, 'VEC2', "uv")
        shaderInfo.vertex_out(vertOut)
        shaderInfo.fragment_out(0, 'VEC4', "colorOut")

        shaderInfo.vertex_source(
            "void main()"
            "{"
            "  uvInterpolated = uv;"
            "  gl_Position = vec4(position, 0.0, 1.0);"
            "}"
        )

        shaderInfo.fragment_source(
            "void main()"
            "{"
            "  colorOut = texture(image, uvInterpolated);"
            "}"
        )

        shader = gpu.shader.create_from_info(shaderInfo)
        del vertOut
        del shaderInfo

        return shader


    def draw(self, windowSize: Size):
        """ Draw the image to the viewport region.

        Args:
            windowSize (Size): The dimensions of the whole viewport region.
        """
        viewParams = self.viewParams

        if viewParams.crop and viewParams.canDrawWithOffset:
            # Camera view with offset — need a positioned quad
            x = viewParams.viewportOffsX + viewParams.regionStart.w
            y = windowSize.h - (viewParams.viewportOffsY + viewParams.regionStart.h + viewParams.regionSize.h)
            w = viewParams.regionSize.w
            h = viewParams.regionSize.h

            # Normalize texture coordinates to [0.0. 1.0]
            l = x / windowSize.w
            t = y / windowSize.h
            r = (x + w) / windowSize.w
            b = (y + h) / windowSize.h

            # Normalize vertex coordinates to [-1.0, 1.0]
            ln = l * 2 - 1.0
            tn = t * 2 - 1.0
            rn = r * 2 - 1.0
            bn = b * 2 - 1.0

            # Rebuild the batch only when the window size changes
            if self._cachedWindowSize != (windowSize.w, windowSize.h):
                self._cachedWindowSize = (windowSize.w, windowSize.h)
                self._cachedBatch = batch_for_shader(
                    DrawData.shader, 'TRI_FAN',
                    {
                        "position": ((ln, tn), (rn, tn), (rn, bn), (ln, bn)),
                        "uv": _QUAD_UVS,
                    },
                )

            batch = self._cachedBatch
        else:
            # Full viewport — use the shared fullscreen quad
            batch = _getFullscreenBatch(DrawData.shader)

        DrawData.shader.uniform_sampler("image", self.texture)
        batch.draw(DrawData.shader)

