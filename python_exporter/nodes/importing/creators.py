# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Per-plugin node creators for the import engine; dispatched from engine.createNode. """

import bpy

from vray_blender.plugins.BRDF import BRDFScanned
from vray_blender.lib import attribute_utils
from vray_blender.lib import image_utils
from vray_blender.nodes.curves_node import getCurvesNode, encodeMapping
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.sockets import addInput, getHiddenInput, moveExtendSocketToBottom
from vray_blender import debug
from vray_blender.exporting.tools import getInputSocketByName, getInputSocketByAttr, getOutputSocketByAttr
from vray_blender.vray_tools.import_common import getPluginByName
from vray_blender.nodes.importing.engine import (
    CollapsibleTypes,
    ImportContext,
    createNode,
    getSelectedOutputAttr,
    loadImage,
    _assignSocketValue,
    _collapseToValue,
    _createGenericNode,
    _createLinkedNode,
    _fillNodeProperties,
    _getInputSocket,
    _getPluginConnectedToInput,
    _getPluginFromLink,
    _isPluginLink,
    _pluginAttrsToNodeProps,
    _pluginAttrsToPropGroup,
)

from numpy import allclose
from mathutils import Matrix, Color, Vector


def _getOptionalPluginConnectedToInput(importContext, inputAttrName, pluginDesc):
    """ _getPluginConnectedToInput for an input that skipDefaults may have omitted entirely. """
    if inputAttrName not in pluginDesc['Attributes']:
        return None, None
    return _getPluginConnectedToInput(importContext, inputAttrName, pluginDesc)


def _createNodeBRDFLayered(importContext: ImportContext, pluginDesc):
    from vray_blender.plugins.BRDF.BRDFLayered import getLayerSocketNames

    def processSocket(inputSocket, attrValue):
        # Could happen with some broken files
        if not attrValue:
            return

        connectedPlugin, connectedOutput = _getPluginFromLink(importContext, attrValue)

        if connectedPlugin:
            _createLinkedNode(importContext, inputSocket, connectedOutput, connectedPlugin)
        else:
             _assignSocketValue(inputSocket, attrValue)

    brdfs     = pluginDesc['Attributes'].get('brdfs')
    weights   = pluginDesc['Attributes'].get('weights')
    opacities = pluginDesc['Attributes'].get('opacities')

    brdfLayeredNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeBRDFLayered', pluginDesc['Name'])
    _fillNodeProperties(importContext, brdfLayeredNode, pluginDesc, "BRDFLayered")

    if brdfs:
        for i, brdf in enumerate(brdfs[:-1]):
            humanIndex = i + 1

            brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(humanIndex)

            # Add each layer socket independently; the default node may carry only some of them.
            if brdfSockName not in brdfLayeredNode.inputs:
                addInput(brdfLayeredNode, 'VRaySocketBRDF',   brdfSockName)
            if weightSockName not in brdfLayeredNode.inputs:
                addInput(brdfLayeredNode, 'VRaySocketColor',  weightSockName).setValue((1.0, 1.0, 1.0))
            if opacitySockName not in brdfLayeredNode.inputs:
                addInput(brdfLayeredNode, 'VRaySocketWeight', opacitySockName).setValue(1.0)

            brdfSocket   = brdfLayeredNode.inputs[brdfSockName]
            weightSocket = brdfLayeredNode.inputs[weightSockName]
            opacitySocket = brdfLayeredNode.inputs[opacitySockName]

            processSocket(brdfSocket, brdf)

            # V-Ray's implicit weight for a layer with no 'weights' entry is 1.0.
            _assignSocketValue(weightSocket, (1.0, 1.0, 1.0))
            if weights and i < len(weights):
                processSocket(weightSocket, weights[i])

            if opacities and i < len(opacities):
                processSocket(opacitySocket, opacities[i])

        brdfSock = getInputSocketByName(brdfLayeredNode, "Base Material")
        processSocket(brdfSock, brdfs[-1])

    return brdfLayeredNode


def _createNodeBRDFScanned(importContext: ImportContext, pluginDesc):
    brdfScannedNode = NodeUtils.createNode(
        importContext.nodeTree, 'VRayNodeBRDFScanned', pluginDesc["Name"])

    # Always read the .vrscan preset before the plugin attributes, never after.
    scannedFile = pluginDesc['Attributes'].get('file')
    brdfScannedNode.BRDFScanned.file = importContext.resolvePath(scannedFile)
    BRDFScanned.onFileUpdate(brdfScannedNode.BRDFScanned)

    _fillNodeProperties(importContext, brdfScannedNode, pluginDesc, "BRDFScanned",
                        skippedAttrs=BRDFScanned.IMPORT_SKIPPED_ATTRS)
    BRDFScanned.applyImportedAttrs(brdfScannedNode.BRDFScanned, pluginDesc['Attributes'])
    return brdfScannedNode


# Canonical BitmapBuffer.rgb_color_space names the addon enum accepts, plus the aliases
# other hosts (and C4D) write for the same spaces.
_RGB_COLOR_SPACE_ALIASES = {
    'raw': 'raw', '': 'raw',
    'acescg': 'acescg',
    'lin_srgb': 'lin_srgb', 'srgb': 'lin_srgb', 'scene-linear rec.709-srgb': 'lin_srgb',
}


def _applyBitmapColorSpace(image: bpy.types.Image, bitmapBufferAttrs: dict):
    """ Set the Blender image's colorspace from the V-Ray BitmapBuffer color space, the
        inverse of exporting/cycles_export._exportCyclesImageNode. Leaves Blender's default
        for color spaces not representable by a fixed Blender name. """
    cs = _RGB_COLOR_SPACE_ALIASES.get(str(bitmapBufferAttrs.get('rgb_color_space', '')).strip().lower())
    if cs == 'raw':
        name, isData = 'Non-Color', True
    elif cs == 'acescg':
        name, isData = 'ACEScg', False
    elif cs == 'lin_srgb':
        tf = str(bitmapBufferAttrs.get('transfer_function', '3'))
        name, isData = ('Linear Rec.709' if tf == '0' else 'sRGB'), False
    else:
        return   # OCIO / unknown name: keep Blender's auto-assigned colorspace.
    try:
        image.colorspace_settings.name = name
        image.colorspace_settings.is_data = isData
    except (TypeError, RuntimeError) as ex:
        debug.printError(f"Import: unsupported image colorspace '{name}': {ex}")


