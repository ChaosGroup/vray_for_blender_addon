from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene, sceneNeedsUpgrade
from vray_blender.resources.upgrade_scripts.upgrade_0001 import _copyPropGroup

def _copyBitmapTex(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    newNode.texture = oldNode.texture
    _copyPropGroup(oldNode, newNode, 'BitmapBuffer')
    # TexBitmap properties are copied by upgradeScene()


UPGRADE_INFO = {
    "nodes": {
        "VRayNodeMetaImageTexture": {
            "node_pre_copy": _copyBitmapTex
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
