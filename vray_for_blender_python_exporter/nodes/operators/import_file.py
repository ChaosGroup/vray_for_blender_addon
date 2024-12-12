import os
import bpy
import queue

from vray_blender.nodes import importing as NodesImport
from vray_blender.nodes import tools     as NodesTools
from vray_blender.nodes.tree_defaults import addMaterialNodeTree, createNodeTreeForLightObject, removeNonVRayNodes

from vray_blender.vray_tools.vrscene_parser import parseVrscene
from vray_blender.vray_tools.vrmat_parser     import parseVrmat


from vray_blender import debug
from vray_blender import proxy as ProxyUtils
from vray_blender.lib import blender_utils
from vray_blender.lib.lib_utils import getUUID, LightTypeToPlugin, LightBlenderToVrayPlugin

from vray_blender.lib.blender_utils import getCmToSceneUnitsMultiplier
from pathlib import Path

def _createMaterial(mtlName, use_fake_user=True):
    mtl = bpy.data.materials.new(mtlName)
    addMaterialNodeTree(mtl, addDefaultTree=False)

    # removing default Blender nodes 
    removeNonVRayNodes(mtl.node_tree)

    mtl.use_fake_user = use_fake_user
    mtl.node_tree.use_fake_user = use_fake_user
    
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


def _importMaterials(context, filePath, baseMaterial, use_fake_user=True, objectForMatAssign = None, locationsMap = None):
    debug.printInfo('Importing materials from "%s"' % filePath)

    vrsceneDict = {}

    if filePath.endswith(".vrscene"):
        vrsceneDict = parseVrscene(filePath)
    else:
        vrsceneDict = parseVrmat(filePath)

    # Fix any plugin params that need special handling, e.g. version upgrades etc.
    _fixPluginParams(vrsceneDict, materialAssetsOnly=not objectForMatAssign)


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
        
        mtl = _createMaterial(mtlName, use_fake_user)
        ntree = mtl.node_tree
        
        outputNode = ntree.nodes.new('VRayNodeOutputMaterial')

        mtlNode = None
        matOutputKey = 'Material'
        if  pluginDesc['ID'] == 'MtlSingleBRDF':
            brdfName = pluginDesc['Attributes']['brdf']
            matPlugin = NodesImport.getPluginByName(vrsceneDict, brdfName)    
            mtlNode = NodesImport.createNode(ntree, outputNode, vrsceneDict, matPlugin, locationsMap)
            
            if 'BRDF' in mtlNode.outputs:
                matOutputKey = 'BRDF'
        else:
            mtlNode = NodesImport.createNode(ntree, outputNode, vrsceneDict, pluginDesc, locationsMap)

        ntree.links.new(mtlNode.outputs[matOutputKey], outputNode.inputs['Material'])

        NodesTools.rearrangeTree(ntree, outputNode)
        NodesTools.deselectNodes(ntree)

        if not objectForMatAssign:
            debug.report('INFO', f"Cosmos material imported: {mtlName}")

    # The created materials are assigned to a the passed blender object
    # if objectForMatAssign!=None and there is MtlMulti in vrsceneDict
    _assignMaterialsToSlots(vrsceneDict, objectForMatAssign, mtlNameOverrides)     

    return {'FINISHED'}



def _importLights(context, parentObj, lightPath, locationsMap):
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

            # Setting the size of area light
            if lightData.type == 'AREA':
                for vrAttr, blAttr in (("u_size", "size"), ("v_size", "size_y")):
                    if vrAttr in plgAttrs:
                        setattr(lightData, blAttr, attribute_utils.scaleToSceneLengthUnit(plgAttrs[vrAttr], "centimeters") * 2)

            lightObj = bpy.data.objects.new(name=plgName, object_data=lightData)
            lightObj.location = context.scene.cursor.location
            context.collection.objects.link(lightObj)

            # Assigning parent to the light object.
            # During cosmos import the parent will be the object from the package's .vrmesh file.
            lightObj.parent = parentObj

            # Applying the transformation from the .vrmat file should happen after the assigning of parent,
            # because it will overwrite 'matrix_world'.
            lightObj.matrix_world = lightObj.matrix_world @ attribute_utils.attrValueToMatrix(plgAttrs['transform'], True)
            lightObj.hide_select = True

            lightNtree = createNodeTreeForLightObject(lightData)

            # The transformation is applied directly to the object, no need  for transform node generation
            del plgAttrs['transform']
            
            lightNode = NodesImport.createNode(lightNtree, None, lightDict, plgDesc, locationsMap)
            
            NodesTools.rearrangeTree(lightNtree, lightNode)
            NodesTools.deselectNodes(lightNtree)


def _importHDRI(texturePath, lightPath, locationsMap):
    # Takes two '.vrmat' files (one for HDR texture and one for dome)
    # and creates dome light object that projects HDR map
    
    domeDict = parseVrmat(lightPath)
    textureDict = parseVrmat(texturePath)

    name = f'VRayDomeLight@{Path(lightPath).stem}'
    domeData = bpy.data.lights.new(name=name , type='POINT')
    domeData.vray.light_type = 'DOME'

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

    lightDomeNode = NodesImport.createNode(ntree, None, domeDict, lightDesc, locationsMap)
    textureNode = NodesImport.createNode(ntree, None, textureDict, texDesc, locationsMap)

    ntree.links.new(textureNode.outputs["Color"], lightDomeNode.inputs['Dome Color'])
    NodesTools.rearrangeTree(ntree, lightDomeNode)
    NodesTools.deselectNodes(ntree)