def _imageFromRawBitmap(name: str, bufferAttrs: dict, ledger = None):
    """ Build a packed Blender image from a RawBitmapBuffer's embedded pixel data.

        RawBitmapBuffer has no external file - the pixels live inline as an INT_LIST, with
        'pixels_type' selecting the encoding (see the plugin descriptor). Decodes the RGBA
        float / 8-bit forms into a packed image; other encodings are skipped with a warning.

        Tracks a newly created image in 'ledger'; an image reused for a buffer already decoded
        is not tracked again. """
    import numpy as np

    # Several TexBitmaps commonly share one RawBitmapBuffer - decoding it per TexBitmap would
    # duplicate the image datablock and re-run the PNG encode in pack(). Reuse it untracked,
    # the way loadImage reuses an already loaded file.
    if (existing := bpy.data.images.get(name)) is not None:
        return existing

    pixels = bufferAttrs.get('pixels')
    w = int(bufferAttrs.get('width', 0) or 0)
    h = int(bufferAttrs.get('height', 0) or 0)
    pixelType = int(bufferAttrs.get('pixels_type', 0) or 0)
    if pixels is None or w <= 0 or h <= 0:
        return None

    ints = np.ascontiguousarray(np.asarray(pixels, dtype=np.int32))
    if pixelType == 1:                                    # Float RGBA: ints are float32 bit patterns
        if ints.size != w * h * 4:
            return None
        rgba = ints.view(np.float32).reshape(h, w, 4)
        isFloat = True
    elif pixelType == 0:                                  # 8-bit RGBA
        u = ints.view(np.uint32)
        if u.size == w * h:                               # one packed 32-bit pixel per int
            rgba = u.view(np.uint8).reshape(h, w, 4).astype(np.float32)
            rgba *= 1.0 / 255.0
        elif u.size == w * h * 4:                     # one byte per int
            rgba = ints.astype(np.float32).reshape(h, w, 4) / 255.0
        else:
            return None
        isFloat = False
    else:
        debug.printWarning(f"RawBitmapBuffer '{name}': pixels_type {pixelType} unsupported; texture skipped")
        return None

    # V-Ray stores rows top-to-bottom; Blender's image buffer is bottom-to-top.
    rgba = np.flipud(rgba)
    img = bpy.data.images.new(name, w, h, alpha=True, float_buffer=isFloat)
    img.pixels.foreach_set(np.ascontiguousarray(rgba, dtype=np.float32).ravel())
    img.pack()
    if ledger is not None:
        ledger.track(img)
    return img


def _createNodeTexBitmap(importContext: ImportContext, pluginDescTexBitmap):
    imageTextureNode = NodeUtils.createNode(
        importContext.nodeTree, 'VRayNodeMetaImageTexture', pluginDescTexBitmap['Name'])

    bitmapBufferName = pluginDescTexBitmap['Attributes']['bitmap']
    pluginDescBitmapBuffer = getPluginByName(importContext.vrsceneDict, bitmapBufferName)

    bitmapTexture = imageTextureNode.texture

    bitmapBufferAttrs = pluginDescBitmapBuffer['Attributes']

    # Normalize the color-space name to one the BitmapBuffer enum accepts (C4D/other hosts
    # write aliases like "sRGB" / "scene-linear Rec.709-sRGB").
    if (rawCs := bitmapBufferAttrs.get('rgb_color_space')) is not None:
        if (norm := _RGB_COLOR_SPACE_ALIASES.get(str(rawCs).strip().lower())) is not None:
            bitmapBufferAttrs['rgb_color_space'] = norm
    imageFilepath = importContext.resolvePath(bitmapBufferAttrs.get('file'))
    importDir = importContext.sceneBaseDir or None

    isRawBuffer = pluginDescBitmapBuffer['ID'] == 'RawBitmapBuffer' or 'pixels' in bitmapBufferAttrs
    isIFL = bool(imageFilepath) and imageFilepath.lower().endswith('.ifl')
    # UDIM paths hold a <UDIM>/<UVTILE> token.
    isUDIM = bool(imageFilepath) and ('<UDIM>' in imageFilepath or '<UVTILE>' in imageFilepath)

    if isRawBuffer:
        # RawBitmapBuffer holds embedded pixel data, no external file. The tracking belongs
        # inside _imageFromRawBitmap: an image shared with another TexBitmap must not be
        # tracked twice, or rollback would try to remove it twice.
        if rawImg := _imageFromRawBitmap(bitmapBufferName, bitmapBufferAttrs,
                                         ledger=importContext.ledger):
            bitmapTexture.image = rawImg
            _applyBitmapColorSpace(rawImg, bitmapBufferAttrs)
    elif isIFL:
        # An .ifl is a text file listing frames, not a Blender image.
        imageTextureNode.BitmapBuffer.use_external_image = True
    else:
        loadPath = imageFilepath.replace('<UDIM>', '1001').replace('<UVTILE>', 'u1_v1') if isUDIM else imageFilepath
        loadImage(loadPath, importDir, bitmapTexture, imageName=importContext.getImageName(imageFilepath),
                  ledger=importContext.ledger)

        # Restore Blender's image source from the imported BitmapBuffer.
        if image := bitmapTexture.image:
            if isUDIM:
                image.source = 'TILED'
                image.filepath = imageFilepath
            elif bitmapBufferAttrs.get('frame_sequence'):
                image.source = 'SEQUENCE'
                imageUser = bitmapTexture.image_user
                if not image_utils.applyDetectedSequenceRange(imageUser, image):
                    # Files not on disk - fall back to the offset stored in the .vrscene.
                    if (importedFrameOffset := bitmapBufferAttrs.get('frame_offset')) is not None:
                        imageUser.frame_offset = int(importedFrameOffset)

            _applyBitmapColorSpace(image, bitmapBufferAttrs)

    # Filling the Mapping Socket of Texture
    if "uvwgen" in pluginDescTexBitmap['Attributes']:
        connectedPlugin, outputSocketName = _getPluginConnectedToInput(
            importContext, "uvwgen", pluginDescTexBitmap)
        if connectedPlugin:
            connectedNode = createNode(importContext, connectedPlugin)
            if connectedNode:
                importContext.nodeTree.links.new(
                    connectedNode.outputs[outputSocketName],
                    imageTextureNode.inputs["Mapping"]
                )

    _pluginAttrsToNodeProps(pluginDescTexBitmap, imageTextureNode)
    if isRawBuffer:
        # RawBitmapBuffer has no property group of its own, but everything on it except the
        # pixel payload - colour space, transfer function, gamma, filtering - is shared with
        # BitmapBuffer, so it belongs on that group. _pluginAttrsToPropGroup skips the attrs
        # the group lacks, which is exactly pixels/alpha_pixels/pixels_type/width/height.
        _pluginAttrsToPropGroup(pluginDescBitmapBuffer, imageTextureNode.BitmapBuffer,
                                'BitmapBuffer')
    else:
        _pluginAttrsToNodeProps(pluginDescBitmapBuffer, imageTextureNode)

    return imageTextureNode


