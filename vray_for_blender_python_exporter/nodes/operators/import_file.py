# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import math
import os
import bpy
from pathlib import PurePath

from vray_blender.nodes import importing as NodesImport
from vray_blender.nodes import tools     as NodesTools
from vray_blender.nodes.utils import getOutputNode, getLightOutputNode
from vray_blender.nodes.tree_defaults import addMaterialNodeTree, addDecalNodeTree, createNodeTreeForLightObject, removeNonVRayNodes

from vray_blender.vray_tools.vrscene_parser import parseVrscene
from vray_blender.vray_tools.vrmat_parser     import parseVrmat

from vray_blender import debug
from vray_blender.vray_tools import vray_proxy
from vray_blender.lib import blender_utils, path_utils
from vray_blender.lib.lib_utils import getUUID, LightTypeToPlugin, LightBlenderToVrayPlugin
from vray_blender.lib.path_utils import tryGetRelativePath
from vray_blender.plugins.geometry.VRayDecal import createDecalObject, generateDecalPreviewMesh
from vray_blender.bin.VRayBlenderLib import CosmosAssetSettings
from pathlib import Path

def _createMaterial(mtlName):
    mtl = bpy.data.materials.new(mtlName)
    addMaterialNodeTree(mtl, addDefaultTree=False)

    # removing default Blender nodes
    removeNonVRayNodes(mtl.node_tree)

    return mtl

def _assignMaterialsToSlots(multiMtlDesc, obj, mtlNameOverrides:dict[str, str]):
    assert obj is not None
    assert 'mtls_list' in multiMtlDesc['Attributes']

    mtlsList =  multiMtlDesc['Attributes']['mtls_list']
    idsList =  multiMtlDesc['Attributes']['ids_list']

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


def _createEmptyMaterialSlotsFromProxy(obj: bpy.types.Object, shaders: list):
    """ Create empty material slots on a proxy object based on the shader info from the proxy file.
        Slot ordering matches shader IDs so that face material indices align correctly.

    Args:
        obj: The proxy object to add slots to.
        shaders: List of shader descriptors [{'name': str, 'id': int}, ...].
    """
    if not shaders:
        return

    # Sort by shader ID so slot indices match material ID assignments
    sortedShaders = sorted(shaders, key=lambda s: s['id'])

    slotCounter = 0
    for shader in sortedShaders:
        # Fill gaps with empty slots (same pattern as _assignMaterialsToSlots)
        while slotCounter < shader['id']:
            obj.data.materials.append(None)
            slotCounter += 1

        # Create a named empty slot
        obj.data.materials.append(None)
        slotCounter += 1


def _isTexturePlugin(pluginType: str) -> bool:
    """ True if a V-Ray plugin type produces a texture (a color/value sample). """
    from vray_blender.plugins import getPluginModule
    try:
        return getPluginModule(pluginType).TYPE == 'TEXTURE'
    except Exception:
        return False


def _applyRealWorldScaleToUVWGens(vrsceneDict: list, widthCm: float, heightCm: float):
    """ Right-multiply every UVWGenChannel/UVWGenObject's uvw_transform by diag(1/w, 1/h, 1).

    Used in combination with a TexTriPlanar wrap (size=1.0) to handle the case with non-uniform
    real-world tile dimensions in some Cosmos assets.

    Transform encoding in vrsceneDict matches the .vrmat parser output:
        ((v0, v1, v2), translation)
    where v0/v1/v2 are the matrix's column basis vectors (image of X/Y/Z axes). Right-
    multiplying by diag(sU, sV, 1) scales those columns by (sU, sV, 1) respectively.
    """
    scaleU = 1.0 / widthCm if widthCm > 0.0 else 1.0
    scaleV = 1.0 / heightCm if heightCm > 0.0 else 1.0
    if scaleU == 1.0 and scaleV == 1.0:
        return

    identityMatrix = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    zeroTranslation = (0.0, 0.0, 0.0)

    for plugin in vrsceneDict:
        if plugin.get('ID') not in ('UVWGenChannel', 'UVWGenObject'):
            continue

        attrs = plugin.setdefault('Attributes', {})
        existing = attrs.get('uvw_transform')

        if existing is None:
            matrix, translation = identityMatrix, zeroTranslation
        else:
            matrix, translation = existing

        v0, v1, v2 = matrix
        attrs['uvw_transform'] = (
            (
                (v0[0] * scaleU, v0[1] * scaleU, v0[2] * scaleU),
                (v1[0] * scaleV, v1[1] * scaleV, v1[2] * scaleV),
                v2,
            ),
            translation,
        )


