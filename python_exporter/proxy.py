# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import os
from pathlib import PurePath

from bpy_extras.io_utils import ImportHelper

import bpy

from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.exporting import tools as export_tools
from vray_blender.lib.blender_utils import setFloatFrame, selectObject, getVRayPreferences
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib.draw_utils import rollout
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.ui.classes import pollEngine
from vray_blender.operators import VRAY_OT_message_box_base
from vray_blender.nodes.operators.import_file import importProxyFromMeshFile
from vray_blender.ui.community_edition import (
    drawCELimitedFeatureWarning,
    getCELimitedFeatureMsg,
    getLimitedFeatureDescription,
)
from vray_blender.lib.names import Names
from vray_blender.vray_tools import vray_proxy


VRAY_SCENE_FILTER_GLOB ="*.vrscene;*.usd;*.usda;*.usdc;*.usdz"
VRAY_PROXY_FILTER_GLOB ="*.vrmesh;*.abc"

class VRAY_OT_object_rotate_to_flip(VRayOperatorBase):
    bl_idname      = "vray.object_rotate_to_flip"
    bl_label       = "Rotate Object"
    bl_description = "Rotate object to flip axis"

    def execute(self, context):
        bpy.ops.transform.rotate(value=1.5708,
            axis=(1, 0, 0),
            constraint_axis=(True, False, False),
            constraint_orientation='GLOBAL',
            mirror=False,
            proportional='DISABLED'
        )

        return {'FINISHED'}