def _fillRamp(importContext: ImportContext, rampNode: bpy.types.Node, colors, positions, interpolation):
    from vray_blender.nodes.specials.gradient_ramp import _manageRampSockets, _texIndexToSockName

    ramp = rampNode.texture.color_ramp

    if isinstance(interpolation, (list, tuple)):
        interpolation = interpolation[0] if interpolation else 1

    # Resolve each stop to a flat color or a texture link into its "Point N" socket.
    stops = []
    for col, pos in zip(colors, positions):
        flat, tex = None, None
        if _isPluginLink(col):
            conPlugin = getPluginByName(importContext.vrsceneDict, col)
            if conPlugin is not None and conPlugin['ID'] in CollapsibleTypes:
                flat = _collapseToValue(importContext, conPlugin)
            if flat is None:
                tex = col
        else:
            flat = col
        stops.append({'color': flat, 'tex': tex,
                      'position': pos if not _isPluginLink(pos) else None})

    # Blender's ColorRamp ships with 2 elements and must retain at least one.
    while len(ramp.elements) < len(stops):
        ramp.elements.new(0.0)
    while len(ramp.elements) > len(stops) and len(ramp.elements) > 1:
        ramp.elements.remove(ramp.elements[-1])

    ramp.interpolation = {0: 'CONSTANT', 1: 'LINEAR'}.get(interpolation, 'EASE')

    elementStep = 1.0 / len(ramp.elements)
    for i, stop in enumerate(stops):
        col = stop['color'] if stop['color'] is not None else (1.0, 1.0, 1.0)
        el = ramp.elements[i]
        # TODO: Alpha?
        el.color = [col[0], col[1], col[2], 1.0]
        el.position = stop['position'] if stop['position'] is not None else i * elementStep

    _manageRampSockets(rampNode, ramp)
    for i, stop in enumerate(stops):
        if stop['tex'] is None:
            continue
        if (pointSock := rampNode.inputs.get(_texIndexToSockName(i))) is None:
            continue
        connectedPlugin, output = _getPluginFromLink(importContext, stop['tex'])
        if connectedPlugin is not None:
            _createLinkedNode(importContext, pointSock, output, connectedPlugin)


def _createNodeTexGradRamp(importContext: ImportContext, pluginDesc: dict):
    texGradRamp = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexGradRamp', pluginDesc['Name'])

    # colors/positions/interpolation are the ramp's parallel per-point lists, consumed by _fillRamp.
    _fillNodeProperties(importContext, texGradRamp, pluginDesc, 'TexGradRamp',
                        skippedAttrs={'colors', 'positions', 'interpolation'})
    attributes = pluginDesc['Attributes']

    # TexGradRamp only has 1 input socket of this type.
    rampSock = [sock for sock in texGradRamp.inputs if sock.bl_idname == 'VRaySocketColorRamp'][0]
    rampNode = rampSock.links[0].from_node

    # skipDefaults omits the ramp lists when the source ramp is left at its default.
    if (colors := attributes.get('colors')) and (positions := attributes.get('positions')):
        _fillRamp(importContext,
            rampNode,
            colors,
            positions,
            attributes.get('interpolation', 1)
        )

    return texGradRamp


def _createNodeTexRamp(importContext: ImportContext, pluginDesc: dict):
    """ Import-only Ramp texture. Mirrors _createNodeTexGradRamp: the color ramp child node is
        created by TexRamp's nodeInit; fill it from the plugin's colors/positions/interpolation. """
    texRamp = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexRamp', pluginDesc['Name'])

    _fillNodeProperties(importContext, texRamp, pluginDesc, 'TexRamp')
    attributes = pluginDesc['Attributes']

    rampSocks = [sock for sock in texRamp.inputs if sock.bl_idname == 'VRaySocketColorRamp']
    if rampSocks and rampSocks[0].links and 'colors' in attributes and 'positions' in attributes:
        rampNode = rampSocks[0].links[0].from_node
        # V-Ray TexRamp has a single interpolation enum; some exporters send it as a 1-element list.
        interpolation = attributes.get('interpolation', 1)
        if isinstance(interpolation, (list, tuple)):
            interpolation = interpolation[0] if interpolation else 1
        _fillRamp(importContext,
            rampNode,
            attributes['colors'],
            attributes['positions'],
            interpolation
        )

    return texRamp


def _createNodeTexBerconGrad(importContext: ImportContext, pluginDesc: dict):
    """ Import-only Bercon gradient. Mirrors _createNodeTexRamp: the color ramp child node is
        created by TexBerconGrad's nodeInit; fill it from the plugin's colors/positions. """
    from vray_blender.plugins.texture.TexBerconGrad import toGradRampInterpolation

    texBerconGrad = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexBerconGrad', pluginDesc['Name'])

    _fillNodeProperties(importContext, texBerconGrad, pluginDesc, 'TexBerconGrad',
                        skippedAttrs={'colors', 'positions', 'interpolation'})
    attributes = pluginDesc['Attributes']

    rampSocks = [sock for sock in texBerconGrad.inputs if sock.bl_idname == 'VRaySocketColorRamp']
    if rampSocks and rampSocks[0].links and \
            (colors := attributes.get('colors')) and (positions := attributes.get('positions')):
        _fillRamp(importContext,
            rampSocks[0].links[0].from_node,
            colors,
            positions,
            toGradRampInterpolation(attributes.get('interpolation', 0))
        )

    return texBerconGrad


def _createNodeMtlDisplacement(importContext: ImportContext, pluginDesc: dict):
    """ Material-based displacement. Generic fill, but convert the min_bound/max_bound
        COLOR triples to the derived min_bound_float/max_bound_float the UI + exporter use
        (mirrors plugins/material/MtlDisplacement.py exportCustom, same as GeomDisplacedMesh). """
    attrs = pluginDesc['Attributes']
    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeMtlDisplacement', pluginDesc['Name'])
    _fillNodeProperties(importContext, node, pluginDesc, 'MtlDisplacement',
                        skippedAttrs={'min_bound', 'max_bound'})
    pg = node.MtlDisplacement
    if attrs.get('use_bounds') or 'min_bound' in attrs or 'max_bound' in attrs:
        pg.use_bounds = True
        if (mb := attrs.get('min_bound')) is not None:
            pg.min_bound_float = mb[0] if isinstance(mb, (list, tuple)) else mb
        if (xb := attrs.get('max_bound')) is not None:
            pg.max_bound_float = xb[0] if isinstance(xb, (list, tuple)) else xb
    # displacement_amount (distance-typed) is already scaled by _fillNodeProperties above.
    return node


def _toonRampInterp(interpolations):
    """ First entry of a toon ramp's interpolation list (export broadcasts one value). """
    if isinstance(interpolations, (list, tuple)) and interpolations:
        return int(interpolations[0])
    if isinstance(interpolations, (int, float)):
        return int(interpolations)
    return 1


def _collapseValueList(importContext: ImportContext, values):
    """ Collapse a list of FloatToTex/plugin-link values to plain floats for a spline. """
    out = []
    for v in values:
        if _isPluginLink(v):
            plugin = getPluginByName(importContext.vrsceneDict, v)
            v = _collapseToValue(importContext, plugin) if plugin is not None else 0.0
        out.append(v if v is not None else 0.0)
    return out


# Remap curve channel -> component index of a color-valued knot (RGB remaps store the output
# color per knot; each channel's scalar curve takes its own component). HSV mirrors it.
_REMAP_CHANNEL_INDEX = {'red': 0, 'green': 1, 'blue': 2, 'hue': 0, 'saturation': 1, 'value': 2}

