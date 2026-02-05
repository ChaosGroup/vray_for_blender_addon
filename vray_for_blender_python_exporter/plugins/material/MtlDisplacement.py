from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc
from vray_blender.engine.renderer_ipr_base import ExporterContext
from vray_blender.exporting.plugin_tracker import getObjTrackId

import bpy
from mathutils import Color


plugin_utils.loadPluginOnModule(globals(), __name__)


def checkForUpdatedMtlWithDisplacement(exporterCtx: ExporterContext):
    """ Check if the active material has a displacement node """
    from vray_blender.nodes.utils import getOutputNode, areNodesInterconnected
    context = exporterCtx.ctx
    obj = context.active_object
    if obj and obj.active_material and \
        context.scene.vray.ActiveNodeEditorType =='SHADER' and \
        any(isinstance(u.id, bpy.types.ShaderNodeTree) for u in exporterCtx.dg.updates):

        activeMtl = obj.active_material

        outputNode = getOutputNode(activeMtl.node_tree)
        for displacementNode in (n for n in activeMtl.node_tree.nodes if n.bl_idname == "VRayNodeMtlDisplacement"):
            if areNodesInterconnected(displacementNode, outputNode):
                exporterCtx.updatedMtlWithDisplacement = activeMtl.name
                return

        # Check if the material had a displacement node attached to it before the current update.
        mtlId = getObjTrackId(activeMtl)
        nodeTracker = exporterCtx.nodeTrackers['MTL']
        for nodeId in nodeTracker.getOwnedNodes(mtlId):
            for pluginName in nodeTracker.getNodePlugins(mtlId, nodeId):
                if "Displacement" in pluginName:
                    exporterCtx.updatedMtlWithDisplacement = activeMtl.name
                    return

def exportCustom(exporterCtx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup
    minBound = propGroup.min_bound_float
    maxBound = propGroup.max_bound_float

    pluginDesc.setAttribute("min_bound", Color((minBound, minBound, minBound)))
    pluginDesc.setAttribute("max_bound", Color((maxBound, maxBound, maxBound)))

    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)