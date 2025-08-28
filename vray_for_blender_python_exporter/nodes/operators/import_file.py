import os
import bpy
from pathlib import PurePath

from vray_blender.nodes import importing as NodesImport
from vray_blender.nodes import tools     as NodesTools
from vray_blender.nodes.tree_defaults import addMaterialNodeTree, createNodeTreeForLightObject, removeNonVRayNodes

from vray_blender.vray_tools.vrscene_parser import parseVrscene
from vray_blender.vray_tools.vrmat_parser     import parseVrmat


from vray_blender import debug
from vray_blender.vray_tools import vray_proxy
from vray_blender.lib import blender_utils, path_utils
from vray_blender.lib.lib_utils import getUUID, LightTypeToPlugin, LightBlenderToVrayPlugin
from pathlib import Path

def _createMaterial(mtlName):
    mtl = bpy.data.materials.new(mtlName)
    addMaterialNodeTree(mtl, addDefaultTree=False)

    # removing default Blender nodes
    removeNonVRayNodes(mtl.node_tree)

    return mtl

def _assignMaterialsToSlots(vrsceneDict, obj, mtlNameOverrides:dict[str, str]):
    if not obj:
        return

    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] != 'MtlMulti':
            continue
        if 'mtls_list' in pluginDesc['Attributes']:
            mtlsList =  pluginDesc['Attributes']['mtls_list']
            idsList =  pluginDesc['Attributes']['ids_list']

            # There could be case where for example:
            # ids_list=[1, 5, 3] and mtls_list=[mtl1, mtl5, mtl3]
            # where we want mtl1 on slot 1, mtl5 on slot 5 and mtl3 on slot 3.
            # For that the materials are first sorted based on their ids and then if there
            # is gap between slots (in the example above between slot 1 and 3) None is appended
            # to obj.data.materials, which adds empty slot between the used ones

            mtlsWithIds = [(int(idsList[i]), mtlNameOverrides.get(mtlsList[i], mtlsList[i])) for i in range(len(idsList))]
            mtlsWithIds.sort(key=lambda e: e[0])

            slotCounter = 0
            for id, mtl in mtlsWithIds:
                while slotCounter < id:
                    obj.data.materials.append(None)
                    slotCounter += 1
                obj.data.materials.append(bpy.data.materials[mtl])
                slotCounter += 1

            break


def importMaterials(filePath, baseMaterial: str, packageId: str, revisionId: str, objectForMatAssign: bpy.types.Object = None, locationsMap: dict[str, str] = None):
    debug.printInfo(f'Importing materials from "{filePath}"')

    vrsceneDict = {}

    if filePath.endswith(".vrscene"):
        vrsceneDict = parseVrscene(filePath)
    else:
        vrsceneDict = parseVrmat(filePath)

    # Fix any plugin params that need special handling, e.g. version upgrades etc.
    NodesImport.fixPluginParams(vrsceneDict, materialAssetsOnly=not objectForMatAssign)


    MaterialTypeFilter = {
        'STANDARD' : {
            'MtlSingleBRDF',
            'MtlVRmat',
            'MtlDoubleSided',
            'MtlGLSL',
            'MtlLayeredBRDF',
            'MtlDiffuse',
            'MtlBump',
            'Mtl2Sided',
        },
        'MULTI' : {
            'MtlMulti'
        },
        'WRAPPED' : {
            'MtlWrapper',
            'MtlWrapperMaya',
            'MayaMtlMatte',
            'MtlMaterialID',
            'MtlMayaRamp',
            'MtlObjBBox',
            'MtlOverride',
            'MtlRenderStats',
            'MtlRoundEdges',
            'MtlStreakFade',
        },
    }

    # Collect material names based on selected
    # base material type
    #
    materialNames = []
    mtlNameOverrides = {}       # Map of renamed materials originalName -> newName.
                                # Used to deal with duplicate material names in different assets

    for pluginDesc in vrsceneDict:
        pluginType  = pluginDesc['ID']
        pluginName  = pluginDesc['Name']

        if pluginType in MaterialTypeFilter[baseMaterial]:
            materialNames.append(pluginName)

    for mtlName in materialNames:
        debug.printInfo(f"Importing material: {mtlName}")

        pluginDesc = NodesImport.getPluginByName(vrsceneDict, mtlName)

        if mtlName in bpy.data.materials:
            # Rename the material if it already exists in the scene 
            mtlNewName = f"{mtlName}_{getUUID()}"
            mtlNameOverrides[mtlName] = mtlNewName
            mtlName = mtlNewName
            debug.printInfo(f"Material name already exists, changing to {mtlName}")

        mtl = _createMaterial(mtlName)
        mtl.vray.cosmos_package_id = packageId
        mtl.vray.cosmos_revision_id = revisionId

        ntree = mtl.node_tree
        importContext = NodesImport.ImportContext(ntree, vrsceneDict, locationsMap = locationsMap)
        outputNode = ntree.nodes.new('VRayNodeOutputMaterial')

        mtlNode = None
        matOutputKey = 'Material'
        if  pluginDesc['ID'] == 'MtlSingleBRDF':
            brdfName = pluginDesc['Attributes']['brdf']
            matPlugin = NodesImport.getPluginByName(vrsceneDict, brdfName)
            mtlNode = NodesImport.createNode(importContext, outputNode, matPlugin)

            if 'BRDF' in mtlNode.outputs:
                matOutputKey = 'BRDF'
        else:
            mtlNode = NodesImport.createNode(importContext, outputNode, pluginDesc)

        ntree.links.new(mtlNode.outputs[matOutputKey], outputNode.inputs['Material'])

        NodesTools.rearrangeTree(ntree, outputNode)
        NodesTools.deselectNodes(ntree)

        if not objectForMatAssign:
            debug.report('INFO', f"Cosmos material imported: {mtlName}")

    # The created materials are assigned to a the passed blender object
    # if objectForMatAssign!=None and there is MtlMulti in vrsceneDict
    _assignMaterialsToSlots(vrsceneDict, objectForMatAssign, mtlNameOverrides)

    return {'FINISHED'}