def _collapseRemapValues(importContext: ImportContext, attributes: dict):
    """ Resolve each remap curve's knot values in place: a knot value that is a constant texture
        (TexFloat/TexAColor, common in Max/Maya .vrscenes) becomes a plain float. A color-valued
        knot is decomposed to the per-channel component. """
    for key in [k for k in attributes if k.endswith('_values')]:
        idx = _REMAP_CHANNEL_INDEX.get(key[:-len('_values')])
        collapsed = []
        for v in _collapseValueList(importContext, attributes[key]):
            if isinstance(v, (list, tuple)) and not isinstance(v, str):
                pick = idx if (idx is not None and idx < len(v)) else 0
                collapsed.append(float(v[pick]) if v else 0.0)
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                collapsed.append(float(v))
            else:
                collapsed.append(0.0)
        attributes[key] = collapsed


def _createNodeBRDFToonMtl(importContext: ImportContext, pluginDesc: dict):
    """ BRDFToonMtl carries two gradient ramps (diffuse/specular) and a highlight-shape
        spline as parallel position/color/interpolation lists that the generic path drops.
        Fill the ramp + curve widgets the node's init() already created. """
    attrs = pluginDesc['Attributes']
    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeBRDFToonMtl', pluginDesc['Name'])
    _fillNodeProperties(importContext, node, pluginDesc, 'BRDFToonMtl',
                        skippedAttrs={'diffuse_positions', 'diffuse_colors', 'diffuse_interpolations',
                                      'specular_positions', 'specular_colors', 'specular_interpolations',
                                      'highlight_positions', 'highlight_values', 'highlight_interpolations'})

    # Diffuse / specular gradient ramps -> the VRayNodeColorRamp connected to each ramp socket.
    rampAttrs = {'diffuse_ramp':  ('diffuse_colors', 'diffuse_positions', 'diffuse_interpolations'),
                 'specular_ramp': ('specular_colors', 'specular_positions', 'specular_interpolations')}
    for sock in node.inputs:
        if sock.bl_idname != 'VRaySocketColorRamp' or getattr(sock, 'vray_attr', '') not in rampAttrs:
            continue
        colorsAttr, posAttr, interpAttr = rampAttrs[sock.vray_attr]
        colors, positions = attrs.get(colorsAttr), attrs.get(posAttr)
        if colors and positions and sock.links:
            _fillRamp(importContext, sock.links[0].from_node,
                      colors, positions, _toonRampInterp(attrs.get(interpAttr)))

    # Highlight-shape spline -> the combined "C" curve of the node's curves widget.
    positions, values = attrs.get('highlight_positions'), attrs.get('highlight_values')
    if positions and values:
        curvesNode = getCurvesNode(node)
        interps = attrs.get('highlight_interpolations') or [1] * len(positions)
        _importRemapSplineData(positions, _collapseValueList(importContext, values),
                               interps, curvesNode.mapping.curves[3])
        node.BRDFToonMtl['curves_data'] = encodeMapping(curvesNode.mapping)
    return node


def _createNodeVolumeVRayToon(importContext: ImportContext, pluginDesc: dict):
    """ VolumeVRayToon carries depth + angular line-width curves as parallel
        position/value/interpolation lists that the generic path drops. Fill the two
        curve widgets the node's init() created. """
    attrs = pluginDesc['Attributes']
    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeVolumeVRayToon', pluginDesc['Name'])
    _fillNodeProperties(importContext, node, pluginDesc, 'VolumeVRayToon',
                        skippedAttrs={'depthCurvePositions', 'depthCurveValues', 'depthCurveInterpolations',
                                      'angularCurvePositions', 'angularCurveValues', 'angularCurveInterpolations'})

    for curveType in ('depth', 'angular'):
        positions = attrs.get(f'{curveType}CurvePositions')
        values = attrs.get(f'{curveType}CurveValues')
        if not positions or not values:
            continue
        interps = attrs.get(f'{curveType}CurveInterpolations') or [1] * len(positions)
        curvesNode = getCurvesNode(node, f'_{curveType}')
        _importRemapSplineData(positions, _collapseValueList(importContext, values),
                               interps, curvesNode.mapping.curves[3])
    return node


def _importRemapSplineData(positions, values, interpolations, curve: bpy.types.CurveMap):
    numKnots = min(len(positions), len(values), len(interpolations))
    # A value curve holds scalar knots; color-mode remap data (tuples) can't go here.
    if any(not isinstance(values[i], (int, float)) for i in range(numKnots)):
        return
    for i in range(numKnots):
        # Splines in Blender will always have at least 2 points.
        if i < 2:
            p = curve.points[i]
            p.location.x = positions[i]
            p.location.y = values[i]
        else:
            p = curve.points.new(positions[i], values[i])
        p.handle_type = 'VECTOR' if interpolations[i] in (0, 1) else 'AUTO'


def _fillRemap(texRemap, mapping: bpy.types.CurveMapping, attributes: dict):
    # TexRemap has three mutually-exclusive layouts: RGB per-channel red/green/blue_*,
    # HSV hue/saturation/value_*, and float/value float_*.
    def _has(*keys):
        return all(k in attributes for k in keys)

    def _curve(prefix, curveIndex):
        _importRemapSplineData(attributes.get(f'{prefix}_positions', []),
                               attributes.get(f'{prefix}_values', []),
                               attributes.get(f'{prefix}_types', []),
                               mapping.curves[curveIndex])

    if _has('red_positions', 'red_values', 'red_types'):
        redPositions = attributes['red_positions']
        redValues = attributes['red_values']
        redInterpolations = attributes['red_types']
        greenPositions = attributes.get('green_positions', redPositions)
        greenValues = attributes.get('green_values', redValues)
        greenInterpolations = attributes.get('green_types', redInterpolations)
        bluePositions = attributes.get('blue_positions', redPositions)
        blueValues = attributes.get('blue_values', redValues)
        blueInterpolations = attributes.get('blue_types', redInterpolations)

        def _compareFloatLists(pos1, pos2):
            # A ramp knot value can be a texture plugin-link (str), not a float.
            if len(pos1) != len(pos2):
                return False
            if not all(isinstance(v, (int, float)) for v in pos1) \
                    or not all(isinstance(v, (int, float)) for v in pos2):
                return False
            return bool(allclose(pos1, pos2))
        if (_compareFloatLists(redPositions, greenPositions)
                and _compareFloatLists(greenPositions, bluePositions)
                and _compareFloatLists(redValues, greenValues)
                and _compareFloatLists(greenValues, blueValues)
                and redInterpolations == blueInterpolations == greenInterpolations
            ):
                texRemap.color_spline_type = "0"
                _importRemapSplineData(redPositions, redValues, redInterpolations, mapping.curves[3])
        else:
            _importRemapSplineData(redPositions, redValues, redInterpolations, mapping.curves[0])
            _importRemapSplineData(greenPositions, greenValues, greenInterpolations, mapping.curves[1])
            _importRemapSplineData(bluePositions, blueValues, blueInterpolations, mapping.curves[2])
            texRemap.color_spline_type = "1"
    elif _has('hue_positions', 'hue_values', 'hue_types'):
        _curve('hue', 0)
        _curve('saturation', 1)
        _curve('value', 2)
    elif _has('float_positions', 'float_values', 'float_types'):
        # RemapValue (float -> float): a single combined curve.
        texRemap.color_spline_type = "0"
        _curve('float', 3)
    elif any(k.endswith(('_positions', '_values')) for k in attributes):
        # Curve keys present but not in a recognized layout.
        debug.printWarning("TexRemap: no recognized remap curve data; leaving the default curve")
    # else: no curve data at all -> the linear input_min/max -> output_min/max mode.