def _wrapTexturesInTriPlanar(vrsceneDict: list, rootPluginNames: list[str], size: float):
    """ BFS the material plugin graph and wrap every top-level texture in a TexTriPlanar.

    Descends through non-texture plugins (BRDFs, materials, UVW generators, etc.) and, when it
    reaches a texture-producing plugin, replaces the reference with a freshly created TexTriPlanar
    whose `texture_x` points at the original texture. Wrapped textures are not descended into so
    nested compositing stays intact. Already-TexTriPlanar plugins are left alone to avoid
    double-wrapping.
    """
    nameToDesc = {p['Name']: p for p in vrsceneDict}
    triplanarCounter = [0]
    visited: set[str] = set()
    queue: list[str] = [n for n in rootPluginNames if n in nameToDesc]

    def wrapReference(value: str) -> str:
        # value is "pluginName" or "pluginName::output". Replace with a new TexTriPlanar that
        # references the original texture and return the new reference string, preserving the
        # caller's requested output socket so downstream connections keep their semantics
        # (e.g. ::out_intensity for a float input).
        pluginName, sep, outSuffix = value.partition("::")
        refDesc = nameToDesc.get(pluginName)
        if refDesc is None:
            return value
        if refDesc['ID'] == 'TexTriPlanar':
            return value
        if not _isTexturePlugin(refDesc['ID']):
            # Non-texture reference: descend into it during BFS, leave the reference alone.
            if pluginName not in visited:
                queue.append(pluginName)
            return value

        triplanarCounter[0] += 1
        triplanarName = f"{pluginName}@TriPlanar{triplanarCounter[0]}"
        triplanarDesc = {
            'ID': 'TexTriPlanar',
            'Name': triplanarName,
            'Attributes': {
                'texture_x': pluginName,
                'size': size,
                'blend': 0.1,
                'blend_method': 1,
            },
        }
        vrsceneDict.append(triplanarDesc)
        nameToDesc[triplanarName] = triplanarDesc
        return f"{triplanarName}{sep}{outSuffix}" if sep else triplanarName

    while queue:
        pluginName = queue.pop(0)
        if pluginName in visited:
            continue
        visited.add(pluginName)

        pluginDesc = nameToDesc.get(pluginName)
        if pluginDesc is None:
            continue

        attrs = pluginDesc.get('Attributes', {})
        for attrName, attrValue in list(attrs.items()):
            if isinstance(attrValue, str) and attrValue and attrValue.partition("::")[0] in nameToDesc:
                attrs[attrName] = wrapReference(attrValue)
            elif isinstance(attrValue, list) and attrValue and all(
                isinstance(item, str) and item.partition("::")[0] in nameToDesc for item in attrValue
            ):
                attrs[attrName] = [wrapReference(item) for item in attrValue]