class VRAY_OT_vrayscene_load_preview(VRayOperatorBase):
    bl_idname      = "vray.vrayscene_load_preview"
    bl_label       = "Load VRayScene Preview"
    bl_description = "Load VRayScene preview from *.vrscene file"

    def execute(self, context):
        vrayScene = context.object.data.vray.VRayScene

        if err := vray_proxy.loadVRayScenePreviewMesh(vrayScene, vrayScene.filepath):
            debug.report('ERROR', err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_proxy_load_preview(VRayOperatorBase):
    bl_idname      = "vray.proxy_load_preview"
    bl_label       = "Load Preview"
    bl_description = "Load VRayProxy preview from .vrmesh or .abc file"

    def execute(self, context):
        geomMeshFile  = context.active_object.data.vray.GeomMeshFile
        proxyFilepath = bpy.path.abspath(geomMeshFile.file)

        if not proxyFilepath:
            self.report({'ERROR'}, "Proxy filepath is not set!")
            return {'CANCELLED'}

        if not os.path.exists(proxyFilepath):
            return {'CANCELLED'}

        err = vray_proxy.loadVRayProxyPreviewMesh(geomMeshFile, geomMeshFile.file, context.scene.frame_current - 1)

        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_proxy_generate_preview(VRayOperatorBase):
    bl_idname      = "vray.proxy_generate_preview"
    bl_label       = "Generate VRayProxy Preview"
    bl_description = "Generate preview mesh for a VRayProxy object and load it into the scene"

    def execute(self, context):
        geomMeshFile = context.object.data.vray.GeomMeshFile

        # Default the preview to the original mesh file
        previewFilePath = bpy.path.abspath(geomMeshFile.file)

        if not (os.path.exists(previewFilePath)):
            debug.reportError(f"File not found: {geomMeshFile.file} [resolves to {previewFilePath}]")
            return {'CANCELLED'}

        if err := vray_proxy.loadVRayProxyPreviewMesh(geomMeshFile, geomMeshFile.file, context.scene.frame_current):
            debug.reportError(err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_vrayscene_generate_preview(VRayOperatorBase):
    bl_idname      = "vray.vrayscene_generate_preview"
    bl_label       = "Generate VRayScene Preview"
    bl_description = "Generate preview mesh for a VRayScene object and load it into the scene"

    def execute(self, context):
        vrayScene = context.object.data.vray.VRayScene

        if not (sceneFilepath := bpy.path.abspath(vrayScene.filepath)):
            self.report({'ERROR'}, "Scene filepath is not set!")
            return {'CANCELLED'}

        if err := vray_proxy.loadVRayScenePreviewMesh(vrayScene, sceneFilepath):
            debug.report('ERROR', err)
            return {'CANCELLED'}

        return {'FINISHED'}



def _buildMeshFromProxyData(meshData, meshName, scale, existingMaterials):
    """ Create a Blender mesh from binary proxy mesh data.
        Uses foreach_set for fast bulk data transfer from numpy arrays.
        Returns the new bpy.data.meshes object, or None if the data is empty.
    """
    import numpy as np
    from mathutils import Matrix

    vertices = meshData.get('vertices')
    faces = meshData.get('faces')

    if vertices is None or faces is None or len(vertices) == 0 or len(faces) == 0:
        return None

    numFaces = len(faces)
    numLoops = numFaces * 3  # all faces are triangles

    from vray_blender.lib.mesh_build_utils import buildTriMeshBase
    mesh = buildTriMeshBase(meshName, vertices, faces)

    if scale != 1.0:
        mesh.transform(Matrix.Scale(scale, 4))

    materialIDs = meshData.get('material_ids')
   
    # Create material slots. Build a mapping from material ID → sequential slot index.
    materialIdToSlotIndex = {}
    # Make sure that there are no material slots that does not match a material ID.
    distinctMaterialIDs = set(materialIDs) if materialIDs is not None else set() 
    for slotIdx, matId in enumerate(distinctMaterialIDs):
        materialIdToSlotIndex[matId] = slotIdx
        if matId < len(existingMaterials):
            mesh.materials.append(existingMaterials[matId])
        else: # If the material ID is not found in the existing materials, add a None slot
            mesh.materials.append(None)

    # Set per-face material indices
    if materialIDs is not None and len(materialIDs) == numFaces:
        numSlots = len(mesh.materials)
        if numSlots > 0:
            if materialIdToSlotIndex:
                maxID = int(max(materialIdToSlotIndex))
                lut = np.zeros(maxID + 1, dtype=np.int32)
                for sid, idx in materialIdToSlotIndex.items():
                    lut[sid] = idx
                matArray = lut[np.clip(materialIDs, 0, maxID)]
            else:
                matArray = np.asarray(materialIDs, dtype=np.int32)

            np.clip(matArray, 0, numSlots - 1, out=matArray)
            attr = mesh.attributes.get("material_index") or mesh.attributes.new("material_index", 'INT', 'FACE')
            attr.data.foreach_set("value", matArray)

    mesh.update()

    # Create and populate UV layers (must be done after mesh.update())
    uvChannels = meshData.get('uv_channels')
    if uvChannels:
        channelCount = 0
        for uvChan in uvChannels:
            origIdx = uvChan['original_index']
            channelCount += 1
            
            if channelCount > 8:
                debug.report('WARNING',
                    f"UV channel count ({channelCount}) exceeds the supported maximum (8); "
                    "only the first 8 channels will be imported.")
                break;

            uvLayer = mesh.uv_layers.new(name=f"vray_channel_id_{origIdx}")

            uvCoords = uvChan['uv_coords']          # Nx2 float array
            uvFaceIndices = uvChan['uv_face_indices']  # flat uint32 array

            if not uvLayer:
                pass

            if len(uvFaceIndices) >= numLoops:
                loopUVs = np.ascontiguousarray(uvCoords[uvFaceIndices[:numLoops]], dtype=np.float32)
                uvLayer.uv.foreach_set('vector', loopUVs.ravel())

    # Apply custom split normals
    normals = meshData.get('normals')
    normalIndices = meshData.get('normal_indices')
    if normals is not None and normalIndices is not None and len(normalIndices) == numLoops:
        loopNormals = np.ascontiguousarray(normals[np.asarray(normalIndices)], dtype=np.float32)
        attr = mesh.attributes.new("custom_normal", 'FLOAT_VECTOR', 'CORNER')
        attr.data.foreach_set("vector", loopNormals.ravel())

    mesh.update()

    return mesh


def _buildProxyHierarchy(items, proxyObj, baseName, scale, existingMaterials):
    """Build a Blender object hierarchy from proxy mesh data.

    Mirrors the C4D importer: if an intermediate Empty already occupies a path when
    geometry arrives for that same path (because a child was serialised first), the
    Empty is replaced by the Mesh and its children are re-parented to it.

    A root Empty named after the asset is always created so all converted objects
    share a single top-level group in the outliner.
    """
    collections = list(proxyObj.users_collection)
    proxyParent = proxyObj.parent
    worldMatrix = proxyObj.matrix_world.copy()

    rootEmpty = bpy.data.objects.new(baseName, None)
    rootEmpty.empty_display_type = 'PLAIN_AXES'
    if proxyParent is not None:
        rootEmpty.parent = proxyParent
    rootEmpty.matrix_world = worldMatrix
    for coll in collections:
        coll.objects.link(rootEmpty)

    nodeCache = {}  # '/'-joined path -> bpy object

    def getOrCreateNode(pathParts):
        if not pathParts:
            return rootEmpty
        pathKey = '/'.join(pathParts)
        if pathKey in nodeCache:
            return nodeCache[pathKey]
        parentNode = getOrCreateNode(pathParts[:-1])
        emptyObj = bpy.data.objects.new(pathParts[-1], None)
        emptyObj.empty_display_type = 'PLAIN_AXES'
        if parentNode is not None:
            emptyObj.parent = parentNode
        emptyObj.matrix_world = worldMatrix
        for coll in collections:
            coll.objects.link(emptyObj)
        nodeCache[pathKey] = emptyObj
        return emptyObj

    created = []
    for objName, meshData in items:
        parts = [p for p in objName.split('/') if p] or [objName]
        leafName = parts[-1]
        pathKey = '/'.join(parts)
        leafParent = getOrCreateNode(parts[:-1])
        mesh = _buildMeshFromProxyData(meshData, leafName, scale, existingMaterials)
        if mesh is None:
            continue
        newObj = bpy.data.objects.new(leafName, mesh)
        existing = nodeCache.get(pathKey)
        if existing is not None and existing.type == 'EMPTY':
            for child in list(existing.users_children):
                child.parent = newObj
                child.matrix_world = worldMatrix
            for coll in collections:
                coll.objects.unlink(existing)
            bpy.data.objects.remove(existing)
        if leafParent is not None:
            newObj.parent = leafParent
        newObj.matrix_world = worldMatrix
        for coll in collections:
            coll.objects.link(newObj)
        newObj.select_set(True)
        nodeCache[pathKey] = newObj
        created.append(newObj)
    return created


class VRAY_OT_proxy_to_mesh(VRayOperatorBase):
    bl_idname      = "vray.proxy_to_mesh"
    bl_label       = "Convert Proxy to Mesh"
    bl_description = "Convert V-Ray Proxy to a full Blender mesh with materials and UV channels"
    bl_options     = {'UNDO'}

    # When set, convert this object instead of the active one (used by the Scene Lister).
    object_name: bpy.props.StringProperty(default="", options={'HIDDEN'})

    def execute(self, context):
        from vray_blender.exporting.tools import isObjectVrayProxy
        from vray_blender.vray_tools.vray_proxy import _dumpMeshFile, _PREVIEW_TYPES

        self.report({'WARNING'}, "Blender may be unresponsive during the conversion")

        ob = context.scene.objects.get(self.object_name) if self.object_name else context.active_object
        if ob is None or not isObjectVrayProxy(ob):
            self.report({'ERROR'}, "Active object is not a V-Ray Proxy")
            return {'CANCELLED'}

        geomMeshFile = ob.data.vray.GeomMeshFile
        absFilePath = bpy.path.abspath(geomMeshFile.file)

        if not os.path.exists(absFilePath):
            self.report({'ERROR'}, f"Proxy file not found: {absFilePath}")
            return {'CANCELLED'}

        # Dump full geometry with metadata (UVs, materials)
        from pathlib import PurePath
        from vray_blender.lib import path_utils
        binFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(absFilePath).stem).with_suffix('.vrbin'))

        if err := _dumpMeshFile(absFilePath, binFile, _PREVIEW_TYPES['FullWithMetadata'], 0, int(geomMeshFile.flip_axis)):
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        objects = vray_proxy.readBinMeshFile(binFile)
        os.unlink(binFile)

        if not objects:
            self.report({'ERROR'}, "No geometry data in proxy file")
            return {'CANCELLED'}

        scale = geomMeshFile.scale
        existingMaterials = list(ob.data.materials)

        proxyPrefix = "VRayProxy@"
        baseName = ob.name[len(proxyPrefix):] if ob.name.startswith(proxyPrefix) else ob.name

        items = list(objects.items())
        if len(items) == 1:
            # Single object: replace proxy mesh in-place to preserve Blender object identity.
            _, firstData = items[0]
            mesh = _buildMeshFromProxyData(firstData, baseName, scale, existingMaterials)
            if mesh is None:
                self.report({'ERROR'}, "Proxy file contains no geometry")
                return {'CANCELLED'}
            oldMesh = ob.data
            ob.data = mesh
            bpy.data.meshes.remove(oldMesh)
            ob.vray.VRayAsset.assetType = '0'
            ob.name = baseName
            debug.report('INFO', f"Converted proxy '{ob.name}' to mesh")
        else:
            created = _buildProxyHierarchy(items, ob, baseName, scale, existingMaterials)
            oldMesh = ob.data
            bpy.data.objects.remove(ob)
            bpy.data.meshes.remove(oldMesh)
            debug.report('INFO', f"Converted proxy to {len(created)} mesh objects")

        return {'FINISHED'}


