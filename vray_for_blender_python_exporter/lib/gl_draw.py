# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gpu
import numpy


from vray_blender.lib.camera_utils import Size, ViewParams


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
            DrawData.shader = self.createScreenQuadShader()


    def __del__(self):
        del self.texture


    def createScreenQuadShader(self):
        """ Create a shader to draw the textured screen quad we need for displaying in the viewport
            images received from V-Ray.
        """

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
        from gpu_extras.batch import batch_for_shader

        viewParams = self.viewParams

        x = 0.0
        y = 0.0
        w = windowSize.w
        h = windowSize.h

        if viewParams.crop and viewParams.canDrawWithOffset:
            # If rendering camera view, the region position is specified relative to the camera viewport
            # otherwise the viewport offsets are 0 and the position is relative to the whole rendering area
            x = viewParams.viewportOffsX + viewParams.regionStart.w

            # The Y-axis goes in opposite directions in VRay and OpenGL. Calculate the correct OpenGL position.
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

        batch = batch_for_shader(
            DrawData.shader, 'TRI_FAN',
            {
                "position": ((ln, bn), (rn, bn), (rn, tn), (ln, tn)),
                "uv": ((0, 0), (1, 0), (1, 1), (0, 1)),
            },
        )

        DrawData.shader.uniform_sampler("image", self.texture)
        batch.draw(DrawData.shader)
        




