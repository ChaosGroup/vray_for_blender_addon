import bpy
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene, sceneNeedsUpgrade

def _upgradeTexSkySun(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    if sunLight := oldNode.TexSky.sun:
        # Find the first parent object. In case of linked lights, it makes no difference whcih 
        # one we pick.
        if obj := next((o for o in bpy.data.objects if o.data == sunLight), None):
            newNode.TexSky.sun = obj


# This dictionary contains information about all node types that will to be upgraded.
# For a description of the format, see utils/upgrade_scene.py => upgradeScene()
UPGRADE_INFO = {
    "nodes": {
        "VRayNodeTexSky": {
            "attributes": {
                "sun": _upgradeTexSkySun
            }
        }
    }
}


def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