class VRAY_OT_proxy_path_browser(bpy.types.Operator, ImportHelper):
    bl_idname = "vray.proxy_path_browser"
    bl_label = "Select File"
    bl_description = "Show a file browser for selecting the path to a V-Ray Proxy or V-Ray Scene compatibe file"

    mesh_name: bpy.props.StringProperty(default="", options={'HIDDEN'})
    filter_glob: bpy.props.StringProperty(default="", options={'HIDDEN'})
    
    relative_path: bpy.props.BoolProperty(
        name="Relative Path",
        description="Use Relative Path",
        default=True,
    )

    is_proxy: bpy.props.BoolProperty(
        default=True, 
        description="True for V-Ray Proxy, False for V-Ray Scene",
        options={'HIDDEN'}
    )

    filepath: bpy.props.StringProperty(subtype='FILE_PATH', default="")

    def execute(self, context):
        if self.is_proxy:
            bpy.data.meshes[self.mesh_name].vray.GeomMeshFile.file = self.filepath
        else:
            bpy.data.meshes[self.mesh_name].vray.VRayScene.filepath = self.filepath

        return {'FINISHED'}
    
    def invoke(self, context, event):
        # Set the initial path to the file browser
        if self.is_proxy:
            filePath = bpy.data.meshes[self.mesh_name].vray.GeomMeshFile.file
        else:
            filePath = bpy.data.meshes[self.mesh_name].vray.VRayScene.filepath
        
        absPath = bpy.path.abspath(filePath)
        if os.path.exists(absPath):
            self.filepath = absPath
        else:
            # Trying to set an invalid path to the file browser crashes Blender.
            # Set a fake name to just give user a hint
            self.filepath = ""

        self.filter_glob = VRAY_PROXY_FILTER_GLOB if self.is_proxy else VRAY_SCENE_FILTER_GLOB 

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


