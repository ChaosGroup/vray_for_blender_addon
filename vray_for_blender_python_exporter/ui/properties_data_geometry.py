# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import blender_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.plugins import getPluginModule
from vray_blender.nodes.utils import getNodeByType, treeHasNodes
from vray_blender.ui import classes

class VRAY_PT_VRayProxy(classes.VRayGeomPanel):
    bl_label   = "V-Ray Proxy"
    vray_icon  = "VRAY_PROXY"


    @classmethod
    def poll(cls, context):
        return classes.VRayGeomPanel.poll(context) and \
                    (context.active_object.vray.VRayAsset.assetType == blender_utils.VRAY_ASSET_TYPE["Proxy"])

    def draw(self, context):
        layout = self.layout

        obj = context.active_object

        if obj.type != "MESH":
            return

        # Disabling the proxy panel in edit mode
        # as generating new preview mesh while in edit mode
        # can lead to unexpected results.
        classes.disableLayoutInEditMode(layout, context)

        geomMeshFile = obj.data.vray.GeomMeshFile

        gemMeshFileModule = getPluginModule('GeomMeshFile')
        painter = UIPainter(context, gemMeshFileModule, geomMeshFile)
        painter.renderPluginUI(layout)

        layout.separator()
        layout.operator("vray.proxy_to_mesh", text="Convert to Mesh", icon='MESH_DATA')


class VRAY_PT_VRayScene(classes.VRayGeomPanel):
    bl_label   = "V-Ray Scene"
    vray_icon  = "VRAY_SCENE"
    bl_options = {'DEFAULT_CLOSED'}


    @classmethod
    def poll(cls, context):
        return classes.VRayGeomPanel.poll(context) and \
                    (context.active_object.vray.VRayAsset.assetType == blender_utils.VRAY_ASSET_TYPE["Scene"])

    def draw(self, context):
        layout = self.layout

        obj = context.active_object

        if obj.type != "MESH":
            return

        vrayScene = obj.data.vray.VRayScene

        vraySceneModule = getPluginModule('VRayScene')
        painter = UIPainter(context, vraySceneModule, vrayScene)
        painter.renderPluginUI(layout)

class VRAY_PT_VRayDecal(classes.VRayGeomPanel):
    bl_label  = "V-Ray Decal"
    vray_icon = "VRAY_DECAL"

    @classmethod
    def poll(cls, context):
        return classes.VRayGeomPanel.poll(context) and context.object.vray.isVRayDecal

    def draw(self, context):
        obj = context.object

        if obj.type != "MESH":
            return

        propGroup = obj.data.vray.VRayDecal
        decalNode = None
        if treeHasNodes(obj.vray.ntree) and (outputNode := getNodeByType(obj.vray.ntree, 'VRayNodeDecalOutput')):
            decalNode = outputNode
            propGroup = decalNode.VRayDecal

        vrayDecalModule = getPluginModule('VRayDecal')
        painter = UIPainter(context, vrayDecalModule, propGroup, decalNode)
        painter.renderPluginUI(self.layout)

def getRegClasses():
    return (
        VRAY_PT_VRayScene,
        VRAY_PT_VRayProxy,
        VRAY_PT_VRayDecal
    )


# Hide default Blender data panels for V-Ray special object types (Proxy, Scene, Decal).
# These objects have their own dedicated panels and don't need the default mesh data
# panels (UV Maps, Vertex Colors, Shape Keys, etc.).
_originalPolls = {}
_vrayDataPanels = set()  # Our own panels that should not be hidden

def _hidePanels():
    for panel in bpy.types.Panel.__subclasses__():
        if getattr(panel, 'bl_context', None) == 'data':
            if not hasattr(panel, 'poll'):
                continue

            if panel in _originalPolls or panel in _vrayDataPanels:
                continue

            originalPoll = panel.poll
            _originalPolls[panel] = originalPoll

            def makePoll(orig, panelCls):
                def vrayPoll(cls, context):
                    obj = context.object
                    if obj and hasattr(obj, 'vray'):
                        if obj.vray.isVRayDecal:
                            return False
                        assetType = obj.vray.VRayAsset.assetType
                        if assetType == blender_utils.VRAY_ASSET_TYPE["Scene"]:
                            return False
                        if assetType == blender_utils.VRAY_ASSET_TYPE["Proxy"]:
                            # Allow the UV Maps panel for proxies so users can see channel names
                            return getattr(panelCls, 'bl_label', '') == "UV Maps"
                    return orig(context)
                return vrayPoll

            panel.poll = classmethod(makePoll(originalPoll, panel))


def _restorePanels():
    for panel, orig in _originalPolls.items():
        panel.poll = orig
    _originalPolls.clear()


def register():
    from bl_ui import properties_data_mesh
    for member in dir(properties_data_mesh):
        subclass = getattr(properties_data_mesh, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.add(compatEngine)
        except:
            pass
    del properties_data_mesh

    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)
        _vrayDataPanels.add(regClass)

    _hidePanels()


def unregister():
    _restorePanels()

    from bl_ui import properties_data_mesh
    for member in dir(properties_data_mesh):
        subclass = getattr(properties_data_mesh, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.remove(compatEngine)
        except:
            pass
    del properties_data_mesh

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
