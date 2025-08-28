from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene, sceneNeedsUpgrade
from vray_blender.lib.mixin import VRayNodeBase


# The Vector properties of the previous versions of the transform nodes have been removed.
# When updating, retrieve their values (if they are set) from the old node’s bpy_struct.
def _getVectorPropFromBpyStruct(oldNode: VRayNodeBase, key: str, defaultValue=(0.0, 0.0, 0.0)):
     try:
          return oldNode[key]
     except KeyError:
          return defaultValue

def _preCopyVRayNodeTransform(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    newNode.invert = oldNode.invert
    newNode.inputs["Rotation"].value = _getVectorPropFromBpyStruct(oldNode, "rotate")
    newNode.inputs["Offset"].value = _getVectorPropFromBpyStruct(oldNode, "offset")
    newNode.inputs["Scale"].value = _getVectorPropFromBpyStruct(oldNode, "scale", (1.0, 1.0, 1.0))

def _preCopyVRayNodeMatrix(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    newNode.invert = oldNode.invert
    newNode.inputs["Rotation"].value = _getVectorPropFromBpyStruct(oldNode, "rotate")
    newNode.inputs["Scale"].value = _getVectorPropFromBpyStruct(oldNode, "scale", (1.0, 1.0, 1.0))

def _preCopyVRayNodeVector(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
        newNode.value = _getVectorPropFromBpyStruct(oldNode, "vector")

UPGRADE_INFO = {
    "nodes": {
        "VRayNodeTransform": {"node_pre_copy": _preCopyVRayNodeTransform}, 
        "VRayNodeMatrix":    {"node_pre_copy": _preCopyVRayNodeMatrix},
        "VRayNodeVector":    {"node_pre_copy": _preCopyVRayNodeVector},
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