def importMaterials(filePath, objectForMatAssign: bpy.types.Object = None,
                   locationsMap: dict[str, str] = None, forceDefaultUVChannel: bool = False,
                   cosmosAssetContext: CosmosAssetSettings | None = None):
    """ Import V-Ray materials from a .vrmat/.vrscene file. """
    debug.printInfo(f'Importing materials from "{filePath}"')

    vrsceneDict = {}

    if filePath.endswith(".vrscene"):
        vrsceneDict = parseVrscene(filePath)
    else:
        vrsceneDict = parseVrmat(filePath)

    # Fix any plugin params that need special handling, e.g. version upgrades etc.
    NodesImport.fixPluginParams(vrsceneDict, forceDefaultUVChannel=forceDefaultUVChannel)

    # A list of all top-level V-Ray materials.
    MaterialTypeFilter = {
        'MtlSingleBRDF',
        'MtlVRmat',
        'MtlDoubleSided',
        'MtlGLSL',
        'MtlLayeredBRDF',
        'MtlDiffuse',
        'MtlBump',
        'Mtl2Sided',
    }

    # Collect the names of all material-type plugins, then keep only the top-level ones.
    #
    # A material plugin is "top-level" only if no other material plugin references it.
    # Nested materials (e.g. Mtl2Sided.front, or a material used as MtlSingleBRDF.brdf)
    # are imported recursively as nodes of their parent, so importing them again as
    # standalone materials would create spurious duplicates. This happens with assets
    # whose hierarchy is e.g. MtlSingleBRDF -> Mtl2Sided -> MtlSingleBRDF.
    #
    # Only references made by plugins in MaterialTypeFilter count as "consuming" a
    # material. MtlMulti is intentionally not in the filter, so its sub-material
    # references do NOT mark them as nested - each MtlMulti sub-material stays
    # top-level and is imported separately (as the slot-assignment logic expects).
    mtlNameOverrides = {}       # Map of renamed materials originalName -> newName.
                                # Used to deal with duplicate material names in different assets

    materialPluginNames = {p['Name'] for p in vrsceneDict if p['ID'] in MaterialTypeFilter}

    referencedMaterials = set()
    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] not in MaterialTypeFilter:
            continue
        for attrValue in pluginDesc.get('Attributes', {}).values():
            values = attrValue if isinstance(attrValue, list) else [attrValue]
            for value in values:
                if isinstance(value, str) and value.partition("::")[0] in materialPluginNames:
                    referencedMaterials.add(value.partition("::")[0])

    materialNames = [p['Name'] for p in vrsceneDict
                     if p['ID'] in MaterialTypeFilter and p['Name'] not in referencedMaterials]

    if cosmosAssetContext and cosmosAssetContext.applyTriplanarMapping and materialNames:
        # Cosmos provides both width and height. TexTriPlanar's `size` is a single scalar, so
        # for non-uniform tiles use TexTriPlanar as a pure projection (size=1.0) and bake the
        # per-axis tile size into every UVWGen's uvw_transform as diag(1/widthCm, 1/heightCm, 1).
        # For uniform/missing dimensions, just use the width as the triplanar size.
        hasNonUniform = (
            cosmosAssetContext.texRealWorldWidth > 0.0
            and cosmosAssetContext.texRealWorldHeight > 0.0
            and cosmosAssetContext.texRealWorldWidth != cosmosAssetContext.texRealWorldHeight
        )
        if hasNonUniform:
            triplanarSize = 1.0
            _applyRealWorldScaleToUVWGens(vrsceneDict, cosmosAssetContext.texRealWorldWidth, cosmosAssetContext.texRealWorldHeight)
        elif cosmosAssetContext.texRealWorldWidth > 0.0:
            triplanarSize = cosmosAssetContext.texRealWorldWidth
        else:
            triplanarSize = 1.0
        _wrapTexturesInTriPlanar(vrsceneDict, materialNames, triplanarSize)

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

        if cosmosAssetContext:
            mtl.vray.cosmos_package_id = cosmosAssetContext.packageId
            mtl.vray.cosmos_revision_id = cosmosAssetContext.revisionId

        ntree = mtl.node_tree
        importContext = NodesImport.ImportContext(ntree, vrsceneDict, locationsMap = locationsMap)

        mtlNode = None
        matOutputKey = 'Material'
        if  pluginDesc['ID'] == 'MtlSingleBRDF':
            brdfName = pluginDesc['Attributes']['brdf']
            matPlugin = NodesImport.getPluginByName(vrsceneDict, brdfName)
            mtlNode = NodesImport.createNode(importContext, matPlugin)

            if 'BRDF' in mtlNode.outputs:
                matOutputKey = 'BRDF'
        else:
            mtlNode = NodesImport.createNode(importContext, pluginDesc)

        outputNode = ntree.nodes.new('VRayNodeOutputMaterial')
        ntree.links.new(mtlNode.outputs[matOutputKey], outputNode.inputs['Material'])

        NodesTools.rearrangeTree(ntree, outputNode)
        NodesTools.deselectNodes(ntree)

        _checkNodeTree(mtl.node_tree, f"Material {mtl.name}")

        if cosmosAssetContext and not objectForMatAssign:
            debug.report('INFO', f"Cosmos material imported: {mtlName}")

    if objectForMatAssign:
        # Assign the imported materials to the object
        if multiMtl := next((d for d in vrsceneDict if (d['ID'] == 'MtlMulti') and ('mtls_list' in d['Attributes'])), None):
            _assignMaterialsToSlots(multiMtl, objectForMatAssign, mtlNameOverrides)
        else:
            # The object has only one material, assign to the first material slot
            mtlName = mtlNameOverrides.get(materialNames[0], materialNames[0])
            objectForMatAssign.data.materials.append(bpy.data.materials[mtlName])

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

            # Apply the transformation from the .vrmat file
            lightObj.matrix_world = lightObj.matrix_world @ attribute_utils.attrValueToMatrix(plgAttrs['transform'], True)

            geomMeshFile = parentObj.data.vray.GeomMeshFile
            objScale = geomMeshFile.scale
            localPos, _, _ = lightObj.matrix_local.decompose()  # Get position of the light in local space

            # Store the offset of light's center to the parent object's center in original scale units
            lightData.vray.initial_proxy_light_pos = localPos / objScale - geomMeshFile.initial_preview_mesh_pos
            lightData.vray.initial_proxy_light_scale = objScale

            # Assigning parent to the light object.
            # During cosmos import the parent will be the object from the package's .vrmesh file.
            lightObj.parent = parentObj

            lightNtree = createNodeTreeForLightObject(lightData, isNewLight = True)

            # The transformation is applied directly to the object, no need  for transform node generation
            del plgAttrs['transform']

            importContext = NodesImport.ImportContext(lightNtree, lightDict, locationsMap = locationsMap)
            lightNode = NodesImport.createNode(importContext, plgDesc)

            NodesTools.rearrangeTree(lightNtree, lightNode)
            NodesTools.deselectNodes(lightNtree)

            _checkNodeTree(lightNtree, f"Light {plgName}")


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

    ntree = createNodeTreeForLightObject(domeData, isNewLight = True)
    try:
        lightDesc = next((plgDesc for plgDesc in domeDict if plgDesc['ID'] == "LightDome"))
        texDesc = next((plgDesc for plgDesc in textureDict if plgDesc['ID'] == "TexBitmap"))
    except:
        debug.printError("HDRI import: either no 'LightDome' description in light file or 'TexBitmap' in texture file")
        return

    domeContext = NodesImport.ImportContext(ntree, domeDict, locationsMap=locationsMap)
    textureContext = NodesImport.ImportContext(ntree, textureDict, locationsMap=locationsMap)

    lightDomeNode = NodesImport.createNode(domeContext, lightDesc)
    textureNode = NodesImport.createNode(textureContext, texDesc)

    ntree.links.new(textureNode.outputs["Color"], lightDomeNode.inputs['Dome Color'])
    NodesTools.rearrangeTree(ntree, lightDomeNode)
    NodesTools.deselectNodes(ntree)


