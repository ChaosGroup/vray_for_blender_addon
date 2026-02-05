import bpy
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.utils import getPropGroupOfNode
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene, sceneNeedsUpgrade
from vray_blender import UPGRADE_NUMBER


def _upgradeCmToMeters(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    oldVal = getattr(getPropGroupOfNode(oldNode), ctx.currentPropName)
    setattr(getPropGroupOfNode(newNode), ctx.currentPropName, oldVal * 0.01)

def _upgradeObjSpaceBump(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    setattr(getPropGroupOfNode(newNode), ctx.currentPropName, False)

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeTexChecker': None,
        'VRayNodeTexMix': None,
        'VRayNodeTexFalloff': None,
        'VRayNodeTexGradRamp': None,
        'VRayNodeTexOutput': None,
        'VRayNodeBRDFAlSurface': None,
        'VRayNodeTexWood': None,
        'VRayNodeTexTiles': None,
        'VRayNodeTexRock': None,
        'VRayNodeTexMarble': None,
        'VRayNodeTexSwirl': None,
        'VRayNodeTexGranite': None,
        'VRayNodeTexGrid': None,
        'VRayNodeTexLeather': None,
        'VRayNodeTexCloth': None,
        'VRayNodeTexSpeckle': None,
        'VRayNodeTexBulge': None,
        'VRayNodeBRDFCarPaint2': {
            'attributes': {
                'flake_scale_triplanar': _upgradeCmToMeters
            },
        },
        'VRayNodeBRDFFlakes2': {
            'attributes': {
                'flake_scale_triplanar': _upgradeCmToMeters
            },
        },
        'VRayNodeBRDFHair4': None,
        'VRayNodeBRDFSSS2Complex': {
            'attributes': {
                'scatter_radius_mult': _upgradeCmToMeters
            }
        },
        'VRayNodeTexNormalBump': {
            'attributes': {
                'bump_object_space': _upgradeObjSpaceBump
            }
        },
        'VRayNodeBRDFBump': {
            'attributes': {
                'bump_object_space': _upgradeObjSpaceBump
            }
        }
    }
}

if UPGRADE_NUMBER >= 31:
    del UPGRADE_INFO['nodes']['VRayNodeTexGradRamp']

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)