def _pollVrmeshDragDrop(cls, context: bpy.types.Context):
    return pollEngine(context) and context.space_data and context.space_data.type == 'VIEW_3D'


class VRAY_OT_import_drop_vrmesh(bpy.types.Operator):
    bl_idname = "vray.import_drop_vrmesh"
    bl_label = "Add V-Ray Proxy"
    bl_options = { 'INTERNAL', 'UNDO' }

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return _pollVrmeshDragDrop(cls, context)

    def execute(self, context):
        from pathlib import PurePath
        from vray_blender.nodes.operators.import_file import importProxyFromMeshFile
        imported = 0
        for file in self.files:
            filename = bpy.path.basename(file.name)
            filepath = os.path.join(self.directory, filename)
            filepath = bpy.path.abspath(filepath)
            if not os.path.exists(filepath):
                continue
            matPath = str(PurePath(filepath).with_suffix('.vrmat'))
            _, err = importProxyFromMeshFile(context, matPath, filepath)
            if err:
                self.report({'WARNING'}, err)
            else:
                imported += 1

        return {'FINISHED'} if imported else {'CANCELLED'}


class VRAY_FH_vrmesh_handler(bpy.types.FileHandler):
    bl_idname = "VRAY_FH_vrmesh_handler"
    bl_import_operator = "vray.import_drop_vrmesh"
    bl_file_extensions = ".vrmesh"
    bl_label = "V-Ray Proxy handler"

    @classmethod
    def poll_drop(cls, context):
        return _pollVrmeshDragDrop(cls, context)


