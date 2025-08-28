from vray_blender.utils.upgrade_scene import upgradeScene, sceneNeedsUpgrade

UPGRADE_INFO = {
    "nodes": {
        "VRayNodeTexNormalBump": {
            "attributes": {
                "additional_bump_type": lambda ctx, oldNode, newNode: None
            }
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
