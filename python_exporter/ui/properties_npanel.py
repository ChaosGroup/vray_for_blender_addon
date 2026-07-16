# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" The V-Ray tab in the 3D Viewport's N-panel.

    Shows the most-used parameters of the currently active V-Ray object (lights, proxy,
    scene, fur, decal, gaussians). The property list of each object type is described by a
    dedicated "npanel" Widget section in the corresponding plugin's *.custom.json and is
    rendered with UIPainter.renderWidgetsSection, so it always matches the control types and
    logic of the Object Data tab.
"""

import bpy
import dataclasses

from vray_blender.lib import blender_utils, lib_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.plugins import getPluginModule
from vray_blender.nodes import utils as NodesUtils
from vray_blender.ui import classes, ui_operators
from vray_blender.ui.icons import getIcon


# Map a V-Ray light plugin type to its icon key in vray_blender.ui.icons._ICON_FILES.
_LIGHT_ICON = {
    'LightRectangle':  'LIGHT_RECT',
    'LightSphere':     'LIGHT_SPHERE',
    'LightDome':       'LIGHT_DOME',
    'LightMesh':       'LIGHT_MESH',
    'LightIES':        'LIGHT_IES',
    'SunLight':        'LIGHT_SUN',
    'LightSpot':       'LIGHT_SPOT',
    'LightOmni':       'LIGHT_OMNI',
    'MayaLightDirect': 'LIGHT_DIRECT',
    'LightAmbient':    'LIGHT_AMBIENT',
}


@dataclasses.dataclass
class NPanelDesc:
    """ Everything the N-panel and its action buttons need to render a V-Ray object. """
    iconKey:            str             # Key into vray_blender.ui.icons for the header icon
    pluginModuleName:   str             # Plugin type passed to getPluginModule()
    propGroup:          object          # The property group holding the parameters
    node:               object          # The node-tree output node, or None
    nodeTreeType:       str             # 'SHADER' for lights, 'OBJECT' for geometry objects,
                                        # '' for objects with no node tree (e.g. cameras, Gaussians)
    advancedPanelClass: str             # __name__ of the Object Data panel to expand, or ""


def _resolveLight(obj: bpy.types.Object):
    light = obj.data
    pluginType = lib_utils.getLightPluginType(light)

    node = None
    if NodesUtils.treeHasNodes(light.node_tree) and (outputNode := NodesUtils.getLightOutputNode(light.node_tree)):
        node = outputNode
        propGroup = getattr(outputNode, outputNode.vray_plugin)
        pluginType = outputNode.vray_plugin
    else:
        propGroup = getattr(light.vray, pluginType, None)

    if propGroup is None:
        return None

    return NPanelDesc(
        iconKey            = _LIGHT_ICON.get(pluginType, 'VRAY_PLACEHOLDER'),
        pluginModuleName   = pluginType,
        propGroup          = propGroup,
        node               = node,
        nodeTreeType       = 'SHADER',
        advancedPanelClass = "",
    )


def _resolveGeometry(obj: bpy.types.Object):
    vray = obj.vray

    if obj.type == 'MESH':
        assetType = vray.VRayAsset.assetType
        if assetType == blender_utils.VRAY_ASSET_TYPE["Proxy"]:
            return NPanelDesc('VRAY_PROXY', 'GeomMeshFile', obj.data.vray.GeomMeshFile,
                              None, 'OBJECT', 'VRAY_PT_VRayProxy')
        if assetType == blender_utils.VRAY_ASSET_TYPE["Scene"]:
            return NPanelDesc('VRAY_SCENE', 'VRayScene', obj.data.vray.VRayScene,
                              None, 'OBJECT', 'VRAY_PT_VRayScene')

    if obj.type == 'MESH' and vray.isVRayDecal:
        propGroup = obj.data.vray.VRayDecal
        node = None
        if NodesUtils.treeHasNodes(vray.ntree) and (outputNode := NodesUtils.getNodeByType(vray.ntree, 'VRayNodeDecalOutput')):
            node = outputNode
            propGroup = outputNode.VRayDecal
        return NPanelDesc('VRAY_DECAL', 'VRayDecal', propGroup, node, 'OBJECT', 'VRAY_PT_VRayDecal')

    if vray.isVRayFur:
        propGroup = obj.data.vray.GeomHair
        node = None
        if NodesUtils.treeHasNodes(vray.ntree) and (outputNode := NodesUtils.getOutputNode(vray.ntree)):
            node = outputNode
            propGroup = getattr(outputNode, outputNode.vray_plugin)
        return NPanelDesc('VRAY_FUR', 'GeomHair', propGroup, node, 'OBJECT', 'VRAY_PT_context_fur')

    if obj.type == 'EMPTY' and vray.isVRayGaussian:
        # A Gaussian splat is an Empty, which is a non-geometry type, so it has no object
        # node tree - nodeTreeType is left empty to hide the "Open in Node Editor" button.
        return NPanelDesc('VRAY_PLACEHOLDER', 'GeomGaussians', obj.vray.GeomGaussians,
                          None, '', 'VRAY_PT_VRayGaussians')

    return None


def _resolveCamera(obj: bpy.types.Object):
    # The camera N-panel is always shown for cameras. Its parameters live on the CameraPhysical
    # plugin and are greyed out while the physical camera is off (mirroring the Data tab).
    camera = obj.data

    return NPanelDesc(
        iconKey            = 'PHYSICAL_CAMERA',
        pluginModuleName   = 'CameraPhysical',
        propGroup          = camera.vray.CameraPhysical,
        node               = None,
        nodeTreeType       = '',  # A camera has no node tree, so the "Open in Node Editor" button is hidden
        advancedPanelClass = 'VRAY_PT_physical_camera',
    )


def resolveNPanelObject(obj: bpy.types.Object):
    """ Return an NPanelDesc for the object if it is a V-Ray object shown in the N-panel,
        otherwise None. The prop-group resolution mirrors the corresponding Object Data panel.
    """
    if obj is None:
        return None
    if obj.type == 'LIGHT':
        return _resolveLight(obj)
    if obj.type == 'CAMERA':
        return _resolveCamera(obj)
    return _resolveGeometry(obj)


class VRAY_PT_npanel(classes.VRayPanel):
    """ The "V-Ray" tab in the 3D Viewport sidebar (N-panel). """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'V-Ray'
    bl_label       = ""

    @classmethod
    def poll(cls, context):
        return classes.pollEngine(context) and resolveNPanelObject(context.object) is not None

    def draw_header(self, context):
        # The collapsible section header shows the V-Ray icon of the object + its Blender name.
        obj = context.object
        if (desc := resolveNPanelObject(obj)) is not None:
            self.layout.label(text=obj.name, icon_value=getIcon(desc.iconKey))

    def draw(self, context):
        obj = context.object
        desc = resolveNPanelObject(obj)
        if desc is None:
            return

        layout = self.layout

        # The special action buttons, right-aligned.
        ui_operators.drawNPanelButtons(layout, context, desc)

        layout.separator()

        pluginModule = getPluginModule(desc.pluginModuleName)
        painter = UIPainter(context, pluginModule, desc.propGroup, desc.node, showAnimDecorators=False)
        painter.renderWidgetsSection(layout, 'npanel')


def getRegClasses():
    return (
        VRAY_PT_npanel,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