def _importLights(context: bpy.types.Context, parentObj, lightPath: str, packageId: str, revisionId: int, locationsMap: dict[str, str]):
    from vray_blender.lib import attribute_utils

    lightDict = parseVrmat(lightPath)

    for plgDesc in lightDict:
        if (pluginType := plgDesc['ID']) in LightTypeToPlugin.values(): # Checking for light plugins
            plgName = plgDesc['Name']
            plgAttrs = plgDesc['Attributes']
 
            blType = next((k for k, v in LightBlenderToVrayPlugin.items() if v == pluginType), 'POINT')
            vrayType = next((k for k, v in LightTypeToPlugin.items() if v == pluginType), 'POINT')

            lightData = bpy.data.lights.new(name=plgName , type=blType)
            lightData.vray.light_type = vrayType
            lightData.vray.cosmos_package_id = packageId
            lightData.vray.cosmos_revision_id = revisionId

            # Scale area light
            if lightData.type == 'AREA':
                for vrAttr, blAttr in (("u_size", "size"), ("v_size", "size_y")):
                    if vrAttr in plgAttrs:
                        setattr(lightData, blAttr, attribute_utils.scaleToSceneLengthUnit(plgAttrs[vrAttr], "centimeters") * 2)

            lightObj = bpy.data.objects.new(name=plgName, object_data=lightData)
            lightObj.location = context.scene.cursor.location

            context.collection.objects.link(lightObj)

            # Applying the transformation from the .vrmat file
            lightObj.matrix_world = lightObj.matrix_world @ attribute_utils.attrValueToMatrix(plgAttrs['transform'], True)

            localPos, _, _ = lightObj.matrix_local.decompose()  # Decomposing the matrix to get the position of the light in local space
            lightData.vray.initial_proxy_light_pos = localPos
            lightData.vray.initial_proxy_light_scale = parentObj.data.vray.GeomMeshFile.scale

            # Assigning parent to the light object.
            # During cosmos import the parent will be the object from the package's .vrmesh file.
            lightObj.parent = parentObj

            lightNtree = createNodeTreeForLightObject(lightData)

            # The transformation is applied directly to the object, no need  for transform node generation
            del plgAttrs['transform']
            importContext = NodesImport.ImportContext(lightNtree, lightDict, locationsMap = locationsMap)
            lightNode = NodesImport.createNode(importContext, None, plgDesc)

            NodesTools.rearrangeTree(lightNtree, lightNode)
            NodesTools.deselectNodes(lightNtree)