def importDecal(settings):
    if not os.path.exists(settings.matFile):
        debug.printError(f"VRmat file {settings.matFile} does not exist")
        return
    if not os.path.exists(settings.objFile):
        debug.printError(f"VRmat extras file {settings.objFile} does not exist")
        return


    # Add both the 'extras' file (the one with the VRayDecal definition) and the material
    # file to the scene dictionary as the obj file might reference plugins from the
    # material file
    extrasSceneDesc = parseVrmat(settings.objFile)
    matSceneDesc = parseVrmat(settings.matFile)

    # Leave just 1 'Import Settings' section. It is always the last list item in any scene description
    # and is identical for all .vrmat files loaded from the same folder.
    vrsceneDict = extrasSceneDesc[:-1] + matSceneDesc

    for decalDesc in [p for p in vrsceneDict if p['ID'] == 'VRayDecal']:
        purePath = PurePath(settings.matFile)
        obj = createDecalObject(bpy.context, f'VRayDecal@{purePath.stem}')

        addDecalNodeTree(obj)
        objTree = obj.vray.ntree

        importContext = NodesImport.ImportContext(objTree, vrsceneDict, locationsMap=settings.locationsMap)
        decalOutputNode = NodesImport.createNode(importContext, decalDesc)

        generateDecalPreviewMesh(obj, obj.data.vray.VRayDecal)

        obj.data.vray.cosmos_package_id = settings.packageId
        obj.data.vray.cosmos_revision_id = settings.revisionId

        NodesTools.rearrangeTree(objTree, decalOutputNode)
        NodesTools.deselectNodes(objTree)

        importMaterials(settings.matFile, objectForMatAssign=obj, locationsMap=settings.locationsMap, cosmosAssetContext=settings)

    if obj:
        blender_utils.selectObject(obj)


