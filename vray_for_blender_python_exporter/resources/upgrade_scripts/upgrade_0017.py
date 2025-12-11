import bpy

from vray_blender.utils.upgrade_scene import upgradeScene, sceneNeedsUpgrade


def directLights() :
    for l in [l for l in bpy.data.lights if l.vray.light_type == 'DIRECT' and l.node_tree is None]:
        yield l
    
def run():
    # If light does not have a node tree, the shadow color has been stored in its shadowColor property.
    # The correct property to use is shadowColor_colortex
    for l in directLights():
        l.vray.MayaLightDirect.shadowColor_colortex = l.vray.MayaLightDirect.shadowColor

def check():
    return any([l for l in directLights()])
