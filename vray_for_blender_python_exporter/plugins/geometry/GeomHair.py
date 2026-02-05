from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import ExporterContext


plugin_utils.loadPluginOnModule(globals(), __name__)

def getGeomHairPluginName(furObjName: str, meshObjName: str):
    return f"VRayFur@{furObjName}_{meshObjName}"

def collectGeomHairInfo(exporterCtx: ExporterContext):
    furObjects = [o for o in exporterCtx.sceneObjects if o.vray.isVRayFur]

    activeFurInfo, updatedFurInfo, _ = export_utils.collectConnectedMeshInfo(
        exporterCtx, furObjects, 'GeomHair', 'Mesh', 'vray.ntree', 'VRayNodeFurOutput')

    return activeFurInfo, updatedFurInfo