def _importVRayProxy(context, filePath, useRelativePath=False, scaleUnit=1.0, outMetadata: dict = None):
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
    proxyFilePath = filePath

    if useRelativePath:
        if relFilePath := tryGetRelativePath(filePath):
            proxyFilePath = relFilePath
        else:
            debug.report('INFO', "Cannot import V-Ray Proxy with relative path, using absolute path instead.")

    # Store the scale at which the model was imported. If its preview has to be regenerated, this value will be used to scale it.
    # The scale will be reset to 1 if the path to the mesh file is changed by the user and the new model will always be imported
    # with scale 1.0 due to the fact that there is no scale information in the mesh file itself.
    geomMeshFile['scale'] = scaleUnit / context.scene.unit_settings.scale_length

    context.collection.objects.link(ob)
    vrayAsset = ob.vray.VRayAsset

    if err := vray_proxy.loadVRayProxyPreviewMesh(ob, proxyFilePath, animFrame=0.0, outMetadata=outMetadata):
        return None, err

    vrayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Proxy"]
    geomMeshFile['file'] = proxyFilePath

    blender_utils.setShadowAttr(geomMeshFile, 'file', geomMeshFile.file)

    return ob, None


def importProxyFromMeshFile(context: bpy.types.Context, matPath: str, meshPath: str,
                            locationsMap: dict[str, str]=None, useRelPath=False, scaleUnit=1.0, select=True,
                            cosmosAssetContext: CosmosAssetSettings | None = None):
    """ Import a VRayProxy object from a .vrmesh or .abc file.
        This function will create a new scene object and load the preview mesh for it.

    Args:
        context (bpy.types.Context):
        matPath (str): Path to a .vrmat file with the materials for the object
        meshPath (str): Absolute path to the .vrmesh file
        locationsMap (dict(str, str), optional): A map of the resources used by the proxy (textures, materials etc) to
                                                fully resolved file paths for each resource. Defaults to None.
        useRelPath (bool, optional): The file paths are in Blender's relative notation. Defaults to False.
        scaleUnit (float, optional): Scale unit for the model in meters. Defaults to 1.0.
        select (bool, optional): True if the created object should be selected as active object.
        cosmosAssetContext (optional): The cosmos import context containing metadata about the imported asset.

    Returns:
        tuple[object, str]: A tuple containing the created object (or None on failure) and an error message (empty string on success).
    """
    assert not path_utils.isRelativePath(meshPath)

    metadata = {}
    objProxy, err = _importVRayProxy(context, meshPath, useRelPath, scaleUnit, outMetadata=metadata)

    if err:
        return None, err

    assert objProxy is not None

    # Create empty material slots from proxy shader metadata so users can assign materials
    # to the correct slots. Skip if a material file exists or this is a Cosmos import,
    # as importMaterials will create and assign the slots itself.
    isCosmos = cosmosAssetContext is not None
    hasMaterialFile = os.path.exists(matPath)
    if not isCosmos and not hasMaterialFile:
        if shaders := metadata.get('shaders'):
            _createEmptyMaterialSlotsFromProxy(objProxy, shaders)

    if hasMaterialFile:
        importMaterials(matPath, objectForMatAssign = objProxy, locationsMap = locationsMap, cosmosAssetContext = cosmosAssetContext)

    if select:
        # Make sure the main proxy object is selected. The selection might have been changed
        # if operators were executed during import which only work with the active object.
        blender_utils.selectObject(objProxy)

    return objProxy, ""


