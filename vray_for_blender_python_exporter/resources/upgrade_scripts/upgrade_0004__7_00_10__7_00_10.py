import bpy

from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender import debug
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene

def _upgradeUVWGenMayaPlace2d(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    # Cut off the last _tex part of the parameters.
    propName=ctx.currentPropName
    newPropName = propName[:-4]
    oldProps = getattr(oldNode, oldNode.vray_plugin)
    newProps = getattr(newNode, newNode.vray_plugin)
    setattr(newProps, newPropName, getattr(oldProps, propName))

    nodeTree: bpy.types.ShaderNodeTree = newNode.id_data
    for link in nodeTree.links:
        if link.to_socket.vray_attr == propName:
            debug.printWarning(f"UVW Mapping socket {propName} will be disconnected in {ctx.nodeTreeType}::{ctx.nodeTreeName}")
            nodeTree.links.remove(link)
            break

def _preCopyUVWMappingNode(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    # Switch to the selected mapping mode
    newNode.mapping_node_type = oldNode.mapping_node_type


UPGRADE_INFO = {
    "nodes": {
        "VRayNodeUVWMapping": {
            "attributes": {
                "repeat_u_tex": _upgradeUVWGenMayaPlace2d,
                "repeat_v_tex": _upgradeUVWGenMayaPlace2d,
                "offset_u_tex": _upgradeUVWGenMayaPlace2d,
                "offset_v_tex": _upgradeUVWGenMayaPlace2d,
                "rotate_uv_tex": _upgradeUVWGenMayaPlace2d,
            },
            "node_pre_copy": _preCopyUVWMappingNode
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)