def _objectsToRemoveAfterProxyExport(exporterCtx: ExporterContext) -> set[bpy.types.Object]:
    """ Original objects exported in the proxy main pass (same iteration as GeometryExporter._exportObjects).
    """
    if exporterCtx.proxyExportSettings.exportOnlySelected:
        objs = [obj for obj in exporterCtx.dg.objects if obj.original.select_get()]
    else:
        objs = exporterCtx.dg.objects

    return {obj.original for obj in objs if export_tools.isProxyConvertibleGeometryType(obj.original)}

def _buildProxyExportSettings(preferences):
    """Fills vray.ProxyExportSettings for .vrmesh export (maps to VRay::ProxyCreateParams)."""
    ps = vray.ProxyExportSettings()

    proxyPath = bpy.path.abspath(preferences.export_proxy_file_path).strip()
    if not proxyPath:
        raise ValueError("Proxy export path is empty.")

    if not proxyPath.lower().endswith('.vrmesh'):
        proxyPath = f"{proxyPath}.vrmesh"
        preferences.export_proxy_file_path = proxyPath

    outputDir = os.path.dirname(proxyPath)
    if outputDir and (not os.path.isdir(outputDir)):
        raise ValueError(f"Directory does not exist '{outputDir}'.")

    ps.filePath = proxyPath
    ps.elementsPerVoxel = preferences.export_proxy_elements_per_voxel
    ps.previewFaces = preferences.export_proxy_preview_faces
    ps.previewType = int(preferences.export_proxy_preview_type)
    ps.animOn = preferences.export_proxy_animation_range == 'FRAME_RANGE'
    ps.startFrame = preferences.export_proxy_start_frame
    ps.endFrame = preferences.export_proxy_end_frame
    return ps


def _appendProxyMaterialSlots(dataName: str, materialSlots, slotOffsets: dict[str, int], slotList: list[tuple[int, str | None]], slotOffset: int):
    slotOffsets[dataName] = slotOffset
    for localSlotIndex, slot in enumerate(materialSlots):
        mat = slot.material
        slotList.append((slotOffset + localSlotIndex, mat.name if mat else None))
    return slotOffset + len(materialSlots)


def _precomputeProxyMaterialSlots(exporterCtx: ExporterContext, exportOnlySelected: bool):
    dg = exporterCtx.dg
    slotOffset = 0
    slotOffsets: dict[str, int] = {}
    slotList: list[tuple[int, str | None]] = []

    for obj in dg.objects:
        if not export_tools.isProxyConvertibleGeometryType(obj):
            continue
        if exportOnlySelected and not obj.original.select_get():
            continue

        dataName = Names.objectData(obj)
        if dataName in slotOffsets:
            continue
        slotOffset = _appendProxyMaterialSlots(dataName, obj.material_slots, slotOffsets, slotList, slotOffset)

    return slotOffsets, slotList


def _setupProxyExporterContext(exporterCtx: ExporterContext, exportOnlySelected: bool):
    precomputedSlotOffsets, precomputedSlotList = _precomputeProxyMaterialSlots(exporterCtx, exportOnlySelected)
    exporterCtx.fullExport = True
    exporterCtx.syncSceneState()
    exporterCtx.syncActiveInstancers()
    exporterCtx.proxyExportSettings.exportOnlySelected = exportOnlySelected
    exporterCtx.proxyExportSettings.proxyMaterialSlotOffsets = dict(precomputedSlotOffsets or {})
    exporterCtx.proxyExportSettings.proxyMaterialSlots = list(precomputedSlotList or [])


def _proxyExportFrames(preferences, currentFrame: int) -> list[int]:
    if preferences.export_proxy_animation_range != 'FRAME_RANGE':
        return [int(currentFrame)]

    startFrame = int(preferences.export_proxy_start_frame)
    endFrame = int(preferences.export_proxy_end_frame)
    if endFrame < startFrame:
        raise ValueError("End frame must be greater than or equal to Start frame.")

    return list(range(startFrame, endFrame + 1))


def _buildProxyMaterialSlotList(exporterCtx: ExporterContext):
    return [
        {"slotIndex": slotIndex, "materialName": materialName}
        for slotIndex, materialName in sorted(exporterCtx.proxyExportSettings.proxyMaterialSlots, key=lambda item: item[0])
    ]