def _fillRemapFromBezierCurve(texRemap, mapping: bpy.types.CurveMapping, attributes: dict):

    # A curve left at its default carries no 'points'/'types' at all (skipDefaults).
    attrPoints = attributes.get('points', [])

    redPositions = [float(p) for p in attrPoints[::6]]
    redValues    = [float(v) for v in attrPoints[1::6]]
    types        = [4] * len(redPositions) # Set all point types to Bezier

    remapAttributes = {
        'remapType': 0,
        'red_positions':   redPositions,
        'red_values':      redValues,
        'red_types':       types,
        'green_positions': redPositions,
        'green_values':    redValues,
        'green_types':     types,
        'blue_positions':  redPositions,
        'blue_values':     redValues,
        'blue_types':      types
    }

    _fillRemap(texRemap, mapping, remapAttributes)


def _fillRemapFromBezierCurveColor(texRemap, mapping: bpy.types.CurveMapping, attributes: dict):
    def getPositions(attrPoints):
        return [float(p) for p in attrPoints[::6]]

    def getValues(attrPoints):
        return [float(v) for v in attrPoints[1::6]]

    # A curve left at its default carries no 'points_r' (skipDefaults); missing green/blue
    # mean greyscale.
    redPoints   = attributes.get('points_r', [])
    greenPoints = attributes.get('points_g') or redPoints
    bluePoints  = attributes.get('points_b') or redPoints

    # Decode the 6-float knot stride exactly once.
    remapAttributes = {
        'remapType': 0,
        'red_positions':   getPositions(redPoints),
        'red_values':      getValues(redPoints),
        'red_types':       [4] * len(redPoints[::6]),   # Set all point types to Bezier
        'green_positions': getPositions(greenPoints),
        'green_values':    getValues(greenPoints),
        'green_types':     [4] * len(greenPoints[::6]),
        'blue_positions':  getPositions(bluePoints),
        'blue_values':     getValues(bluePoints),
        'blue_types':      [4] * len(bluePoints[::6]),
    }

    _fillRemap(texRemap, mapping, remapAttributes)


def _createNodeTexRemap(importContext: ImportContext, pluginDesc: dict):
    texRemapNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexRemap', pluginDesc['Name'])

    attributes = pluginDesc['Attributes']
    _collapseRemapValues(importContext, attributes)   # resolve constant TexFloat/TexAColor knot values
    curvesNode = getCurvesNode(texRemapNode)
    _fillRemap(
        texRemapNode.TexRemap,
        curvesNode.mapping,
        attributes
    )

    # The per-channel '*_types' keys are curve data consumed by _fillRemap, not node props.
    # Never delete them from pluginDesc['Attributes']; that dict is shared across the import.
    typeKeys = {k for k in attributes if '_types' in k}
    _fillNodeProperties(importContext, texRemapNode, pluginDesc, 'TexRemap', skippedAttrs=typeKeys)

    return texRemapNode


def _createNodeTexRemapFromBezierCurve(importContext: ImportContext, pluginDesc: dict, isColor: False):
    texRemapNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexRemap', pluginDesc['Name'])

    attributes = pluginDesc['Attributes']
    curvesNode = getCurvesNode(texRemapNode)

    if isColor:
        _fillRemapFromBezierCurveColor(texRemapNode.TexRemap, curvesNode.mapping, attributes)
    else:
        _fillRemapFromBezierCurve(texRemapNode.TexRemap, curvesNode.mapping, attributes)

    # Manually import connected inputs because we have created a different type of plugin and
    # the automatic walk won't find the correct sockets
    inputAttrName = 'input_color' if isColor else 'input_float'
    connectedPlugin, connectedOutput = _getOptionalPluginConnectedToInput(importContext, inputAttrName, pluginDesc)

    if connectedPlugin:
        inputColorSocket = getInputSocketByAttr(texRemapNode, 'input_color')
        _createLinkedNode(importContext, inputColorSocket, connectedOutput, connectedPlugin)

    return texRemapNode


def _createNodeTexVectorProduct(importContext: ImportContext, pluginDesc: dict):
    texVectorProductNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexVectorProduct', pluginDesc['Name'])

    _fillNodeProperties(importContext, texVectorProductNode, pluginDesc, 'TexVectorProduct')

    attributes = pluginDesc['Attributes']
    if (inp1 := attributes.get("input1")) and isinstance(inp1, Color):
        vectorNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeVector')
        vectorNode.value = Vector(inp1[:])
        importContext.nodeTree.links.new(vectorNode.outputs[0], getInputSocketByAttr(texVectorProductNode, "input1"))
    return texVectorProductNode


def _createNodeMtlMulti(importContext: ImportContext, pluginDesc: dict):
    """ Import a MtlMulti used as a SWITCH material (it has an mtlid_gen* generator) into
        a VRayNodeMtlMulti ("V-Ray Switch Mtl") node: wire each sub-material into a
        'Material N' socket with its ID, and the generator into 'Switch Texture'.

        A MtlMulti WITHOUT a generator is a per-face-ID multi-material and is handled as
        object material slots by the scene importer, not through this node. """
    from vray_blender.nodes.specials.material import getMaterialSockets

    attrs = pluginDesc['Attributes']
    mtls = attrs.get('mtls_list')
    ids = attrs.get('ids_list')

    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeMtlMulti', pluginDesc['Name'])
    # Non-list attrs (wrap_id, mtlid_gen*/generator link) fill generically.
    _fillNodeProperties(importContext, node, pluginDesc, 'MtlMulti',
                        skippedAttrs={'mtls_list', 'ids_list'})

    if mtls:
        while node.materials < len(mtls):
            node.addMaterial()
        mtlSockets = getMaterialSockets(node)
        for i, mtl in enumerate(mtls):
            sock = mtlSockets[i]
            sock.enabled = True
            if ids and i < len(ids):
                sock.value = int(ids[i])
            if _isPluginLink(mtl):
                connectedPlugin, connectedOutput = _getPluginFromLink(importContext, mtl)
                if connectedPlugin:
                    _createLinkedNode(importContext, sock, connectedOutput, connectedPlugin)

    return node


