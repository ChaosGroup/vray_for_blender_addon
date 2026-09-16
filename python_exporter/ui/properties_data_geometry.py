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

def _isChaosScatterObject(obj):
    """ True for the carrier object of a Chaos Scatter setup - a PointCloud, or a vertices-only
        Mesh on Blender versions where a PointCloud cannot be sized from Python (pre-5.1). The
        'chaos_scatter' property group is registered by the separate Chaos Scatter addon; when
        that addon is disabled, no object can match.
    """
    cs = getattr(obj, 'chaos_scatter', None)
    return (cs is not None) and cs.is_scatter


def _hidePanels():
    for panel in bpy.types.Panel.__subclasses__():
        if getattr(panel, 'bl_context', None) == 'data':
            if not hasattr(panel, 'poll'):
                continue

            if panel in _originalPolls or panel in _vrayDataPanels:
                continue

            if panel.__module__.startswith('chaos_scatter'):
                # The Chaos Scatter addon owns these - they are the panels we keep.
                continue

            originalPoll = panel.poll
            _originalPolls[panel] = originalPoll

            def makePoll(orig, panelCls):
                def vrayPoll(cls, context):
                    obj = context.object
                    if obj and hasattr(obj, 'vray'):
                        if obj.vray.isVRayDecal:
                            return False
                        if blender_utils.isNonGeometryExportedAsGeometry(obj):
                            # Empty-backed V-Ray geometry (Gaussian splats, infinite plane,
                            # perfect sphere) has its own data panels, and the plugin owns the
                            # Empty's display type and size. Hide the default Empty data panels.
                            return False
                        if _isChaosScatterObject(obj):
                            # The carrier's own geometry is generated - editing its attributes,
                            # shape keys etc. is meaningless. Leave only the Chaos Scatter panels.
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


# Blender draws a data panel only when the active engine is listed in the panel's
# COMPAT_ENGINES. The stock panels for these object types name only Blender's own engines, so
# without adding V-Ray they all vanish as soon as V-Ray is the active engine (VBLD-2779).
# properties_data_curve (the legacy Curve) is deliberately absent - its panels declare no
# COMPAT_ENGINES at all, so they are never engine-gated and already show under V-Ray.
_COMPAT_PANEL_MODULES = (
    'properties_data_mesh',
    'properties_data_pointcloud',
    'properties_data_curves',
    'properties_data_speaker',
)


def _setCompatEngines(add: bool):
    import importlib

    for moduleName in _COMPAT_PANEL_MODULES:
        module = importlib.import_module(f'bl_ui.{moduleName}')
        for member in dir(module):
            subclass = getattr(module, member)
            try:
                for compatEngine in classes.VRayEngines:
                    if add:
                        subclass.COMPAT_ENGINES.add(compatEngine)
                    else:
                        subclass.COMPAT_ENGINES.remove(compatEngine)
            except:
                pass


def register():
    _setCompatEngines(True)

    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)
        _vrayDataPanels.add(regClass)

    _hidePanels()


def unregister():
    _restorePanels()

    _setCompatEngines(False)

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