def _assignImportedProxyMaterials(proxyObject: bpy.types.Object, slotMaterialList: list[dict]):
    if not slotMaterialList:
        return

    materials = proxyObject.data.materials
    materials.clear()
    for slotInfo in slotMaterialList:
        name = slotInfo["materialName"]
        mat = bpy.data.materials.get(name) if name else None
        materials.append(mat)

def runProxyFileExport(scene: bpy.types.Scene, exporterCtx: ExporterContext, engine: bpy.types.RenderEngine) -> bool:
    """ Export a V-Ray proxy file and optionally reimports the proxy and removes the original objects.
    """
    
    context = exporterCtx.ctx
    renderer = exporterCtx.renderer
    preferences = getVRayPreferences(context)
    exportOnlySelected = preferences.export_proxy_scope == 'SELECTION'
    success = False

    originalLockInterface = scene.render.use_lock_interface
    originalFrame = scene.frame_current_final
    scene.render.use_lock_interface = True

    # Indicate if all necessary plugin exports were successful.
    pluginExportSuccessful = False

    try:
        proxySettings = _buildProxyExportSettings(preferences)
        exportFrames = _proxyExportFrames(preferences, scene.frame_current)

        if not renderer:
            raise ValueError("Failed to acquire V-Ray renderer for proxy export.")

        _setupProxyExporterContext(exporterCtx, exportOnlySelected)

        from vray_blender.exporting import obj_export, instancer_export
        obj_export.GeometryExporter(exporterCtx).syncObjVisibility()
        for frame in exportFrames:
            setFloatFrame(engine, frame)
            vray.setRenderFrame(renderer, frame)
            exporterCtx.syncSceneState()
            exporterCtx.syncActiveInstancers()
            geomExporter = obj_export.run(exporterCtx)
            instancer_export.run(exporterCtx, geomExporter, lightExporter=None)
            exporterCtx.fullExport = False

        pluginExportSuccessful = True
        vray.finishExport(renderer, False)

        ok, err = vray.exportProxyFile(renderer, proxySettings)
        if not ok:
            raise ValueError(err or "Proxy export failed.")

        importedProxy = None
        if preferences.export_proxy_add_to_scene:
            proxyPath = proxySettings.filePath
            matPath = str(PurePath(proxyPath).with_suffix('.vrmat'))
            importedProxy, importErr = importProxyFromMeshFile(context, matPath, proxyPath, useRelPath=False, scaleUnit=1.0, select=False)
            if importErr:
                engine.report({'WARNING'},f"Cannot import the currently exported V-Ray Proxy: {importErr}")
            elif importedProxy:
                proxyMaterialSlots = _buildProxyMaterialSlotList(exporterCtx)
                _assignImportedProxyMaterials(importedProxy, proxyMaterialSlots)

        if preferences.export_proxy_remove_exported_objects:
            for ob in _objectsToRemoveAfterProxyExport(exporterCtx):
                parent = ob.parent
                bpy.data.objects.remove(ob, do_unlink=True)
                if exportOnlySelected:
                    while parent:
                        oldParent = parent
                        parent = parent.parent
                        if oldParent.type == 'EMPTY' and not oldParent.children and oldParent.original.select_get():
                            bpy.data.objects.remove(oldParent, do_unlink=True)

        if importedProxy:
            selectObject(importedProxy)

        success = True

    except Exception as ex:
        engine.report({'ERROR'}, f"V-Ray Proxy export failed: {ex}")
    finally:
        
        # Ensure graceful finish if an exception interrupts the plugin export process.
        if not pluginExportSuccessful:
            vray.finishExport(renderer, False)

        setFloatFrame(scene, originalFrame)
        scene.render.use_lock_interface = originalLockInterface
        if success:
            engine.report({'INFO'}, f"Exported proxy: {preferences.export_proxy_file_path}")

# Reported when the export scope holds no object that can go into a .vrmesh file.
_NO_PROXY_OBJECTS_MSG = "No V-Ray proxy-convertible objects found for export."


