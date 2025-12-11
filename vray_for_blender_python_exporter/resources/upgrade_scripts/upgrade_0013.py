from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene, sceneNeedsUpgrade

def _upgradeBounds(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    # Min/Max bound were changed from colors to floats. For old scenes just used the intensity.
    attrName = 'min_bound' if ctx.currentPropName == 'min_bound_float' else 'max_bound'
    colorValue = getattr(oldNode.GeomDisplacedMesh, attrName)
    setattr(newNode.GeomDisplacedMesh, ctx.currentPropName, (colorValue[0] + colorValue[1] + colorValue[2]) / 3.0)

def _copyDisplacementTextureLink(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    # Transfer the displacement type manually so properly create the color/float socket on the node.
    newNode.GeomDisplacedMesh.type = oldNode.GeomDisplacedMesh.type

    # Old scenes had water level set to -1 by default, so for new scenes it should be enabled by default.
    oldNode.GeomDisplacedMesh.use_water_level = True

    # The socket was renamed from Displacement Tetxure to Texture and also the type was changed. For old
    # scenes the link has to be transfered manually.
    textureSocket = oldNode.inputs['Texture']
    if len(textureSocket.links) > 0:
        newTextureSocket = newNode.inputs["Displacement Texture"]
        ctx.nodeTree.links.new(textureSocket.links[0].from_socket, newTextureSocket)

        # Remove the old link to avoid warnings
        oldTree = oldNode.id_data
        oldTree.links.remove(textureSocket.links[0])

def _postCopySubdivNode(ctx: UpgradeContext, links: list, newNode: VRayNodeBase):
    # Old scenes don't have a use_globals option(it was always disabled). Now the parameter is exposed
    # with a default True, so for old scenes it's set to False.
    newNode.GeomStaticSmoothedMesh.use_globals = False

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeDisplacement': {
            'attributes': {
                'min_bound_float': _upgradeBounds,
                'max_bound_float': _upgradeBounds
            },
            'node_pre_copy': _copyDisplacementTextureLink
        },
        "VRayNodeObjectOutput": None,
        'VRayNodeGeomStaticSmoothedMesh': {
            'node_post_copy': _postCopySubdivNode
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
