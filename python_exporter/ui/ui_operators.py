# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Operators backing the buttons shown in the header of the V-Ray property pages
    (Material and Light) and in the V-Ray N-panel: quick navigation to the node tree,
    reset-to-defaults and jumping to the full settings in the Object Data tab.
"""

import bpy
import time

from vray_blender.lib import lib_utils
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.attribute_utils import resetPropGroupToDefaults
from vray_blender.nodes.group.utils import VRAY_EDITOR_TREE_TYPES
from vray_blender.nodes.operators.wrangler.settings import resetNode
from vray_blender.nodes.utils import getLightOutputNode, getPluginTypeOfNode, treeHasNodes
from vray_blender.plugins import PLUGINS, getPluginModule


# V-Ray plugin attributes that reference an external file or another scene object. The
# 'Reset to Defaults' button preserves these so the user does not lose the asset link / object
# reference on reset (re-picking a file or object would be tedious). Keyed by V-Ray plugin type.
# Add an entry here to preserve the reference of another plugin.
_RESET_PRESERVED_REFS = {
    'LightIES':      {'ies_file'},                 # path to the .ies profile
    'GeomGaussians': {'file'},                     # path to the .ply Gaussian splat file
    'GeomMeshFile':  {'file'},                     # path to the .vrmesh / .abc proxy file
    'VRayScene':     {'filepath'},                 # path to the .vrscene file
    'GeomHair':      {'mesh', 'object_selector'},  # base object(s) the fur grows on
    'LightMesh':     {'geometry', 'object_selector'},  # referenced geometry object(s)
}


def _vrayNodeEditorOpen(context):
    """ Return True if any open area is a V-Ray Node Editor. """
    return any(
        area.type == 'NODE_EDITOR'
        and area.spaces.active is not None
        and area.spaces.active.tree_type in VRAY_EDITOR_TREE_TYPES
        for area in context.screen.areas
    )


def _redrawNodeEditors(context):
    for area in context.screen.areas:
        if area.type == 'NODE_EDITOR':
            area.tag_redraw()


def _isPropertiesEditorOpen(context):
    """ Return True if any open area is a Properties editor to navigate to. """
    return any(area.type == 'PROPERTIES' for area in context.screen.areas)


class VRAY_OT_goto_node_tree(VRayOperatorBase):
    bl_idname  = "vray.goto_node_tree"
    bl_label   = "Open Node Tree"
    bl_options = {'INTERNAL'}

    tree_type: bpy.props.EnumProperty(
        items = (
            ('SHADER', "Shader", "Material / Light tree"),
            ('OBJECT', "Object", "Object geometry tree"),
        ),
        default = 'SHADER'
    )

    @classmethod
    def description(cls, context, properties):
        if not _vrayNodeEditorOpen(context):
            return "No V-Ray Node editor is currently open"
        if properties.tree_type == 'OBJECT':
            return "Open Object node tree"
        if getattr(context, 'light', None):
            return "Open Light node tree"
        return "Open Shader node tree"

    def execute(self, context):
        if not _vrayNodeEditorOpen(context):
            self.report({'INFO'}, "Open V-Ray Node editor to view the node tree")
            return {'CANCELLED'}

        context.scene.vray.ActiveNodeEditorType = self.tree_type
        _redrawNodeEditors(context)
        return {'FINISHED'}


class VRAY_OT_reset_property_page(VRayOperatorBase):
    bl_idname  = "vray.reset_property_page"
    bl_label   = "Reset Parameters"
    bl_options = {'INTERNAL', 'UNDO'}

    mode: bpy.props.EnumProperty(
        items = (
            ('MATERIAL', "Material", ""),
            ('LIGHT',    "Light",    ""),
            ('OBJECT',   "Object",   ""),
        ),
        default = 'MATERIAL'
    )

    @classmethod
    def description(cls, context, properties):
        name = None
        if properties.mode == 'MATERIAL' and getattr(context, 'material', None):
            name = context.material.name
        elif properties.mode == 'LIGHT' and getattr(context, 'light', None):
            name = context.light.name
        elif properties.mode == 'OBJECT' and getattr(context, 'object', None):
            name = context.object.name
        return f"Reset {name} parameters to defaults" if name else "Reset parameters to defaults"

    def execute(self, context):
        if self.mode == 'MATERIAL':
            return self._resetMaterial(context)
        if self.mode == 'LIGHT':
            return self._resetLight(context)
        return self._resetObject(context)

    def _resetMaterial(self, context):
        # Local import to avoid a circular import with properties_material (which imports this module).
        from vray_blender.ui.properties_material import getMaterialPanelNode

        mtl = context.material
        if not (mtl and mtl.vray.is_vray_class):
            return {'CANCELLED'}

        node = getMaterialPanelNode(context, mtl)
        if not (node and (plugin := getPluginTypeOfNode(node)) and (module := getPluginModule(plugin))):
            return {'CANCELLED'}

        resetNode(node, module)
        mtl.node_tree.update_tag()
        self.report({'INFO'}, f"Reset {mtl.name} parameters to defaults.")
        return {'FINISHED'}

    def _resetLight(self, context):
        # context.light is only populated in the Properties editor (accessing it elsewhere
        # raises AttributeError). When invoked from the V-Ray N-panel (3D Viewport) fall back
        # to the active object's light data.
        light = getattr(context, 'light', None)
        if not light and (obj := getattr(context, 'object', None)) and obj.type == 'LIGHT':
            light = obj.data
        if not light:
            return {'CANCELLED'}

        lightPluginType = lib_utils.getLightPluginType(light)

        if treeHasNodes(light.node_tree) and (outputNode := getLightOutputNode(light.node_tree)):
            pluginType = getPluginTypeOfNode(outputNode)
            if module := getPluginModule(pluginType):
                resetNode(outputNode, module, skipAttrs=_RESET_PRESERVED_REFS.get(pluginType))
                light.node_tree.update_tag()
        else:
            resetPropGroupToDefaults(getattr(light.vray, lightPluginType), PLUGINS['LIGHT'][lightPluginType],
                                     skipAttrs=_RESET_PRESERVED_REFS.get(lightPluginType))

        # The Include/Exclude list is stored on the VRayLight propGroup, not the light plugin, so
        # reset it explicitly.
        light.vray.objectList.resetToDefaults()

        self.report({'INFO'}, f"Reset {light.name} parameters to defaults.")
        return {'FINISHED'}

    def _resetObject(self, context):
        # Reset the V-Ray parameters of a geometry/empty object shown in the V-Ray N-panel
        # (Proxy, Scene, Fur, Decal, Gaussians). The object type is resolved exactly as the
        # N-panel resolves it, so node-tree-backed objects reset the active output node.
        # Local import to avoid a circular import with properties_npanel (which imports this module).
        from vray_blender.ui.properties_npanel import resolveNPanelObject

        obj = context.object
        desc = resolveNPanelObject(obj)
        if desc is None:
            return {'CANCELLED'}

        if desc.node is not None:
            pluginType = getPluginTypeOfNode(desc.node)
            if module := getPluginModule(pluginType):
                resetNode(desc.node, module, skipAttrs=_RESET_PRESERVED_REFS.get(pluginType))
                if obj.vray.ntree:
                    obj.vray.ntree.update_tag()
        else:
            skipAttrs = set(_RESET_PRESERVED_REFS.get(desc.pluginModuleName, ()))
            # For a camera, skip 'use' so the reset does not desync the physical-camera toggle
            # (it mirrors VRayCamera.use_physical, which lives on a different prop group).
            if obj.type == 'CAMERA':
                skipAttrs.add('use')
            resetPropGroupToDefaults(desc.propGroup, getPluginModule(desc.pluginModuleName), skipAttrs=skipAttrs)

        self.report({'INFO'}, f"Reset {obj.name} parameters to defaults.")
        return {'FINISHED'}


class VRAY_OT_npanel_show_advanced_settings(VRayOperatorBase):
    """ Switch any open Properties editor to the Object Data tab and expand the V-Ray
        panel for the active object so all of its settings become visible.
    """
    bl_idname      = "vray.npanel_show_advanced_settings"
    bl_label       = "Advanced Settings"
    bl_description = "Show all settings of this object in the Properties editor's Object Data tab"
    bl_options     = {'INTERNAL'}

    # __name__ of the Object Data Panel class to force-open (empty to only switch the tab).
    panel_class: bpy.props.StringProperty(default="", options={'HIDDEN'})

    _forceOpenPending: set = set()

    @staticmethod
    def _forcePanelOpenTick():
        """ Force-open the queued panels on the next draw cycle. Blender's Python API does not
            expose a way to programmatically expand a collapsed bpy.types.Panel, so we unregister
            and re-register the panel with a new bl_idname and without the DEFAULT_CLOSED option.
            This mirrors the workaround used by operators.VRAY_OT_jump_to_setting.
        """
        pending = list(VRAY_OT_npanel_show_advanced_settings._forceOpenPending)
        VRAY_OT_npanel_show_advanced_settings._forceOpenPending.clear()

        for panelClass in pending:
            try:
                bpy.utils.unregister_class(panelClass)
                panelClass.bl_options = set(getattr(panelClass, 'bl_options', set())) - {'DEFAULT_CLOSED'}
                panelClass.bl_idname = panelClass.__name__ + str(time.time_ns())
                bpy.utils.register_class(panelClass)
            except Exception:
                pass

        return None

    @staticmethod
    def _findPanelClass(className: str):
        if not className:
            return None
        from vray_blender.ui import (properties_data_geometry, properties_data_fur,
                                      properties_data_empty, properties_data_camera)
        for mod in (properties_data_geometry, properties_data_fur, properties_data_empty, properties_data_camera):
            cls = getattr(mod, className, None)
            if isinstance(cls, type):
                return cls
        return None

    def _queueForcePanelOpen(self, panelClass):
        if panelClass is None:
            return
        self._forceOpenPending.add(panelClass)
        if not bpy.app.timers.is_registered(self._forcePanelOpenTick):
            bpy.app.timers.register(self._forcePanelOpenTick, first_interval=0)

    def execute(self, context):
        # Switch every open Properties editor to the Object Data tab.
        for area in context.screen.areas:
            if area.type == 'PROPERTIES' and area.spaces.active is not None:
                try:
                    area.spaces.active.context = 'DATA'
                    area.tag_redraw()
                except TypeError:
                    # 'DATA' is not a valid context for this object - ignore.
                    pass

        # Expand the V-Ray Object Data panel so the full settings are visible.
        self._queueForcePanelOpen(self._findPanelClass(self.panel_class))
        return {'FINISHED'}


def drawPropertyPageButtons(layout: bpy.types.UILayout, context, mode: str):
    """ Draw the right-aligned 'Node Tree' + reset buttons for a property page header.

        @param context - current Blender context (used to enable/disable the navigation button)
        @param mode - 'MATERIAL' or 'LIGHT'
    """
    row = layout.row(align=True)
    row.alignment = 'RIGHT'

    # The navigation button is only useful when there is a V-Ray Node editor open to show
    # the tree in. Disable it otherwise (the tooltip explains why).
    navRow = row.row(align=True)
    navRow.enabled = _vrayNodeEditorOpen(context)
    op = navRow.operator("vray.goto_node_tree", text="Node Tree", icon='NODETREE')
    op.tree_type = 'SHADER'
    row.separator()

    op = row.operator("vray.reset_property_page", text="", icon='FILE_REFRESH')
    op.mode = mode


def drawNPanelButtons(layout: bpy.types.UILayout, context, desc):
    """ Draw the three right-aligned, icon-only action buttons of the V-Ray N-panel:
        Advanced Settings, Open in Node Editor and Reset to Defaults.

        @param desc - the NPanelDesc returned by properties_npanel.resolveNPanelObject
    """
    row = layout.row(align=True)
    row.alignment = 'RIGHT'

    # The Advanced Settings button navigates to a Properties editor's Object Data tab, so it is
    # only useful when there is a Properties editor open. Disable it otherwise.
    advRow = row.row(align=True)
    advRow.enabled = _isPropertiesEditorOpen(context)
    op = advRow.operator("vray.npanel_show_advanced_settings", text="", icon='PRESET')
    op.panel_class = desc.advancedPanelClass

    # The navigation button is only shown for objects that have a node tree (e.g. not for
    # cameras or Gaussian splats). It is only useful when there is a V-Ray Node editor open
    # to show the tree in, so disable it otherwise (the tooltip explains why).
    if desc.nodeTreeType:
        navRow = row.row(align=True)
        navRow.enabled = _vrayNodeEditorOpen(context)
        op = navRow.operator("vray.goto_node_tree", text="", icon='NODETREE')
        op.tree_type = desc.nodeTreeType

    op = row.operator("vray.reset_property_page", text="", icon='FILE_REFRESH')
    op.mode = 'LIGHT' if context.object and context.object.type == 'LIGHT' else 'OBJECT'


def getRegClasses():
    return (
        VRAY_OT_goto_node_tree,
        VRAY_OT_reset_property_page,
        VRAY_OT_npanel_show_advanced_settings,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
