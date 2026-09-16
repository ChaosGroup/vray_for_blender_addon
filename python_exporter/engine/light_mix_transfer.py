# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import math

import bpy

from vray_blender.lib.lib_utils import getLightPluginType, getLightPropGroup
from vray_blender import debug


# Light select plugin name prefix used by the exporter (see Names.pluginObject)
_LS_PREFIX = "renderChannelLightSelect@"

# Maps V-Ray light plugin type to (color_property, intensity_property).
# Color properties use the Blender 'COLOR_TEXTURE' composite property name
# (color_colortex) rather than the raw V-Ray SDK parameter name (color).
_LIGHT_PROPS = {
    'LightRectangle':  ('color_colortex', 'intensity'),
    'LightSphere':     ('color_colortex', 'intensity'),
    'LightDome':       ('color_colortex', 'intensity'),
    'SunLight':        ('filter_color', 'intensity_multiplier'),
    'LightMesh':       ('color_colortex', 'intensity'),
    'LightIES':        ('color_colortex', 'power'),
    'LightSpot':       ('color_colortex', 'intensity'),
    'LightOmni':       ('color_colortex', 'intensity'),
    'LightAmbient':    ('color_colortex', 'intensity'),
    'MayaLightDirect': ('color_colortex', 'intensity'),
    # Both are multipliers over the radiance baked into the luminaire cache.
    'LightLuminaire':  ('color_colortex', 'intensity'),
}


class LightMixMapping:
    """ Stores the mapping from light select channel names to Blender scene objects.
        Built during export, used at transfer-to-scene time.
    """
    def __init__(self):
        # channel name -> set of Blender light object names
        self.lightChannels: dict[str, set[str]] = {}
        # channel name -> set of material names
        self.materialChannels: dict[str, set[str]] = {}

    def clear(self):
        self.lightChannels.clear()
        self.materialChannels.clear()

    def addLight(self, channelName: str, objectName: str):
        """ Register a light object under a channel name. """
        self.lightChannels.setdefault(channelName, set()).add(objectName)

    def addMaterial(self, channelName: str, materialName: str):
        """ Register an emissive material under a channel name. """
        self.materialChannels.setdefault(channelName, set()).add(materialName)


# Singleton mapping, populated during export
_mapping = LightMixMapping()


def getLightMixMapping() -> LightMixMapping:
    return _mapping


def applyLightMixChanges(changes: list):
    """ Apply light mix changes from VFB back to the Blender scene.

        Each entry in 'changes' is a tuple:
        (pluginName: str, colorR: float, colorG: float, colorB: float, intensityMult: float, enabled: bool)
    """
    debug.printDebug(f"Light Mix Transfer: received {len(changes)} changes")

    sceneLights = [o for o in bpy.context.scene.objects if o.type == 'LIGHT']

    restChange = None
    selfIllumChange = None
    changedLights = set()    # names of light objects already changed
    changedMaterials = set() # names of materials already changed

    for change in changes:
        pluginName, colorR, colorG, colorB, intensityMult, enabled = change

        if not pluginName:
            # Empty plugin name = "Rest" category
            restChange = change
            continue

        channelName = _extractChannelName(pluginName)

        if channelName == "Environment":
            # Environment changes are not applied to scene, matching C4D behavior
            continue

        if channelName == "Self_Illumination":
            selfIllumChange = change
            continue

        # Apply to lights using the stored mapping
        lightObjNames = _mapping.lightChannels.get(channelName)
        if lightObjNames:
            for objName in lightObjNames:
                if obj := bpy.data.objects.get(objName):
                    if obj.type == 'LIGHT':
                        _changeLightProperties(obj, colorR, colorG, colorB, intensityMult, enabled)
                        changedLights.add(obj.name)

        # Apply to materials using the stored mapping.
        # channelName is the node name (unique within the material's tree).
        materialNames = _mapping.materialChannels.get(channelName)
        for matName in (materialNames or []):
            if mat := bpy.data.materials.get(matName):
                _changeMaterialProperties(mat, colorR, colorG, colorB, intensityMult, enabled, channelName)
                changedMaterials.add(matName)

        if not lightObjNames and not materialNames:
            # Fallback: try to find by name directly (in case mapping wasn't built)
            if (obj := bpy.data.objects.get(channelName)) and obj.type == 'LIGHT':
                _changeLightProperties(obj, colorR, colorG, colorB, intensityMult, enabled)
                changedLights.add(obj.name)
            elif coll := bpy.data.collections.get(channelName):
                for obj in coll.all_objects:
                    if obj.type == 'LIGHT':
                        _changeLightProperties(obj, colorR, colorG, colorB, intensityMult, enabled)
                        changedLights.add(obj.name)
            else:
                debug.printWarning(f"Light Mix Transfer: no mapping found for '{channelName}'")

    # Apply "Rest" to all lights that were not explicitly changed
    if restChange:
        _, rR, rG, rB, rIntensity, rEnabled = restChange
        for obj in sceneLights:
            if obj.name not in changedLights:
                _changeLightProperties(obj, rR, rG, rB, rIntensity, rEnabled)

    # Apply "Self_Illumination" to all emissive materials not explicitly changed
    if selfIllumChange:
        _, sR, sG, sB, sIntensity, sEnabled = selfIllumChange
        for mat in bpy.data.materials:
            if mat.name not in changedMaterials and _isEmissiveMaterial(mat):
                _changeMaterialProperties(mat, sR, sG, sB, sIntensity, sEnabled)

    # Tag areas for redraw so the properties panels refresh.
    for area in bpy.context.screen.areas if bpy.context.screen else []:
        area.tag_redraw()

    debug.printInfo("Light Mix: Transfer to Scene applied")


