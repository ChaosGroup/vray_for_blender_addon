import copy

from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene, sceneNeedsUpgrade, NodeLink, _createNodeLinks


def _postCopyBDRFLayeredNode(ctx: UpgradeContext, links: list[NodeLink], newNode: VRayNodeBase):
    
    newLinks: list[NodeLink] = []

    for l in [l for l in links if l.toNodeName == newNode.name]:
        if l.toSockName == 'BRDF 1':
            toSockName = 'Base Material'
        elif l.toSockName.startswith('BRDF'):
            index = int(l.toSockName.replace('BRDF ', ''))
            if index != 1:
                toSockName =f"Coat Material {index - 1}"
        elif l.toSockName.startswith('Weight'):
            index = int(l.toSockName.replace('Weight ', ''))
            if index != 1:
                toSockName =f"Blend Amount {index - 1}"
        else:
            continue

        newLink = copy.copy(l)
        newLink.toSockName = toSockName
        newLinks.append(newLink)

    if newLinks:
        _createNodeLinks(ctx, newLinks)

UPGRADE_INFO = {
    "nodes": {
        "VRayNodeBRDFLayered": {
            "node_post_copy": _postCopyBDRFLayeredNode
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