def _createNodeTexMulti(importContext: ImportContext, pluginDesc: dict):
    """ TexMulti carries its sub-textures in the textures_list/ids_list PLUGIN_LISTs,
        which the generic path cannot follow (they are lists, not single links). Create
        the node and populate its 'Texture N' sockets from the lists. """
    attrs = pluginDesc['Attributes']
    textures = attrs.get('textures_list') or []
    ids = attrs.get('ids_list')

    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexMulti', pluginDesc['Name'])
    _fillNodeProperties(importContext, node, pluginDesc, 'TexMulti',
                        skippedAttrs={'textures_list', 'ids_list', 'random_mode'})

    # random_mode is a bitmask that the addon exposes as the derived random_mode_* bools
    # (see plugins/texture/TexMulti.py _getRandomMode); decompose it back into those flags.
    if (randomMode := attrs.get('random_mode')) is not None:
        randomModeBits = {
            'random_mode_node_handle': 1, 'random_mode_render_id': 2, 'random_mode_node_name': 4,
            'random_mode_particle_id': 8, 'random_mode_instance_id': 16, 'random_mode_polygon_selection': 32,
            'random_mode_object_id': 64, 'random_mode_mesh_element': 128, 'random_mode_user_attribute': 256,
            'random_mode_scene_name': 512,
        }
        mask = int(randomMode)
        for attrName, bit in randomModeBits.items():
            if hasattr(node.TexMulti, attrName):
                setattr(node.TexMulti, attrName, bool(mask & bit))

    texSockets = [s for s in node.inputs if s.bl_idname == 'VRaySocketTexMulti']

    if textures:
        while len(texSockets) < len(textures):
            addInput(node, 'VRaySocketTexMulti', f"Texture {len(texSockets) + 1}")
            texSockets = [s for s in node.inputs if s.bl_idname == 'VRaySocketTexMulti']
        moveExtendSocketToBottom(node)

        for i, tex in enumerate(textures):
            sock = texSockets[i]
            sock.use = True
            if ids and i < len(ids):
                sock.id = int(ids[i])
            if _isPluginLink(tex):
                connectedPlugin, connectedOutput = _getPluginFromLink(importContext, tex)
                if connectedPlugin:
                    _createLinkedNode(importContext, sock, connectedOutput, connectedPlugin)
            else:
                _assignSocketValue(sock, tex)

    # The exporter emits every socket with 'use' set, linked or not.
    for sock in texSockets[len(textures):]:
        sock.use = False

    return node


# TexLayered (Maya, C4D, XSI, Nuke) and TexLayeredMax (3ds Max) are the same node in the addon,
# but their blend_modes enums are unrelated.
# TexLayered mode -> (TexLayeredMax mode, name). None means the mode has no equivalent.
_TEX_LAYERED_BLEND_MODE_TO_MAX = {
    0:  (0,    'None'),        # -> Normal (approximate: 'None' also overwrites the alpha)
    1:  (0,    'Over'),        # -> Normal
    2:  (None, 'In'),
    3:  (None, 'Out'),
    4:  (2,    'Add'),
    5:  (3,    'Subtract'),
    6:  (5,    'Multiply'),
    7:  (19,   'Difference'),
    8:  (8,    'Lighten'),
    9:  (4,    'Darken'),
    10: (13,   'Saturate'),    # -> Spotlight blend
    11: (None, 'Desaturate'),
    12: (12,   'Illuminate'),  # -> Spotlight
}

# The modes for which TexLayered's bottom layer agrees with TexLayeredMax's (see below).
_TEX_LAYERED_NEUTRAL_FIRST_MODES = (0, 1, 4, 7, 8)


def _mapTexLayeredBlendModes(blendModes, pluginName: str) -> list[int]:
    """ Translate a TexLayered blend_modes list to the TexLayeredMax modes with the same result. """
    mapped, unmapped = [], []

    for blendMode in blendModes:
        maxMode, name = _TEX_LAYERED_BLEND_MODE_TO_MAX.get(int(blendMode), (None, str(blendMode)))
        if maxMode is None:
            unmapped.append(name)
            maxMode = 0
        mapped.append(maxMode)

    if unmapped:
        debug.printWarning(f"'{pluginName}': blend mode(s) {', '.join(unmapped)} have no "
                           f"TexLayeredMax equivalent, imported as 'Normal'")

    # TexLayeredMax seeds the stack with the bottom layer's color; TexLayered blends it against black.
    firstMode = int(blendModes[0])
    if firstMode not in _TEX_LAYERED_NEUTRAL_FIRST_MODES:
        firstName = _TEX_LAYERED_BLEND_MODE_TO_MAX.get(firstMode, (None, str(firstMode)))[1]
        debug.printWarning(f"'{pluginName}': bottom layer blends with '{firstName}', "
                           f"which may render differently as TexLayeredMax")

    return mapped


def _createNodeTexLayered(importContext: ImportContext, pluginDesc: dict):
    """ Builds a TexLayeredMax node for both TexLayeredMax and its DCC-neutral sibling
        TexLayered, which is the same texture without per-layer masks and opacities. """
    from vray_blender.nodes.specials.texture import VRayNodeTexLayeredMax

    textures = pluginDesc['Attributes'].get('textures')
    masks = pluginDesc['Attributes'].get('masks')
    opacities = pluginDesc['Attributes'].get('opacities')
    blendModes = pluginDesc['Attributes'].get('blend_modes')

    if pluginDesc['ID'] == 'TexLayered' and blendModes:
        blendModes = _mapTexLayeredBlendModes(blendModes, pluginDesc['Name'])

    texLayeredNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexLayeredMax', pluginDesc['Name'])
    # Never delete these from pluginDesc['Attributes']; that dict is shared across the import.
    _fillNodeProperties(importContext, texLayeredNode, pluginDesc, "TexLayeredMax",
                        skippedAttrs={'textures', 'masks', 'opacities'})

    if textures:
        for i, texture in enumerate(textures):
            humanIndex = i + 1
            # NOTE: Node already has two inputs
            texSocketName = f"Texture {humanIndex}"
            if texSocketName not in texLayeredNode.inputs:
                # addLayer takes a 1-based human index.
                VRayNodeTexLayeredMax.addLayer(texLayeredNode, humanIndex)
            if texSocketName not in texLayeredNode.inputs:
                # addLayer did not produce the expected socket.
                continue

            textureSocket = texLayeredNode.inputs[texSocketName]
            maskSocket = texLayeredNode.inputs[f"Mask {humanIndex}"]
            opacitySocket = getHiddenInput(texLayeredNode, f"Opacity {humanIndex}")
            blendModeSocket = getHiddenInput(texLayeredNode, f"Blend Mode {humanIndex}")

            if _isPluginLink(texture):
                connectedPlugin, connectedOutput = _getPluginFromLink(importContext, texture)
                if connectedPlugin:
                    _createLinkedNode(importContext, textureSocket, connectedOutput, connectedPlugin)
            else:
                textureSocket.value = texture

            if masks and i < len(masks):
                mask = masks[i]
                if _isPluginLink(mask):
                    connectedPlugin, connectedOutput = _getPluginFromLink(importContext, mask)
                    if connectedPlugin:
                        _createLinkedNode(importContext, maskSocket, connectedOutput, connectedPlugin)
                else:
                    maskSocket.value = mask

            opacitySocket.value = float(opacities[i]) if opacities and i < len(opacities) else 1.0
            if blendModes and i < len(blendModes):
                blendModeSocket.value = str(blendModes[i])
    importedLayers = len(textures) if textures else 0

    # init() adds two default layers; always remove the sockets past the imported ones.
    for humanIndex in range(importedLayers + 1, texLayeredNode.layers + 1):
        for sockName in (f"Texture {humanIndex}", f"Mask {humanIndex}"):
            if sock := texLayeredNode.inputs.get(sockName):
                texLayeredNode.inputs.remove(sock)
        for sockName in (f"Opacity {humanIndex}", f"Blend Mode {humanIndex}"):
            if sock := getHiddenInput(texLayeredNode, sockName):
                texLayeredNode.inputs.remove(sock)

    texLayeredNode.layers = importedLayers

    return texLayeredNode


