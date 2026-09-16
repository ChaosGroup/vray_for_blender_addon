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

from vray_blender.vray_tools.vrmat_parser import parseVrmat

from vray_blender import debug
from vray_blender.vray_tools import vray_proxy
from vray_blender.lib import blender_utils, path_utils
from vray_blender.lib.lib_utils import (cleanImportedPluginName, getCosmosAssetName,
                                        sanitizeDatablockName, LightTypeToPlugin)
from vray_blender.lib.path_utils import tryGetRelativePath
from vray_blender.plugins.geometry.VRayDecal import createDecalObject, generateDecalPreviewMesh
from vray_blender.bin.VRayBlenderLib import CosmosAssetSettings
from vray_blender.vray_tools.import_common import (refName as _refName, sceneName as _sceneName,
                                                   MATERIAL_ROOT_TYPES, WRAPPER_BASE_ATTRS,
                                                   getImportDir)
from pathlib import Path

def _createMaterial(mtlName):
    mtl = bpy.data.materials.new(mtlName)
    addMaterialNodeTree(mtl, addDefaultTree=False)

    # removing default Blender nodes
    removeNonVRayNodes(mtl.node_tree)

    return mtl


# Material-option wrappers modeled as toggles/fields on the material rather than nodes
# (reverses mtl_export._exportMtlOption). MtlUVWSelect, MtlSelectRE and MtlWrapperMaya have
# neither a node nor a toggle and are peeled without their own params.
_PEELED_WRAPPER_TYPES = ('MtlRoundEdges', 'MtlMaterialID', 'MtlRenderStats', 'MtlWrapper', 'MtlUVWSelect',
                         'MtlSelectRE', 'MtlWrapperMaya')


def _peelMaterialWrappers(vrsceneDict, mtl, pluginDesc):
    """ Peel MtlRoundEdges/MtlMaterialID/MtlRenderStats/MtlWrapper option wrappers off the
        root material, enabling the matching toggle on mtl.vray.<Type> and filling its
        fields, and return the innermost wrapped material's pluginDesc. Wrappers nested
        deeper than the root are left for the generic node path (createNode). """
    seen = set()
    while (pluginDesc is not None
           and pluginDesc['ID'] in _PEELED_WRAPPER_TYPES
           and pluginDesc['Name'] not in seen):
        seen.add(pluginDesc['Name'])
        pluginType = pluginDesc['ID']
        if propGroup := getattr(mtl.vray, pluginType, None):
            NodesImport._pluginAttrsToPropGroup(pluginDesc, propGroup, pluginType)
            if hasattr(propGroup, 'use'):
                propGroup.use = True
        baseRef = pluginDesc['Attributes'].get(WRAPPER_BASE_ATTRS[pluginType])
        pluginDesc = NodesImport.getPluginByName(vrsceneDict, _refName(baseRef)) if baseRef else None
    return pluginDesc


def _resolveMtlSingle(vrsceneDict, pluginDesc):
    """ MtlSingleBRDF has no node of its own - it is represented by the material output node.
        Follow its 'brdf' chain to the actual shader plugin (BRDF/material wrapper) that does. """
    seen = set()
    while (pluginDesc is not None and pluginDesc['ID'] == 'MtlSingleBRDF'
           and pluginDesc['Name'] not in seen):
        seen.add(pluginDesc['Name'])
        pluginDesc = NodesImport.getPluginByName(vrsceneDict, _refName(pluginDesc['Attributes'].get('brdf')))
    return pluginDesc

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

    # A .vrscene reference may carry an output socket ('name::output'), which is not part of the
    # plugin name the override map is keyed on.
    mtlsWithIds = []
    for id, ref in zip(idsList, mtlsList):
        pluginName = _refName(ref)
        mtlsWithIds.append((int(id), mtlNameOverrides.get(pluginName, pluginName)))

    mtlsWithIds.sort(key=lambda e: e[0])

    slotCounter = 0
    for id, mtl in mtlsWithIds:
        if mtl not in bpy.data.materials:
            # The MtlMulti lists a material that was not imported, e.g. because another
            # material already consumed it.
            debug.printWarning(f"Material {mtl} of the imported MtlMulti was not created, skipping slot {id}")
            continue

        while slotCounter < id:
            obj.data.materials.append(None)
            slotCounter += 1
        # A sub-material that failed to import leaves an empty (None) slot.
        obj.data.materials.append(bpy.data.materials.get(mtl))
        slotCounter += 1