def importCosmosCompositeAsset(cosmosAssetContext: CosmosAssetSettings, scaleUnit=1.0):
    """ Import a Chaos Cosmos composite asset. It can contain V-Ray Proxy with materials and in some cases Lights.

    Args:
        cosmosAssetContext: The cosmos import context containing metadata about the imported asset.
        scaleUnit: (float, optional): Scale unit for the model in meters. Defaults to 1.0.
    Returns:
        tuple[object, str]: A tuple containing the created object (or None on failure) and an error message (empty string on success).
    """
    assert cosmosAssetContext is not None

    objProxy, err = importProxyFromMeshFile(bpy.context,
                                            cosmosAssetContext.matFile,
                                            cosmosAssetContext.objFile,
                                            locationsMap=cosmosAssetContext.locationsMap,
                                            scaleUnit=scaleUnit,
                                            cosmosAssetContext=cosmosAssetContext)

    if err:
        return None, err

    assert objProxy is not None
    objProxy.data.vray.cosmos_package_id = cosmosAssetContext.packageId
    objProxy.data.vray.cosmos_revision_id = cosmosAssetContext.revisionId

    if cosmosAssetContext.lightFile and os.path.exists(cosmosAssetContext.lightFile):
        _importLights(bpy.context, objProxy, cosmosAssetContext.lightFile, cosmosAssetContext.packageId, cosmosAssetContext.revisionId, cosmosAssetContext.locationsMap)

    return objProxy, ""


def importParallaxInterior(cosmosAssetContext: CosmosAssetSettings):
    """ Import a Cosmos Parallax Interior asset as a native Blender plane.

    Parallax Interior assets ship with plane-dimension metadata; we create the plane
    ourselves instead of loading the .vrmesh proxy for better render performance.
    The parallax material (containing a TexParallax node) is imported from the
    accompanying .vrmat and assigned to the plane's first material slot.
    """
    from vray_blender.lib import attribute_utils

    assert cosmosAssetContext is not None

    width = attribute_utils.scaleToSceneLengthUnit(cosmosAssetContext.planeWidth, "centimeters")
    height = attribute_utils.scaleToSceneLengthUnit(cosmosAssetContext.planeHeight, "centimeters")

    # Parallax Interiors are typically building windows/facades, authored to stand
    # vertically in the scene. Cosmos assets follow the Z-up, Y-forward convention,
    # so we create the plane on the XZ plane with its normal along +Y by rotating
    # the default (XY-plane, normal +Z) plane 90 degrees around X.
    bpy.ops.mesh.primitive_plane_add(size=1.0,
                                     location=bpy.context.scene.cursor.location,
                                     rotation=(math.pi / 2, 0.0, 0.0))
    planeObj = bpy.context.active_object
    planeObj.name = f"ParallaxInterior@{Path(cosmosAssetContext.matFile).stem}"
    # Scale in local axes: widthM along local X, heightM along local Y (which maps
    # to world Z after the 90-degree X rotation).
    planeObj.scale = (width, height, 1.0)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)

    if os.path.exists(cosmosAssetContext.matFile):
        importMaterials(cosmosAssetContext.matFile,
                        objectForMatAssign=planeObj,
                        locationsMap=cosmosAssetContext.locationsMap,
                        forceDefaultUVChannel=True,
                        cosmosAssetContext=cosmosAssetContext)

    planeObj.data.vray.cosmos_package_id = cosmosAssetContext.packageId
    planeObj.data.vray.cosmos_revision_id = cosmosAssetContext.revisionId

    blender_utils.selectObject(planeObj)


def _checkNodeTree(ntree: bpy.types.NodeTree, locatorName: str):
    # Check that all imported nodes are linked
    if ntree.vray.tree_type == 'LIGHT':
        outputNode = getLightOutputNode(ntree)
    else:
        outputNode = getOutputNode(ntree)

    if outputNode is None:
        debug.printWarning(f"{locatorName} does not have an output node")
        return

    def getConnectedNodes(node: bpy.types.Node, nodes: set):
        # Walk the node tree and make sure that all nodes are connected
        for s in node.inputs:
            for l in s.links:
                nodes.add(l.from_node)
                getConnectedNodes(l.from_node, nodes)


    treeNodes = {outputNode,}
    getConnectedNodes(outputNode, treeNodes)
    vrayNodes = sum(1 for n in ntree.nodes if NodesTools.isVrayNode(n))

    if len(treeNodes) != vrayNodes:
        debug.printWarning(f"{locatorName} was imported with some non-connected nodes")