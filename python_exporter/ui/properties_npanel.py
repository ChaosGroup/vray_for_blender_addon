# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" The V-Ray tab in the 3D Viewport's N-panel.

    Shows the most-used parameters of the currently active V-Ray object (lights, proxy,
    scene, fur, decal, gaussians). The property list of each object type is described by a
    dedicated "npanel" Widget section in the corresponding plugin's *.custom.json and is
    rendered with UIPainter.renderWidgetsSection, so it always matches the control types and
    logic of the Object Data tab.

    The same parameter block is shown in Blender's "Adjust Last Operation" box after a V-Ray
    object is added - see VRAY_PT_add_object_params.
"""

import bpy
import dataclasses

from vray_blender import features
from vray_blender.features import Feature
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
    'LightLuminaire':  'LIGHT_LUMINAIRE',
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

    if obj.type == 'EMPTY' and vray.isVRayGaussian and features.isEnabled(Feature.GAUSSIAN_SPLATS):
        # A Gaussian splat is an Empty, which is a non-geometry type, so it has no object
        # node tree - nodeTreeType is left empty to hide the "Open in Node Editor" button.
        return NPanelDesc('VRAY_PLACEHOLDER', 'GeomGaussians', obj.vray.GeomGaussians,
                          None, '', 'VRAY_PT_VRayGaussians')

    # The infinite plane and the perfect sphere are Empties too - same no-node-tree handling.
    if obj.type == 'EMPTY' and vray.isVRayInfinitePlane:
        return NPanelDesc('VRAY_PLACEHOLDER', 'GeomPlane', obj.vray.GeomPlane,
                          None, '', 'VRAY_PT_VRayInfinitePlane')

    if obj.type == 'EMPTY' and vray.isVRayPerfectSphere:
        return NPanelDesc('VRAY_PLACEHOLDER', 'GeomPerfectSphere', obj.vray.GeomPerfectSphere,
                          None, '', 'VRAY_PT_VRayPerfectSphere')

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


def drawNPanelParams(layout: bpy.types.UILayout, context: bpy.types.Context, obj: bpy.types.Object) -> bool:
    """ Draw the 'npanel' parameter block of a V-Ray object into any layout.

        Used by both the N-panel and VRAY_PT_add_object_params, so the two can never drift.

        @return False if the object is not one of the types the N-panel covers.
    """
    if (desc := resolveNPanelObject(obj)) is None:
        return False

    painter = UIPainter(context, getPluginModule(desc.pluginModuleName), desc.propGroup, desc.node,
                        showAnimDecorators=False)
    painter.renderWidgetsSection(layout, 'npanel')
    return True


class VRayAddObjectMixin:
    """ Mix-in for the "Add V-Ray <object>" operators. It records the object the operator created,
        which VRAY_PT_add_object_params then shows in Blender's "Adjust Last Operation" box.
    """

    # The name of the object created by the last execute(). Blender rebuilds the operator from its
    # RNA properties before the box is drawn, so an instance attribute set in execute() would not
    # survive - the name has to be an operator property. HIDDEN keeps it out of any auto-drawn
    # layout, SKIP_SAVE stops a new invocation from inheriting the previous object.
    added_object: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    def setAddedObject(self, obj: bpy.types.Object):
        self.added_object = obj.name if obj else ""

    def draw(self, context: bpy.types.Context):
        """ Draws nothing, deliberately - do not delete.

            Blender hides the whole "Adjust Last Operation" box unless WM_operator_ui_poll()
            passes, and that only accepts an operator which either defines this method or has a
            property that is not hidden. These operators have neither ('added_object' is hidden),
            so without it the box - and the parameters VRAY_PT_add_object_params draws inside it -
            would never appear.

            The parameters cannot be drawn from here: everything the redo panel draws goes into a
            UI block whose handler re-runs the operator on every edit, which recreates the object
            and discards the change. A panel of its own gets a clean block, hence the split.
        """


def _lastAddedObject(context: bpy.types.Context):
    """ The object created by the most recent V-Ray Add operator, or None if the last registered
        operator was not one of them - in which case the Add box is not showing our operator
        either, and the parameters must not be drawn.
    """
    operators = context.window_manager.operators
    if not operators:
        return None

    objName = getattr(operators[-1].properties, 'added_object', "")
    # The object may have been removed (undo) or renamed since it was recorded.
    return bpy.data.objects.get(objName) if objName else None


class VRAY_PT_add_object_params(bpy.types.Panel):
    """ The V-Ray parameters of a newly added object, shown in Blender's "Adjust Last Operation"
        box under the name of the Add operator.

        A panel of its own, rather than something the operator's own draw() renders, because each
        panel is drawn into its own UI block. The redo panel's block carries a handler that undoes
        and re-runs the operator whenever any of its widgets is edited, which would recreate the
        object and throw the edit away. A separate block is free of it, so these parameters edit
        the object directly, like the N-panel's.

        It is a sub-panel and not a sibling because Blender sizes a floating region from
        region->panels.last alone ("only single panel support at the moment", ED_region_panels_
        layout_ex). A sibling left the box one row tall, with everything else clipped; a child's
        height is folded into the parent's by panel_calculate_size_recursive, so the box grows to
        fit.

        The box itself is Blender's, and it only exists while an operator is repeatable - see
        VRayAddObjectMixin.draw for what keeps it alive.
    """
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'HUD'
    bl_parent_id   = 'OPERATOR_PT_redo'  # Blender's own "Adjust Last Operation" panel
    bl_label       = "V-Ray Parameters"

    @classmethod
    def poll(cls, context):
        return classes.pollEngine(context) and _lastAddedObject(context) is not None

    def draw(self, context):
        drawNPanelParams(self.layout, context, _lastAddedObject(context))


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

        drawNPanelParams(layout, context, obj)


def getRegClasses():
    return (
        VRAY_PT_npanel,
        VRAY_PT_add_object_params,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