def _createNodeLightIES(importContext: ImportContext, pluginDesc: dict):
    lightIES = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeLightIES', pluginDesc["Name"])

    _fillNodeProperties(importContext, lightIES, pluginDesc, 'LightIES')

    iesFile = pluginDesc['Attributes'].get('ies_file')
    lightIES.LightIES.ies_file = importContext.resolvePath(iesFile)

    return lightIES


def _createNodeLightLuminaire(importContext: ImportContext, pluginDesc: dict):
    """ Create a Luminaire light node. Its cache ('file') is an external asset. """
    luminaire = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeLightLuminaire', pluginDesc["Name"])

    _fillNodeProperties(importContext, luminaire, pluginDesc, 'LightLuminaire')

    if cacheFile := pluginDesc['Attributes'].get('file'):
        luminaire.LightLuminaire.file = importContext.resolvePath(cacheFile)

    return luminaire


def _createNodeTexNormalMap(importContext: ImportContext, pluginDesc: dict):
    assert pluginDesc['ID'] in [ "TexNormalBump", "TexNormalMapFlip" ]

    normalBumpNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexNormalBump')
    
    # We need to handle both directions in graphs with TexNormalBump->TexNormalMapFlip
    # and TexNormalMapFlip->TexNormalBump.
    normalMap, texmap, normalFlip, outputName = None, None, None, ""
    if pluginDesc['ID'] == "TexNormalBump":
        normalMap = pluginDesc
        texmap, outputName = _getOptionalPluginConnectedToInput(importContext, "bump_tex_color", pluginDesc)
        if texmap is not None and texmap['ID'] == "TexNormalMapFlip":
            normalFlip = texmap
            texmap, outputName = _getOptionalPluginConnectedToInput(importContext, "texmap", texmap)
    elif pluginDesc['ID'] == "TexNormalMapFlip":
        normalFlip = pluginDesc
        texmap, outputName = _getOptionalPluginConnectedToInput(importContext, "texmap", pluginDesc)
        if texmap is not None and texmap['ID'] == "TexNormalBump":
            normalMap = texmap
            texmap, outputName = _getOptionalPluginConnectedToInput(importContext, "bump_tex_color", texmap)

    if normalFlip is not None:
        normalBumpNode.TexNormalBump.flip_red = normalFlip['Attributes'].get('flip_red', False)
        normalBumpNode.TexNormalBump.flip_green = normalFlip['Attributes'].get('flip_green', False)
        normalBumpNode.TexNormalBump.swap_red_green = normalFlip['Attributes'].get('swap_redgreen', False)
    if normalMap is not None:
        normalMap['Attributes'].setdefault('bump_object_space', False)
        _fillNodeProperties(importContext, normalBumpNode, normalMap, "TexNormalBump", {'bump_tex_color'})
    if texmap:
        if bumpTexSocket := _getInputSocket(normalBumpNode, "bump_tex_color"):
            _createLinkedNode(importContext, bumpTexSocket, outputName, texmap)

    return normalBumpNode


def _checkIdentityMatrix(pluginDesc: dict, paramName: str):
    if paramName not in pluginDesc['Attributes']:
        return True
    attrValue = pluginDesc['Attributes'][paramName]
    transform = attrValue if isinstance(attrValue, Matrix) else attribute_utils.attrValueToMatrix(attrValue, True)
    return allclose(transform, Matrix.Identity(4))


def _createNodeUVWGenChannel(importContext: ImportContext, pluginDesc: dict):
    # A default-valued UVWGenChannel (identity transforms, default channel) adds nothing
    # over the socket's implicit default mapping.
    attrs = pluginDesc['Attributes']
    isIdentity = _checkIdentityMatrix(pluginDesc, "uvw_transform") and _checkIdentityMatrix(pluginDesc, "tex_transform")
    hasChannelOverride = 'uvw_channel' in attrs and int(attrs['uvw_channel']) not in (0, -1)

    if isIdentity and not hasChannelOverride:
        return None
    return _createGenericNode(importContext, pluginDesc)


def _createNodeUVWGenSelect(importContext: ImportContext, pluginDesc: dict):
    # UVWGenSelect picks a UV set by a string id (uvwgen_id), resolved at render time against the
    # wrapping material's map. There is no Blender node for it; fixPluginParams populates the
    # 'uvwgen' fallback from the MtlUVWSelect wrapper (see _resolveUVWGenSelectFallbacks).
    fallbackRef = pluginDesc['Attributes'].get('uvwgen')
    if _isPluginLink(fallbackRef):
        if (fallbackPlugin := _getPluginFromLink(importContext, fallbackRef)[0]) is not None:
            return createNode(importContext, fallbackPlugin)
    return None


def _createMappingNode(importContext: ImportContext, pluginDesc, pluginType):
    pluginName = pluginDesc['Name']
    mappingNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeUVWMapping', pluginName)

    match pluginType:
        case 'UVWGenMayaPlace2dTexture':
            mappingNode.mapping_node_type = 'UV'
        case 'UVWGenProjection':
            mappingNode.mapping_node_type = 'PROJECTION'
        case 'UVWGenObject':
            mappingNode.mapping_node_type = 'OBJECT'
        case 'UVWGenEnvironment':
            mappingNode.mapping_node_type = 'ENVIRONMENT'
        case _: # Just in case
            return mappingNode

    _fillNodeProperties(importContext, mappingNode, pluginDesc, pluginType)

    if pluginType == 'UVWGenMayaPlace2dTexture':
        _retargetUVSetName(importContext, mappingNode)

    return mappingNode


def _retargetUVSetName(importContext: ImportContext, mappingNode):
    """ Point uv_set_name at the UV layer the mesh import actually created. """
    propGroup = mappingNode.UVWGenMayaPlace2dTexture
    uvSetName = propGroup.uv_set_name
    if not uvSetName or uvSetName.startswith('vray_channel_id_'):
        return

    if (channelId := importContext.uvChannelId(uvSetName)) is not None:
        propGroup.uv_set_name = f'vray_channel_id_{channelId}'


def _createNodeVRayDecal(importContext: ImportContext, pluginDesc: dict):
    outputNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeDecalOutput', 'VRayDecal')
    # height_offset exports as a percentage (fraction*100); reverse it on import.
    _fillNodeProperties(importContext, outputNode, pluginDesc, 'VRayDecal',
                        skippedAttrs={'material', 'height_offset'})

    if (heightOffset := pluginDesc['Attributes'].get('height_offset')) is not None:
        outputNode.VRayDecal.height_offset = float(heightOffset) / 100.0

    return outputNode


