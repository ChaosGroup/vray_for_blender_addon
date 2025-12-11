from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.utils.upgrade_scene import  UpgradeContext, upgradeScene, sceneNeedsUpgrade
from vray_blender.nodes.sockets import addInput

def texMultiPreCopy(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    oldTextureInputNames = [input.name for input in oldNode.inputs if 'Texture ' in input.name]
    newTextureInputNames = [input.name for input in newNode.inputs if 'Texture ' in input.name]
    newTextureInputs = [input for input in newNode.inputs if 'Texture ' in input.name]

    for newTexture in newTextureInputs:
        if newTexture.name not in oldTextureInputNames:
            newNode.inputs.remove(newTexture)

    for sockName in oldTextureInputNames:
        if sockName not in newTextureInputNames:
            addInput(newNode, 'VRaySocketTexMulti', sockName)

# The defaults of random_mode_render_id was changed to false and the default of
# random_mode_scene_name was changed to true so to preserve old scenes they should
# be set manually due to the way Blender saves its scenes.
UPGRADE_INFO = {
    'nodes': {
        'VRayNodeTexMulti': {
            'attributes': {
                'random_mode_render_id': lambda ctx, oldNode, newNode: setattr(
                    newNode.TexMulti,
                    "random_mode_render_id",
                    'random_mode_render_id' not in oldNode.TexMulti.keys() or oldNode.TexMulti.random_mode_render_id
                ),
                'random_mode_scene_name': lambda ctx, oldNode, newNode: setattr(
                    newNode.TexMulti,
                    "random_mode_scene_name",
                    'random_mode_scene_name' in oldNode.TexMulti.keys() and oldNode.TexMulti.random_mode_scene_name
                )
            },
            'node_pre_copy': texMultiPreCopy
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
