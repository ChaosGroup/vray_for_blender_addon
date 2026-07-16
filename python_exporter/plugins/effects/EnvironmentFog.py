# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.exporting import node_export as commonNodesExport, light_export
from vray_blender.exporting.tools import *
from vray_blender.lib.defs import *
from vray_blender.lib.names import Names
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.exporting.light_export import getLightMeshInstanceNames
from vray_blender.nodes.specials.selector import resolveSelectorNode
from vray_blender.nodes.utils import getObjectsFromSelector, getNodeOfPropGroup

from vray_blender.bin import VRayBlenderLib as vray

plugin_utils.loadPluginOnModule(globals(), __name__)

def drawHeightProp(context, layout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)
    attrName = widgetAttr['name']

    row = layout.row()
    row.active = len(_getSelectedObjects(context, node, "gizmos", "gizmo_selector")) == 0
    row.prop(propGroup, attrName)

def _exportEnvFogMeshGizmoFromObject(nodeCtx: NodeContext, objectName: str):
    # Export EnvFogMeshGizmo plugin by getting geometry of object
    sceneObjects = nodeCtx.exporterCtx.dg.objects

    if objectName in sceneObjects:
        domainObj = sceneObjects[objectName]
        if domainObj.type == 'MESH':
            # Export empty plugin because the plugin could be created later
            domainObGeomName = Names.objectData(domainObj)
            vray.pluginCreate(nodeCtx.renderer, domainObGeomName, "GeomStaticMesh")
            
            fogMeshPluginName = Names.nextVirtualNode(nodeCtx, "EnvFogMeshGizmo")
            fogMesh = PluginDesc(fogMeshPluginName, "EnvFogMeshGizmo")
            
            fogMesh.setAttribute("transform", domainObj.matrix_world)                    
            fogMesh.setAttribute("geometry", AttrPlugin(domainObGeomName))
            
            # A 'Node' plugin should not be exported for gizmo objects. Track the gizmo object so that we could 
            # remove the Node plugin exported for it.
            nodeCtx.exporterCtx.objTrackers['GIZMO'].trackPlugin(getObjTrackId(domainObj), fogMeshPluginName)
            
            return commonNodesExport.exportPluginWithStats(nodeCtx, fogMesh)

    return None


def _getSelectedObjects(context, node, sockAttr, selectorAttr):
    sockGizmos = getInputSocketByAttr(node, sockAttr)
    
    if selectorNode := resolveSelectorNode(sockGizmos):
        return getObjectsFromSelector(selectorNode, context)
    else:
        return getattr(node.EnvironmentFog, selectorAttr).getSelectedItems(context, 'objects')


def _exportEnvFogMeshGizmosList(nodeCtx: NodeContext):
    node = nodeCtx.node
    context = nodeCtx.exporterCtx.ctx
    selectedObjects = _getSelectedObjects(context, node, "gizmos", "gizmo_selector")

    exportedGizmos = [_exportEnvFogMeshGizmoFromObject(nodeCtx, obj.name) for obj in selectedObjects]
    return [g for g in exportedGizmos if g is not None]


def _exportEnvFogMeshLightsList(nodeCtx: NodeContext):
    node = nodeCtx.node
    context = nodeCtx.exporterCtx.ctx
    selectedLightObjects = _getSelectedObjects(context, node, "lights", "light_selector")

    lights = []
    
    for obj in selectedLightObjects:
        lightPluginName = light_export.getPluginName(obj)
        
        if obj.data.vray.light_type == 'MESH':
            # Append the lights created for each gizmo object of the LightMesh.
            lights.extend([AttrPlugin(n) for n in getLightMeshInstanceNames(nodeCtx.exporterCtx, lightPluginName)])
        else:
            lights.append(AttrPlugin(lightPluginName))
    
    return lights


def exportTreeNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "EnvironmentFog")
    pluginDesc.vrayPropGroup = nodeCtx.node.EnvironmentFog

    pluginDesc.setAttribute("gizmos", _exportEnvFogMeshGizmosList(nodeCtx))
    pluginDesc.setAttribute("lights", _exportEnvFogMeshLightsList(nodeCtx))

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=['gizmos', 'lights'])
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)