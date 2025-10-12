from vray_blender.utils.upgrade_scene import upgradeScene, sceneNeedsUpgrade

UPGRADE_INFO = {
    "nodes": {
        "VRayNodeBRDFVRayMtl": None
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