# Explicit per-type wrappers for the parameterized creators.
def _createNodeUVWGenMayaPlace2dTexture(importContext: ImportContext, pluginDesc: dict):
    return _createMappingNode(importContext, pluginDesc, 'UVWGenMayaPlace2dTexture')


def _createNodeUVWGenProjection(importContext: ImportContext, pluginDesc: dict):
    return _createMappingNode(importContext, pluginDesc, 'UVWGenProjection')


def _createNodeUVWGenObject(importContext: ImportContext, pluginDesc: dict):
    return _createMappingNode(importContext, pluginDesc, 'UVWGenObject')


def _createNodeUVWGenEnvironment(importContext: ImportContext, pluginDesc: dict):
    return _createMappingNode(importContext, pluginDesc, 'UVWGenEnvironment')


def _createNodeTexBezierCurve(importContext: ImportContext, pluginDesc: dict):
    return _createNodeTexRemapFromBezierCurve(importContext, pluginDesc, isColor=False)


def _createNodeTexBezierCurveColor(importContext: ImportContext, pluginDesc: dict):
    return _createNodeTexRemapFromBezierCurve(importContext, pluginDesc, isColor=True)


def _createNodeTexAColorChannel(importContext: ImportContext, pluginDesc: dict):
    """ TexAColorChannel returns one channel of color_a * mult_a, chosen by its 'mode'.
        TexAColorOp computes the same product and exposes every channel as its own output
        socket, so import as that; the link resolver picks the socket 'mode' names. """
    attrs = dict(pluginDesc['Attributes'])
    # TexAColorOp's 'mode' is the arithmetic operation, not the channel. Its channel outputs
    # are always Color A * Mult A, so pin the operation to Result A.
    attrs['mode'] = '0'

    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexAColorOp', pluginDesc['Name'])
    _fillNodeProperties(importContext, node,
                        {**pluginDesc, 'ID': 'TexAColorOp', 'Attributes': attrs}, 'TexAColorOp')

    # The channel outputs ship hidden; show the one this plugin selected.
    if outputAttr := getSelectedOutputAttr(pluginDesc):
        if outSocket := getOutputSocketByAttr(node, outputAttr):
            outSocket.hide = False
            outSocket.enabled = True

    return node


def _createNodeTexVertexColorDirect(importContext: ImportContext, pluginDesc: dict):
    """ TexVertexColorDirect reads a named colour set. TexUserColor is the addon's own node
        for that (and what the Cycles export path already emits), so import as one. """
    attrs = pluginDesc['Attributes']
    userAttrs = {'attribute_priority': '0'}   # 0 = Map Channel, i.e. a named colour set
    if (colorSet := attrs.get('color_set_name')) is not None:
        userAttrs['user_attribute'] = colorSet
    if (defaultColor := attrs.get('default_color')) is not None:
        userAttrs['default_color'] = defaultColor

    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexUserColor', pluginDesc['Name'])
    _fillNodeProperties(importContext, node,
                        {**pluginDesc, 'ID': 'TexUserColor', 'Attributes': userAttrs}, 'TexUserColor')
    # Addon-only selector: 1 = Color Attribute, so the UI does a prop_search over colour attributes.
    node.TexUserColor.mode = '1'
    return node


def _createNodeTexMaxHairInfo(importContext: ImportContext, pluginDesc: dict):
    """ TexMaxHairInfo returns one hair property chosen by its 'output'; TexHairSampler
        exposes each as its own output socket. Modes with no counterpart (hair opacity,
        transparency and incandescence - TexHairSampler excludes the latter two) keep the
        source plugin's own node so they still render. """
    attrs = pluginDesc['Attributes']

    if not getSelectedOutputAttr(pluginDesc):
        debug.printWarning(f"'{pluginDesc['Name']}' (TexMaxHairInfo) uses output mode "
                           f"{attrs.get('output')}, which the V-Ray Hair Sampler does not expose; "
                           f"importing it as a Max Hair Info node instead.")
        return _createGenericNode(importContext, pluginDesc)

    node = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexHairSampler', pluginDesc['Name'])
    # max_distance is the only input the two share. seed_offset/color_a/color_b/bias drive the
    # Max map's own random-colour blend, which TexHairSampler has no equivalent for.
    hairAttrs = {}
    if (maxDistance := attrs.get('max_distance')) is not None:
        hairAttrs['hair_max_distance'] = maxDistance
    _fillNodeProperties(importContext, node,
                        {**pluginDesc, 'ID': 'TexHairSampler', 'Attributes': hairAttrs},
                        'TexHairSampler')
    return node


# Per-plugin node creators dispatched from engine.createNode. Plugin types not listed
# here import through the generic path (engine._createGenericNode).
NODE_CREATORS = {
    'TexAColorChannel':         _createNodeTexAColorChannel,
    'TexMaxHairInfo':           _createNodeTexMaxHairInfo,
    'TexVertexColorDirect':     _createNodeTexVertexColorDirect,
    'BRDFLayered':              _createNodeBRDFLayered,
    'BRDFScanned':              _createNodeBRDFScanned,
    'TexBitmap':                _createNodeTexBitmap,
    'TexGradRamp':              _createNodeTexGradRamp,
    'TexBerconGrad':            _createNodeTexBerconGrad,
    'TexRamp':                  _createNodeTexRamp,
    'TexRemap':                 _createNodeTexRemap,
    'TexVectorProduct':         _createNodeTexVectorProduct,
    'TexLayeredMax':            _createNodeTexLayered,
    'TexLayered':               _createNodeTexLayered,
    'TexMulti':                 _createNodeTexMulti,
    # Only switch-MtlMulti reaches here (as a material root or a nested link);
    # per-face MtlMulti is applied as object material slots by the scene importer.
    'MtlMulti':                 _createNodeMtlMulti,
    'UVWGenMayaPlace2dTexture': _createNodeUVWGenMayaPlace2dTexture,
    'UVWGenProjection':         _createNodeUVWGenProjection,
    'UVWGenObject':             _createNodeUVWGenObject,
    'UVWGenEnvironment':        _createNodeUVWGenEnvironment,
    'UVWGenChannel':            _createNodeUVWGenChannel,
    'UVWGenSelect':             _createNodeUVWGenSelect,
    'LightIES':                 _createNodeLightIES,
    'LightLuminaire':           _createNodeLightLuminaire,
    'TexNormalMapFlip':         _createNodeTexNormalMap,
    'TexNormalBump':            _createNodeTexNormalMap,
    'TexBezierCurve':           _createNodeTexBezierCurve,
    'TexBezierCurveColor':      _createNodeTexBezierCurveColor,
    'VRayDecal':                _createNodeVRayDecal,
    'MtlDisplacement':          _createNodeMtlDisplacement,
    'BRDFToonMtl':              _createNodeBRDFToonMtl,
    'VolumeVRayToon':           _createNodeVolumeVRayToon,
}