def _hasProxyConvertibleObjects(context: bpy.types.Context):
    """ True if the objects in the configured export scope include at least one
        that can be exported to a .vrmesh file.
    """
    preferences = getVRayPreferences(context)
    exportOnlySelected = preferences.export_proxy_scope == 'SELECTION'
    scopeObjects = context.selected_objects if exportOnlySelected else context.scene.objects

    return any(export_tools.isProxyConvertibleGeometryType(obj) for obj in scopeObjects)


class VRAY_OT_export_vrmesh(VRAY_OT_message_box_base):
    bl_idname = "vray.export_vrmesh"
    bl_label = "Export V-Ray Proxy"
    bl_description = "Export mesh objects to a V-Ray Proxy (.vrmesh) file. \nV-Ray must be the active renderer for this command to be enabled."

    def execute(self, context):
        if vray.isCommunityEdition():
            self.report({'WARNING'}, getCELimitedFeatureMsg())
            return {'CANCELLED'}

        if not _hasProxyConvertibleObjects(context):
            self.report({'ERROR'}, _NO_PROXY_OBJECTS_MSG)
            return {'CANCELLED'}

        debug.report('INFO', 'Started V-Ray Proxy export. Blender UI will be unresponsive until the operation is complete.')
        
        # VfbEventHandler is used to export the proxy via a render engine because Scene.frame_set
        # does not trigger Depsgraph.updates between animation frames, while RenderEngine.frame_set does.
        # This approach avoids needing fullExport for each frame.
        # Additionally, this reuses ExporterContext and renderer creation logic from VRayRendererProdBase.

        from vray_blender.engine import vfb_event_handler
        vfb_event_handler.VfbEventHandler.exportProxy()

        return {'FINISHED'}

    def invoke(self, context, event):
        # Warn before showing the dialog so that the command behaves like the other object
        # commands when there is nothing to export. The CE upsell popup takes precedence.
        if (not vray.isCommunityEdition()) and (not _hasProxyConvertibleObjects(context)):
            self.report({'WARNING'}, _NO_PROXY_OBJECTS_MSG)
            return {'CANCELLED'}

        self._centerDialog(context, event)

        windowTitle = "Export V-Ray Proxy (.vrmesh)"
        if vray.isCommunityEdition():
            return context.window_manager.invoke_popup(self, width=400)
        return context.window_manager.invoke_props_dialog(self, width=480, title=windowTitle, confirm_text="Export")

    def draw(self, context):
        if vray.isCommunityEdition():
            drawCELimitedFeatureWarning(self.layout)
            return

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.active = not vray.isCommunityEdition()

        preferences = getVRayPreferences(context)

        row = layout.row(align=True)
        row.prop(preferences, 'export_proxy_file_path')
        row.operator('vray.select_proxy_export_file', text='', icon='FILE_FOLDER')

        body = rollout(layout, "VRAY_OT_export_vrmesh_export", "Export Settings", defaultClosed=False)
        if body:
            body.prop(preferences, 'export_proxy_scope')
            body.prop(preferences, 'export_proxy_add_to_scene')
            body.prop(preferences, 'export_proxy_remove_exported_objects')
            body.prop(preferences, 'export_proxy_elements_per_voxel')
            body.prop(preferences, 'export_proxy_preview_type')
            body.prop(preferences, 'export_proxy_preview_faces')

        body = rollout(layout, "VRAY_OT_export_vrmesh_anim", "Animation", defaultClosed=False)
        if body:
            body.prop(preferences, 'export_proxy_animation_range')
            col = body.column()
            col.enabled = preferences.export_proxy_animation_range == 'FRAME_RANGE'
            col.prop(preferences, 'export_proxy_start_frame')
            col.prop(preferences, 'export_proxy_end_frame')

        self._cursorWarp(context)

    @classmethod
    def description(cls, context, properties):
        return getLimitedFeatureDescription(cls.bl_description)


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_proxy_load_preview,
        VRAY_OT_proxy_generate_preview,
        VRAY_OT_proxy_to_mesh,
        VRAY_OT_proxy_path_browser,

        VRAY_OT_vrayscene_load_preview,
        VRAY_OT_vrayscene_generate_preview,
        VRAY_OT_object_rotate_to_flip,

        VRAY_OT_import_drop_vrmesh,
        VRAY_FH_vrmesh_handler,
        VRAY_OT_export_vrmesh,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