def _addMaterialSlotsForProxyShaders(obj: bpy.types.Object, shaders: list):
    """ Create empty material slots on a proxy object based on the shader info from the proxy file.
        Slot ordering matches shader IDs so that face material indices align correctly.

    Args:
        obj: The proxy object to add slots to.
        shaders: List of shader descriptors [{'name': str, 'id': int}, ...].
    """
    if not shaders:
        return

    # Slot indices follow the sorted shader IDs.
    sortedShaders = sorted(shaders, key=lambda s: s['id'])

    slotCounter = 0
    for shader in sortedShaders:
        # Fill gaps with empty slots (same pattern as _assignMaterialsToSlots)
        while slotCounter < shader['id']:
            obj.data.materials.append(None)
            slotCounter += 1

        # The shader's own slot: also empty (None) for now; materials are assigned later
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
    whose `texture_x` points at the original texture. Wrapped textures are not descended into.
    Already-TexTriPlanar plugins are left alone.
    """
    nameToDesc = {p['Name']: p for p in vrsceneDict}
    triplanarCounter = [0]
    visited: set[str] = set()
    queue: list[str] = [n for n in rootPluginNames if n in nameToDesc]

    def wrapReference(value: str) -> str:
        # value is "pluginName" or "pluginName::output". Replace with a new TexTriPlanar that
        # references the original texture and return the new reference string, preserving the
        # caller's requested output socket (e.g. ::out_intensity for a float input).
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
                'ref_space': '1'
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

    if filePath.endswith(".vrscene"):
        # Read through the server importer (importVrsceneSync starts the ZMQ server if needed).
        from vray_blender.vray_tools.vrscene_import import importVrsceneSync
        vrsceneDict = importVrsceneSync(filePath)
    else:
        vrsceneDict = parseVrmat(filePath)

    # A name-indexed list makes plugin-link resolution during import O(1).
    vrsceneDict = NodesImport.IndexedVrsceneDict(vrsceneDict)

    # Fix any plugin params that need special handling, e.g. version upgrades etc.
    NodesImport.fixPluginParams(vrsceneDict, forceDefaultUVChannel=forceDefaultUVChannel)

    importMaterialsFromDict(vrsceneDict,
                            objectForMatAssign=objectForMatAssign,
                            locationsMap=locationsMap,
                            cosmosAssetContext=cosmosAssetContext)

    return {'FINISHED'}


def importMaterialsFromDict(vrsceneDict, *, materialRoots: list[str] = None,
                            objectForMatAssign: bpy.types.Object = None,
                            locationsMap: dict[str, str] = None,
                            objectResolver = None,
                            cosmosAssetContext: CosmosAssetSettings | None = None,
                            stats = None, ledger = None) -> dict[str, str]:
    """ Import V-Ray materials from a parsed vrscene dict. fixPluginParams() is
        expected to have been run on the dict already.

        @param materialRoots - explicit list of material plugin names to import. When
               None, the top-level materials are auto-detected (see below).
        @param objectResolver - optional callback(pluginName, linkType) -> bpy Object,
               used to resolve object-referencing texture attributes (e.g. TexDistance).
        @return {materialPluginName: bpy material name} for every imported material.
    """

    # Collect the names of all material-type plugins, then keep only the top-level ones.
    # A material plugin is "top-level" only if no other material plugin references it.
    # Only references made by plugins in MATERIAL_ROOT_TYPES count as "consuming" a material.
    # Never add MtlMulti to that filter - each of its sub-materials stays top-level and is
    # imported separately.
    mtlNameOverrides = {}       # Map of renamed materials originalName -> newName.
                                # Used to deal with duplicate material names in different assets

    if materialRoots is not None:
        materialNames = list(materialRoots)
    else:
        materialPluginNames = {p['Name'] for p in vrsceneDict if p['ID'] in MATERIAL_ROOT_TYPES}

        referencedMaterials = set()
        for pluginDesc in vrsceneDict:
            if pluginDesc['ID'] not in MATERIAL_ROOT_TYPES:
                continue
            for attrValue in pluginDesc.get('Attributes', {}).values():
                values = attrValue if isinstance(attrValue, list) else [attrValue]
                for value in values:
                    if isinstance(value, str) and value.partition("::")[0] in materialPluginNames:
                        referencedMaterials.add(value.partition("::")[0])

        materialNames = [p['Name'] for p in vrsceneDict
                         if p['ID'] in MATERIAL_ROOT_TYPES and p['Name'] not in referencedMaterials]

    if cosmosAssetContext and cosmosAssetContext.applyTriplanarMapping and materialNames:
        # TexTriPlanar's `size` is a single scalar. Non-uniform tiles use it as a pure projection
        # (size=1.0), with the per-axis tile size baked into every UVWGen's uvw_transform as
        # diag(1/widthCm, 1/heightCm, 1). Uniform/missing dimensions use the width as the size.
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

    importedMaterials = {}      # {materialPluginName: bpy material name}

    assetName = getCosmosAssetName(cosmosAssetContext)
    mtlPluginNames = [cleanImportedPluginName(n) for n in materialNames]

    sceneBaseDir = getImportDir(vrsceneDict)

    for i, pluginName in enumerate(materialNames):
        debug.printInfo(f"Importing material: {pluginName}")

        try:
            pluginDesc = NodesImport.getPluginByName(vrsceneDict, pluginName)

            # A single material takes the asset name; the parts of a model are prefixed with it.
            # Plain .vrmat/.vrscene imports carry no asset name and prefer the host-app name
            # (scene_name).
            if assetName:
                mtlName = assetName if len(materialNames) == 1                           else f"{assetName} {mtlPluginNames[i]}"
            else:
                mtlName = _sceneName(pluginDesc.get('Attributes', {}), pluginName) if pluginDesc                           else pluginName

            mtl = _createMaterial(mtlName)

            # bpy.data.materials.new() makes the name unique; read back what it settled on.
            # Maps the plugin name to the created material name for slot assignment.
            mtlNameOverrides[pluginName] = mtl.name
            if mtl.name != mtlName:
                debug.printInfo(f"Material name already exists, changing to {mtl.name}")

            if cosmosAssetContext:
                mtl.vray.cosmos_package_id = cosmosAssetContext.packageId
                mtl.vray.cosmos_revision_id = cosmosAssetContext.revisionId
                mtl.vray.cosmos_asset_name = cosmosAssetContext.assetName

            ntree = mtl.node_tree
            importContext = NodesImport.ImportContext(ntree, vrsceneDict, locationsMap = locationsMap,
                                                      texNamePrefix = assetName,
                                                      texNameOwners = mtlPluginNames,
                                                      objectResolver = objectResolver,
                                                      sceneBaseDir = sceneBaseDir,
                                                      stats = stats, ledger = ledger)

            # Peel MtlMaterialID / MtlRoundEdges option wrappers off the root onto the
            # material's option toggles; returns the innermost wrapped material.
            pluginDesc = _peelMaterialWrappers(vrsceneDict, mtl, pluginDesc)
            if pluginDesc is None:
                debug.printWarning(f"Skipping material '{pluginName}': empty wrapper chain")
                bpy.data.materials.remove(mtl)
                continue

            mtlNode = None
            toonOverrideDesc = None

            # MtlSingleBRDF has no node of its own (it maps onto the material output node);
            # unwrap it to the shader plugin that does - a BRDF, a material wrapper
            # (MtlDisplacement, ...) or a BRDFToonOverride.
            shaderDesc = _resolveMtlSingle(vrsceneDict, pluginDesc)

            # BRDFToonOverride (V-Ray "Outlines") wraps a base material and drives the output
            # node's dedicated Outlines socket while its base drives the Material socket. It may
            # be the shader inside a material or the material root itself (e.g. a decal material).
            if shaderDesc is not None and shaderDesc['ID'] == 'BRDFToonOverride':
                toonOverrideDesc = shaderDesc
                baseDesc = _resolveMtlSingle(vrsceneDict, NodesImport.getPluginByName(
                    vrsceneDict, _refName(shaderDesc['Attributes'].get('base_brdf'))))
                mtlNode = NodesImport.createNode(importContext, baseDesc) if baseDesc else None
            elif shaderDesc is not None:
                mtlNode = NodesImport.createNode(importContext, shaderDesc)

            if mtlNode is None and toonOverrideDesc is None:
                # The root BRDF/material is an unsupported plugin type (createNode warned).
                debug.printWarning(f"Skipping material '{pluginName}': unsupported shader")
                bpy.data.materials.remove(mtl)
                continue

            outputNode = ntree.nodes.new('VRayNodeOutputMaterial')
            # Material nodes expose a 'Material' output; a bare BRDF exposes 'BRDF'. The output
            # node's Material input accepts either. A BRDFToonOverride whose base_brdf is absent
            # (or unsupported) has no material node - wire the outlines alone.
            matOutputKey = None if mtlNode is None else \
                           ('Material' if 'Material' in mtlNode.outputs
                            else 'BRDF' if 'BRDF' in mtlNode.outputs else None)
            if matOutputKey is not None:
                ntree.links.new(mtlNode.outputs[matOutputKey], outputNode.inputs['Material'])

            # Connect the outlines override (if any) to the output node's Outlines socket.
            if toonOverrideDesc is not None and 'Outlines' in outputNode.inputs:
                toonNode = NodesImport.createNode(importContext, toonOverrideDesc)
                if toonNode is not None and 'BRDF' in toonNode.outputs:
                    ntree.links.new(toonNode.outputs['BRDF'], outputNode.inputs['Outlines'])

            NodesTools.arrangeImportedTree(ntree, outputNode)
            NodesTools.deselectNodes(ntree)

            _checkNodeTree(mtl.node_tree, f"Material {mtl.name}")

            importedMaterials[pluginName] = mtl.name

            if cosmosAssetContext and not objectForMatAssign:
                debug.report('INFO', f"Cosmos material imported: {mtl.name}")
        except Exception as e:
            # A broken material must not abort the import of the remaining ones.
            debug.printExceptionInfo(e, f"Importing material '{pluginName}'")
            debug.printError(f"Failed to import material '{pluginName}': {e}")

    if objectForMatAssign:
        # Assign the imported materials to the object
        if multiMtl := next((d for d in vrsceneDict if (d['ID'] == 'MtlMulti') and ('mtls_list' in d['Attributes'])), None):
            _assignMaterialsToSlots(multiMtl, objectForMatAssign, mtlNameOverrides)
        elif importedMaterials:
            # The object has only one material, assign to the first material slot
            mtlName = importedMaterials.get(materialNames[0], materialNames[0])
            objectForMatAssign.data.materials.append(bpy.data.materials[mtlName])
        else:
            debug.printWarning(f"No material to assign to {objectForMatAssign.name} from {sceneBaseDir}")

    return importedMaterials


def _importProxyChildLight(context: bpy.types.Context, parentObj, lightDict: list, plgDesc: dict,
                           pluginType: str, lightName: str, packageId: str, revisionId: int,
                           locationsMap: dict[str, str], assetName: str, texNamePrefix: str):
    """ Create one light of a Cosmos composite asset as a child of the asset's proxy object.

        Shared by the asset's light sources and its Luminaire. Returns the light object and its
        light node.
    """
    from vray_blender.lib import attribute_utils

    plgAttrs = plgDesc['Attributes']

    lightData = NodesImport.createLightFromPluginDesc(pluginType, plgAttrs, lightName)
    lightData.vray.cosmos_package_id = packageId
    lightData.vray.cosmos_revision_id = revisionId
    lightData.vray.cosmos_asset_name = assetName

    lightObj = bpy.data.objects.new(name=lightName, object_data=lightData)
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

    importContext = NodesImport.ImportContext(lightNtree, lightDict, locationsMap = locationsMap,
                                              texNamePrefix = texNamePrefix)
    lightNode = NodesImport.createNode(importContext, plgDesc)

    NodesTools.arrangeImportedTree(lightNtree, lightNode)
    NodesTools.selectOnlyNode(lightNtree, lightNode)

    _checkNodeTree(lightNtree, f"Light {lightName}")

    return lightObj, lightNode


def _importLights(context: bpy.types.Context, parentObj, lightPath: str, packageId: str, revisionId: int,
                  locationsMap: dict[str, str], assetName: str = ""):
    lightDict = parseVrmat(lightPath)

    # A lamp with two bulbs gets 'lightsource_1' and 'lightsource_2', as in 3dsmax and C4D.
    lightIndex = 0
    lightBaseName = sanitizeDatablockName(assetName)

    for plgDesc in lightDict:
        if (pluginType := plgDesc['ID']) in LightTypeToPlugin.values(): # Checking for light plugins
            if pluginType == 'LightLuminaire':
                # _importCosmosLuminaire owns the luminaire (older assets embed it here).
                continue

            plgName = plgDesc['Name']
            if lightBaseName:
                lightIndex += 1
                plgName = f"{lightBaseName} lightsource_{lightIndex}"

            _importProxyChildLight(context, parentObj, lightDict, plgDesc, pluginType, plgName,
                                   packageId, revisionId, locationsMap, assetName, lightBaseName)


def _findLuminaireCache(vrmatPath: str):
    """ Path of the luminaire cache shipped in the package of 'vrmatPath', or None. Cosmos ships
        the wavelet-compressed .vlw, but the plugin also reads grid (.vlg) and spherical
        harmonics (.vlsh) caches.

        Only used to report a package that promises a luminaire but ships no cache. The cache the
        light actually uses comes from the plugin's 'file' attr via the asset locations map.
    """
    assetsDir = os.path.join(os.path.dirname(vrmatPath), 'Assets')
    if not os.path.isdir(assetsDir):
        return None
    return next((str(p) for ext in ('*.vlw', '*.vlg', '*.vlsh')
                 for p in Path(assetsDir).glob(ext)), None)


def _luminaireVrmatSource(cosmosAssetContext: CosmosAssetSettings):
    """ The .vrmat to read the asset's LightLuminaire from, or None if it has no luminaire.

        Normally a file of its own, named by the package's 'backward incompatible plugins' map.
        Older assets embed the plugin in the main light .vrmat.
    """
    if (luminaireFile := cosmosAssetContext.luminaireFile) and os.path.exists(luminaireFile):
        return luminaireFile

    lightFile = cosmosAssetContext.lightFile
    if lightFile and os.path.exists(lightFile) \
            and any(d['ID'] == 'LightLuminaire' for d in parseVrmat(lightFile)):
        return lightFile

    return None


def _importCosmosLuminaire(context: bpy.types.Context, parentObj, luminairePath: str,
                           packageId: str, revisionId: int, locationsMap: dict[str, str],
                           scaleUnit: float, assetName: str = ""):
    """ Create the V-Ray Luminaire light of a Cosmos light asset, in addition to the asset's own
        light sources (as in Maya and Cinema 4D).

        'scale' converts the centimeters the cache was baked in to scene units.
    """
    luminaireDict = parseVrmat(luminairePath)

    if not (plgDesc := next((d for d in luminaireDict if d['ID'] == 'LightLuminaire'), None)):
        debug.printError(f"No LightLuminaire plugin in the Cosmos luminaire file {luminairePath}")
        return None

    # Named after the asset, next to its 'lightsource_N' light sources.
    luminaireBaseName = sanitizeDatablockName(assetName)
    lightName = f"{luminaireBaseName} luminaire" if luminaireBaseName \
                else f'VRayLuminaire@{Path(luminairePath).stem}'

    lightObj, lightNode = _importProxyChildLight(context, parentObj, luminaireDict, plgDesc,
                                                 'LightLuminaire', lightName,
                                                 packageId, revisionId, locationsMap,
                                                 assetName, luminaireBaseName)

    lightNode.LightLuminaire.scale = scaleUnit / context.scene.unit_settings.scale_length

    return lightObj


def importHDRI(texturePath: str, lightPath: str, packageId: str, revisionId: str, locationsMap: dict[str, str],
               assetName: str = ""):
    # Takes two '.vrmat' files (one for HDR texture and one for dome)
    # and creates dome light object that projects HDR map

    domeDict = parseVrmat(lightPath)
    textureDict = parseVrmat(texturePath)

    try:
        lightDesc = next((plgDesc for plgDesc in domeDict if plgDesc['ID'] == "LightDome"))
        texDesc = next((plgDesc for plgDesc in textureDict if plgDesc['ID'] == "TexBitmap"))
    except:
        debug.printError("HDRI import: either no 'LightDome' description in light file or 'TexBitmap' in texture file")
        return

    domeBaseName = sanitizeDatablockName(assetName)
    name = domeBaseName or f'VRayDomeLight@{Path(lightPath).stem}'
    domeData = bpy.data.lights.new(name=name , type='POINT')
    domeData.vray.light_type = 'DOME'
    domeData.vray.cosmos_package_id = packageId
    domeData.vray.cosmos_revision_id = revisionId
    domeData.vray.cosmos_asset_name = assetName

    # Always build the light's node tree before linking the object into the scene.
    ntree = createNodeTreeForLightObject(domeData, isNewLight = True)

    domeContext = NodesImport.ImportContext(ntree, domeDict, locationsMap=locationsMap)
    textureContext = NodesImport.ImportContext(ntree, textureDict, locationsMap=locationsMap,
                                               texNamePrefix = domeBaseName,
                                               texNameOwners = [cleanImportedPluginName(texDesc['Name'])])

    lightDomeNode = NodesImport.createNode(domeContext, lightDesc)
    textureNode = NodesImport.createNode(textureContext, texDesc)

    ntree.links.new(textureNode.outputs["Color"], lightDomeNode.inputs['Dome Color'])
    NodesTools.arrangeImportedTree(ntree, lightDomeNode)
    NodesTools.selectOnlyNode(ntree, lightDomeNode)

    domeObj = bpy.data.objects.new(name=name, object_data=domeData)
    bpy.context.collection.objects.link(domeObj)
    blender_utils.selectObject(domeObj)


def importDecal(settings):
    if not os.path.exists(settings.matFile):
        debug.printError(f"VRmat file {settings.matFile} does not exist")
        return
    if not os.path.exists(settings.objFile):
        debug.printError(f"VRmat extras file {settings.objFile} does not exist")
        return


    # Add both the 'extras' file (the one with the VRayDecal definition) and the material
    # file to the scene dictionary.
    extrasSceneDesc = parseVrmat(settings.objFile)
    matSceneDesc = parseVrmat(settings.matFile)

    # Leave just 1 'Import Settings' section. It is always the last list item in any scene description
    # and is identical for all .vrmat files loaded from the same folder.
    vrsceneDict = extrasSceneDesc[:-1] + matSceneDesc

    assetName = getCosmosAssetName(settings)

    for decalDesc in [p for p in vrsceneDict if p['ID'] == 'VRayDecal']:
        purePath = PurePath(settings.matFile)
        obj = createDecalObject(bpy.context, assetName or f'VRayDecal@{purePath.stem}')

        addDecalNodeTree(obj)
        objTree = obj.vray.ntree

        # VRayDecal can hold textures of its own ('mask', 'displacement_tex_color'), which have
        # to be named like the ones of the decal's material below.
        importContext = NodesImport.ImportContext(objTree, vrsceneDict, locationsMap=settings.locationsMap,
                                                 texNamePrefix = assetName)
        decalOutputNode = NodesImport.createNode(importContext, decalDesc)

        generateDecalPreviewMesh(obj)

        obj.data.vray.cosmos_package_id = settings.packageId
        obj.data.vray.cosmos_revision_id = settings.revisionId
        obj.data.vray.cosmos_asset_name = settings.assetName

        NodesTools.arrangeImportedTree(objTree, decalOutputNode)
        NodesTools.deselectNodes(objTree)

        importMaterials(settings.matFile, objectForMatAssign=obj, locationsMap=settings.locationsMap, cosmosAssetContext=settings)

    if obj:
        blender_utils.selectObject(obj)


def _importVRayProxy(context, filePath, useRelativePath=False, scaleUnit=1.0, outMetadata: dict = None,
                     objName: str = ""):
    if not os.path.exists(filePath):
        return None, f"File not found: {filePath}"

    if (fileExt:= PurePath(filePath).suffix) not in ('.vrmesh', '.abc'):
        return None, f"File format {fileExt} is not supported by V-Ray Proxy"

    purePath = PurePath(filePath)

    # Add new mesh object
    name = objName or f'VRayProxy@{purePath.stem}'

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
    # with scale 1.0.
    geomMeshFile['scale'] = scaleUnit / context.scene.unit_settings.scale_length

    context.collection.objects.link(ob)
    vrayAsset = ob.vray.VRayAsset

    if err := vray_proxy.loadVRayProxyPreviewMesh(ob.data.vray.GeomMeshFile, proxyFilePath, animFrame=0.0, outMetadata=outMetadata):
        # The object is already linked into the collection; drop it and its mesh.
        bpy.data.objects.remove(ob, do_unlink=True)
        bpy.data.meshes.remove(previewMesh)
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
    objProxy, err = _importVRayProxy(context, meshPath, useRelPath, scaleUnit, outMetadata=metadata,
                                     objName=getCosmosAssetName(cosmosAssetContext))

    if err:
        return None, err

    assert objProxy is not None

    # Create empty material slots from proxy shader metadata. Skipped when a material file
    # exists or this is a Cosmos import - importMaterials creates and assigns the slots then.
    isCosmos = cosmosAssetContext is not None
    hasMaterialFile = os.path.exists(matPath)
    if not isCosmos and not hasMaterialFile:
        if shaders := metadata.get('shaders'):
            _addMaterialSlotsForProxyShaders(objProxy, shaders)

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
    objProxy.data.vray.cosmos_asset_name = cosmosAssetContext.assetName

    if cosmosAssetContext.lightFile and os.path.exists(cosmosAssetContext.lightFile):
        _importLights(bpy.context, objProxy, cosmosAssetContext.lightFile, cosmosAssetContext.packageId,
                      cosmosAssetContext.revisionId, cosmosAssetContext.locationsMap,
                      cosmosAssetContext.assetName)

    # The luminaire comes in addition to the light sources above, not instead of them.
    if luminaireFile := _luminaireVrmatSource(cosmosAssetContext):
        if not _findLuminaireCache(luminaireFile):
            debug.printWarning(f"Cosmos asset '{cosmosAssetContext.packageId}' declares a "
                               f"LightLuminaire but ships no luminaire cache. The "
                               f"luminaire will render nothing until its file is relinked.")
        _importCosmosLuminaire(bpy.context, objProxy, luminaireFile,
                               cosmosAssetContext.packageId, cosmosAssetContext.revisionId,
                               cosmosAssetContext.locationsMap, scaleUnit,
                               cosmosAssetContext.assetName)

    return objProxy, ""


def importParallaxInterior(cosmosAssetContext: CosmosAssetSettings):
    """ Import a Cosmos Parallax Interior asset as a native Blender plane.

    Parallax Interior assets ship with plane-dimension metadata; the plane is created here
    instead of loading the .vrmesh proxy. The parallax material (containing a TexParallax
    node) is imported from the accompanying .vrmat and assigned to the plane's first
    material slot.
    """
    from vray_blender.lib import attribute_utils

    assert cosmosAssetContext is not None

    width = attribute_utils.scaleToSceneLengthUnit(cosmosAssetContext.planeWidth, "centimeters")
    height = attribute_utils.scaleToSceneLengthUnit(cosmosAssetContext.planeHeight, "centimeters")

    # Cosmos assets follow the Z-up, Y-forward convention: the plane stands on the XZ plane
    # with its normal along +Y, i.e. the default (XY-plane, normal +Z) plane rotated 90
    # degrees around X.
    bpy.ops.mesh.primitive_plane_add(size=1.0,
                                     location=bpy.context.scene.cursor.location,
                                     rotation=(math.pi / 2, 0.0, 0.0))
    planeObj = bpy.context.active_object
    planeObj.name = getCosmosAssetName(cosmosAssetContext) or f"ParallaxInterior@{Path(cosmosAssetContext.matFile).stem}"
    # primitive_plane_add() leaves the mesh datablock named 'Plane'
    planeObj.data.name = planeObj.name
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
    planeObj.data.vray.cosmos_asset_name = cosmosAssetContext.assetName

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
        # Walk the node tree and make sure that all nodes are connected. Recurse only
        # into unvisited nodes - also breaks cyclic links (possible in imported trees).
        for s in node.inputs:
            for l in s.links:
                if l.from_node not in nodes:
                    nodes.add(l.from_node)
                    getConnectedNodes(l.from_node, nodes)


    treeNodes = {outputNode,}
    getConnectedNodes(outputNode, treeNodes)
    vrayNodes = sum(1 for n in ntree.nodes if NodesTools.isVrayNode(n))

    if len(treeNodes) != vrayNodes:
        debug.printWarning(f"{locatorName} was imported with some non-connected nodes")