def _fixPluginParams(vrsceneDict: dict, materialAssetsOnly: bool):
    """ Fix any plugin parameters that need special handling.

        materialAssetsOnly(bool) : True if the asset being imported is a material (has no geometry).
                                    Geometry models have their channels explicitly set so we don't want
                                    to change the indices.
    """

    if materialAssetsOnly:
        for uvwGenChannel in [v for v in vrsceneDict if v['ID'] == 'UVWGenChannel']:
            # V4B and Cosmos materials often disagree on what the indexes of the UVW maps should be.
            # Cosmos usually starts from 1 while V4B expects 0. This is fine for geometry assets imported
            # from Cosmos, but the materials cannot be assigned to non-cosmos objects without modification.
            # If just a material is being imported, it is safe to fix the UVW channel numbers so that Blender
            # objects could render correctly with the imported material.
            #   Set to -1 which is a special value designating the default UVW channel
            uvwGenChannel['Attributes']['uvw_channel'] = -1

    for vrayMtl in [p for p in vrsceneDict if p['ID'] == 'BRDFVRayMtl']:
        attrs = vrayMtl['Attributes']
        if (value := attrs.get('gtr_energy_compensation', None)) is not None:
            if type(value) is bool:
                # Upgrade from the older version, set to the new default
                vrayMtl['Attributes']['gtr_energy_compensation'] = '1'

        if (opacity := attrs.get('opacity', None)) and (type(opacity) is str):
            # The VRayMtl node shows only the 'opacity_color' to which both color and float
            # inputs can be attached. The export code then determines whether to export 
            # 'opacity' or 'opacity_color' based on the type of the connected socket.
            # If the 'opacity' socket is connected in the imported material, reconnect the
            # link to the 'opacity_color' socket.
            attrs['opacity_color'] = opacity
            attrs['opacity_source'] = 1 # set opacity_color as the valid opacity source
            del attrs['opacity']



def importVRMesh(context, matPath, meshPath, lightPath="", operator=None, locationsMap=None, useRelPath=False):
    if not meshPath:
        return {'CANCELLED'}

    meshPath = os.path.normpath(meshPath)
    if not os.path.exists(meshPath):
        if operator:
            operator.report({'ERROR'}, "File not found!")
        return {'CANCELLED'}

    # Add new mesh object
    name = f'VRayProxy@{os.path.splitext(os.path.basename(meshPath))[0]}'

    mesh = bpy.data.meshes.new(name)
    ob = bpy.data.objects.new(name, mesh)
    ob.location = context.scene.cursor.location
    
    context.collection.objects.link(ob)


    # Add proxy
    if err := ProxyUtils.loadProxyPreviewMesh(ob, meshPath, 0, 0.0, 1.0, 0.0):
        if operator:
            operator.report({'ERROR'}, f"Error loading VRayProxy: {err}!")
        return {'CANCELLED'}

    # Rescaling the object based on length unit of the scene
    # In V-Ray the default unit is centimeters
    ob.scale *= getCmToSceneUnitsMultiplier(context)

    vrayAsset = ob.vray.VRayAsset
    vrayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Proxy"]
    vrayAsset.filePath = bpy.path.relpath(meshPath) if useRelPath else meshPath
    


    if os.path.exists(matPath):
        _importMaterials(context, matPath, 'STANDARD', objectForMatAssign = ob, locationsMap=locationsMap)

    blender_utils.selectObject(ob)
    
    if lightPath and os.path.exists(lightPath):
        _importLights(context, ob, lightPath, locationsMap)

    return {'FINISHED'}

# bpy datastructures modifying from another thread could crash Blender.
# This means that vrmat and vrmesh file importing can be done only from the main thread.
# For that reason 'assetImportTimerFunction' is registered as timer and waits until asset import data is 
# added to the 'assetImportQueue' from 'assetImportCallback' which is safe for execution from another thread
# This is the recommended by the  blender community way for dealing with this problem:
# https://docs.blender.org/api/current/bpy.app.timers.html#use-a-timer-to-react-to-events-in-another-thread
assetImportQueue = queue.Queue()

def assetImportTimerFunction():
    global assetImportQueue

    while not assetImportQueue.empty():
        assetType, matFile, objFile, lightFile, locationsMap = assetImportQueue.get()
        if assetType == "Material":
            _importMaterials(bpy.context, matFile, 'STANDARD', locationsMap=locationsMap)
        elif assetType == "VRMesh":
            importVRMesh(bpy.context, matFile, objFile, lightFile, locationsMap=locationsMap)
        elif assetType == "HDRI":
            _importHDRI(matFile, lightFile, locationsMap=locationsMap)

    
    return 1.0 # Timeout before the next invocation

def assetImportCallback(assetType, matFile, objFile, lightFile, locationsMap):
    global assetImportQueue
    assetImportQueue.put((assetType, matFile, objFile, lightFile, locationsMap))


def registerAssetImportTimerFunction():
    if not bpy.app.timers.is_registered(assetImportTimerFunction): 
        bpy.app.timers.register(assetImportTimerFunction)

def register():
    registerAssetImportTimerFunction()


def unregister():
    if bpy.app.timers.is_registered(assetImportTimerFunction): 
        bpy.app.timers.unregister(assetImportTimerFunction)