def _extractChannelName(pluginName: str) -> str:
    """ Extract the channel name from a light select plugin name. """
    if pluginName.startswith(_LS_PREFIX):
        return pluginName[len(_LS_PREFIX):]
    return pluginName


def _changeLightProperties(obj: bpy.types.Object, colorR, colorG, colorB, intensityMult, enabled):
    """ Apply light mix changes to a single light object. """
    light = obj.data
    if not hasattr(light, 'vray'):
        return

    pluginType = getLightPluginType(light)
    if not pluginType:
        return

    propGroup = getLightPropGroup(light, pluginType)
    if not propGroup:
        return

    props = _LIGHT_PROPS.get(pluginType)
    if not props:
        debug.printWarning(f"Light Mix Transfer: unsupported light type '{pluginType}'")
        return

    colorProp, intensityProp = props

    # Apply color multiplier (skip if white / no change)
    isWhite = (math.isclose(colorR, 1.0) and math.isclose(colorG, 1.0) and math.isclose(colorB, 1.0))
    if not isWhite:
        oldColor = getattr(propGroup, colorProp)
        newColor = (oldColor[0] * colorR, oldColor[1] * colorG, oldColor[2] * colorB)
        setattr(propGroup, colorProp, newColor)

    # Apply intensity multiplier (skip if 1.0 / no change)
    if intensityMult == 0.0:
        # Zeroing the scene intensity is irreversible - any further transfer would
        # multiply by 0 again. Turn the light off and keep its intensity instead.
        enabled = False
    elif not math.isclose(intensityMult, 1.0):
        oldIntensity = getattr(propGroup, intensityProp)
        setattr(propGroup, intensityProp, oldIntensity * intensityMult)

    propGroup.enabled = enabled


def _isEmissiveMaterial(material: bpy.types.Material) -> bool:
    """ Check if a material has emissive properties.
        Matches the export-time check in BRDFVRayMtl.py and BRDFLight.py.
    """
    if not hasattr(material, 'vray') or not material.node_tree:
        return False

    for node in material.node_tree.nodes:
        if node.bl_idname == 'VRayNodeBRDFLight':
            return True
        if node.bl_idname == 'VRayNodeBRDFVRayMtl':
            if not all(math.isclose(c, 0.0) for c in node.BRDFVRayMtl.self_illumination[:3]):
                return True
        if node.bl_idname == 'VRayNodeBRDFToonMtl':
            if not all(math.isclose(c, 0.0) for c in node.BRDFToonMtl.self_illumination[:3]):
                return True

    return False


def _changeMaterialProperties(material: bpy.types.Material, colorR, colorG, colorB, intensityMult, enabled, nodeName: str = ""):
    """ Apply light mix changes to an emissive material.

        Args:
            nodeName: If set, only modify the node with this name (unique within the tree).
                      Used for individual material light selects. If empty, modify all emissive nodes.
    """
    if not material.node_tree:
        return

    isNoChange = (math.isclose(intensityMult, 1.0)
                  and math.isclose(colorR, 1.0) and math.isclose(colorG, 1.0) and math.isclose(colorB, 1.0))
    if isNoChange:
        return

    nodes = [material.node_tree.nodes.get(nodeName)] if nodeName else material.node_tree.nodes
    for node in nodes:
        if node is None:
            continue

        if node.bl_idname in ('VRayNodeBRDFVRayMtl', 'VRayNodeBRDFToonMtl'):
            propGroupName = 'BRDFVRayMtl' if node.bl_idname == 'VRayNodeBRDFVRayMtl' else 'BRDFToonMtl'
            propGroup = getattr(node, propGroupName)
            oldColor = propGroup.self_illumination
            propGroup.self_illumination = (
                oldColor[0] * intensityMult * colorR,
                oldColor[1] * intensityMult * colorG,
                oldColor[2] * intensityMult * colorB
            )

        elif node.bl_idname == 'VRayNodeBRDFLight':
            propGroup = node.BRDFLight
            oldColor = propGroup.color
            propGroup.color = (
                oldColor[0] * intensityMult * colorR,
                oldColor[1] * intensityMult * colorG,
                oldColor[2] * intensityMult * colorB
            )