def importHDRI(texturePath: str, lightPath: str, packageId: str, revisionId: str, locationsMap: dict[str, str]):
    # Takes two '.vrmat' files (one for HDR texture and one for dome)
    # and creates dome light object that projects HDR map

    domeDict = parseVrmat(lightPath)
    textureDict = parseVrmat(texturePath)

    name = f'VRayDomeLight@{Path(lightPath).stem}'
    domeData = bpy.data.lights.new(name=name , type='POINT')
    domeData.vray.light_type = 'DOME'
    domeData.vray.cosmos_package_id = packageId
    domeData.vray.cosmos_revision_id = revisionId
    domeObj = bpy.data.objects.new(name=name, object_data=domeData)

    bpy.context.collection.objects.link(domeObj)
    blender_utils.selectObject(domeObj)

    ntree = createNodeTreeForLightObject(domeData)
    try:
        lightDesc = next((plgDesc for plgDesc in domeDict if plgDesc['ID'] == "LightDome"))
        texDesc = next((plgDesc for plgDesc in textureDict if plgDesc['ID'] == "TexBitmap"))
    except:
        debug.printError("HDRI import: either no 'LightDome' description in light file or 'TexBitmap' in texture file")
        return

    domeContext = NodesImport.ImportContext(ntree, domeDict, locationsMap=locationsMap)
    textureContext = NodesImport.ImportContext(ntree, textureDict, locationsMap=locationsMap)
    lightDomeNode = NodesImport.createNode(domeContext, None, lightDesc)
    textureNode = NodesImport.createNode(textureContext, None, texDesc)

    ntree.links.new(textureNode.outputs["Color"], lightDomeNode.inputs['Dome Color'])
    NodesTools.rearrangeTree(ntree, lightDomeNode)
    NodesTools.deselectNodes(ntree)


def _importVRayProxy(context, filePath, useRelativePath=False, scaleUnit=1.0):
    if not os.path.exists(filePath):
        return None, f"File not found: {filePath}"

    if (fileExt:= PurePath(filePath).suffix) not in ('.vrmesh', '.abc'):
        return None, f"File format {fileExt} is not supported by V-Ray Proxy"

    purePath = PurePath(filePath)

    # Add new mesh object
    name = f'VRayProxy@{purePath.stem}'

    # Create a new mesh object for the object preview
    previewMesh = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, previewMesh)
    ob.location = context.scene.cursor.location

    geomMeshFile = previewMesh.vray.GeomMeshFile

    # Store the scale at which the model was imported. If it's  preview has to be regenerated, this value will be used to scale it.
    # The scale will be reset to 1 if the path to the mesh file is changed by the user and the new model will always be imported
    # with scale 1.0 due to the fact that there is no scale information in the mesh file itself.
    geomMeshFile['scale'] = scaleUnit / context.scene.unit_settings.scale_length

    context.collection.objects.link(ob)
    vrayAsset = ob.vray.VRayAsset

    if err := vray_proxy.loadVRayProxyPreviewMesh(ob, filePath, 0, 0.0, 1.0, 0.0):
        return None, err

    vrayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Proxy"]
    geomMeshFile['file'] = bpy.path.relpath(filePath) if useRelativePath else filePath
    blender_utils.setShadowAttr(geomMeshFile, 'file', geomMeshFile.file)

    return ob, None


def importProxyFromMeshFile(context: bpy.types.Context, matPath: str, meshPath: str, packageId = '', revisionId = 0,
                 lightPath="", locationsMap: dict[str, str]=None, useRelPath=False, scaleUnit=1.0):
    """ Import a VRayProxy object from a .vrmesh or .abc file.
        This function will create a new scene object and load the preview mesh for it.

    Args:
        context (bpy.types.Context): 
        matPath (str): Path to a .vrmat file with the materials for the object
        meshPath (str): Absolute path to the .vrmesh file
        lightPath (str, optional): Path to a .vrmat file with the light propeties. Defaults to "".
        locationsMap (dict(str, str), optional): A map of the resources used by the proxy (textures, materials etc) to 
                                                fully resolved file paths for each resource. Defaults to None.
        useRelPath (bool, optional): The file paths are in Blender's relative notation. Defaults to False.
        scaleUnit (float, optional): Scale unit for the model in meters. Defaults to 1.0.

    Returns:
        tuple[object, str]: A tuple containing the created object (or None on failure) and an error message (empty string on success).
    """
    assert not path_utils.isRelativePath(meshPath)

    objProxy, err = _importVRayProxy(context, meshPath, useRelPath, scaleUnit)

    if err:
        return None, err

    assert objProxy is not None
    objProxy.data.vray.cosmos_package_id = packageId
    objProxy.data.vray.cosmos_revision_id = revisionId

    if os.path.exists(matPath):
        importMaterials(matPath, 'STANDARD', packageId, revisionId, objectForMatAssign = objProxy, locationsMap=locationsMap)

    if lightPath and os.path.exists(lightPath):
        _importLights(context, objProxy, lightPath, packageId, revisionId, locationsMap)

    # Make sure the main proxy object is selected. The selection might have been changed
    # if operators were executed during import which only work with the active object.
    blender_utils.selectObject(objProxy)

    return objProxy, ""
