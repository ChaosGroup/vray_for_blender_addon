
# This dictionary contains information about all node types that will to be upgraded.
# For a description of the format, see utils/upgrade_scene.py => upgradeScene()
UPGRADE_INFO = {
    "nodes": {
        "VRayNodeTexMix": None,
        "VRayNodeTexDirt": None
    }
}


def run():
    from vray_blender.utils.upgrade_scene import upgradeScene
    upgradeScene(UPGRADE_INFO)

def check():
    from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade
    return sceneNeedsUpgrade(UPGRADE_INFO)
    