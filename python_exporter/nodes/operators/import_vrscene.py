# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" File -> Import -> V-Ray Scene (.vrscene).

    The scene file is parsed by the VRayZmqServer process (through the V-Ray SDK) and
    the plugin data is streamed back over a dedicated connection. This operator drives
    the round trip: it starts the native import session, shows progress while the
    server works, and applies the received data to the Blender scene in phases so the
    UI stays responsive and the operation can be cancelled with ESC.
"""

import os
import queue

import bpy
from bpy_extras.io_utils import ImportHelper

from vray_blender import debug, features
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.sys_utils import activeRendererExists
from vray_blender.nodes.utils import DisableAutoConnect

# Import status codes, mirroring VrayZmqWrapper::ImportStatus
_STATUS_OK = 0
_STATUS_PARSE_ERROR = 1
_STATUS_CANCELLED = 2
_STATUS_INTERNAL_ERROR = 3

# Wall-clock budget (seconds) for scene-build work per modal timer tick. The loop returns to
# the modal handler after this so Blender can process events (ESC cancel) between batches.
_APPLY_TIME_BUDGET = 0.04

# Percent of the bar the server's read+enumerate stage gets, the rest going to scene assembly.
# Measured on two ~350 MB scenes: 9.3 s of 35.9 s (26%) on ts_202423 and 3.9 s of 22.5 s (17%)
# on AE60_001_vlado. It was 50%, which put the halfway mark at a fifth of the elapsed time.
# A constant is the best available: the apply cost cannot be estimated until the plugin data
# has arrived, which is the moment this stage ends.
_SERVER_BAR_SHARE = 25


# Events from the native import callbacks, consumed by the modal operator's timer.
# The callbacks are invoked on a connection thread and must not touch bpy data.
_importEvents = queue.Queue()


def vrsceneImportProgressCallback(importId: int, pluginsDone: int, pluginsTotal: int, stage: str):
    _importEvents.put(('progress', importId, pluginsDone, pluginsTotal, stage))


def vrsceneImportFinishedCallback(importId: int, result):
    _importEvents.put(('finished', importId, result))


class VRAY_OT_import_vrscene(VRayOperatorBase, ImportHelper):
    bl_idname = "vray.import_vrscene"
    bl_label = "Import V-Ray Scene"
    bl_description = "Import a V-Ray scene (.vrscene) file.\nV-Ray must be the active renderer for this command to be enabled"
    bl_options = {'INTERNAL'}

    filename_ext = ".vrscene"

    filter_glob: bpy.props.StringProperty(
        default="*.vrscene",
        options={'HIDDEN'}
    )

    # Populated by the file-drop handler (VRAY_FH_vrscene_import) on a viewport drop.
    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE', 'HIDDEN'})

    import_mode: bpy.props.EnumProperty(
        name="Import As",
        description="How to bring the .vrscene into the scene",
        items=(
            ('OBJECTS', "Native Objects", "Import the scene contents as native Blender objects, materials and lights"),
            ('VRAYSCENE', "V-Ray Scene Reference", "Add a single V-Ray Scene object that references the file and is rendered at render time"),
        ),
        default='OBJECTS'
    )

    import_geometry: bpy.props.BoolProperty(
        name="Geometry",
        description="Import geometry and objects",
        default=True
    )

    import_materials: bpy.props.BoolProperty(
        name="Materials",
        description="Import materials and their assignments",
        default=True
    )

    import_lights: bpy.props.BoolProperty(
        name="Lights",
        description="Import lights",
        default=True
    )

    import_camera: bpy.props.BoolProperty(
        name="Camera",
        description="Import the camera and set it as the active scene camera",
        default=True
    )

    import_environment: bpy.props.BoolProperty(
        name="Environment",
        description="Import the environment as the scene's world",
        default=True
    )

    import_render_settings: bpy.props.BoolProperty(
        name="Render Settings",
        description="Apply the render settings from the imported scene",
        default=False
    )

    import_render_channels: bpy.props.BoolProperty(
        name="Render Elements",
        description="Import render elements/channels (denoiser, cryptomatte, etc.) into the world",
        default=False
    )

    import_vfb_layers: bpy.props.BoolProperty(
        name="VFB Layers",
        description="Replace the scene's V-Ray Frame Buffer correction layers with the ones from the imported file",
        default=False
    )

    import_instances: bpy.props.BoolProperty(
        name="Instances",
        description="Unpack V-Ray instancers into a point cloud with geometry-nodes instancing",
        default=True
    )

    import_hair: bpy.props.BoolProperty(
        name="Hair Curves",
        description="Import V-Ray hair (GeomMayaHair) as native Blender hair curves",
        default=True
    )

    create_collection: bpy.props.BoolProperty(
        name="Create Collection",
        description="Import the scene into a new collection named after the file. "
                    "When off, import into the active collection",
        default=True
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "import_mode", expand=True)

        box = layout.box()
        box.enabled = self.import_mode == 'OBJECTS'
        box.label(text="Import:")
        grid = box.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        grid.prop(self, "import_geometry")
        # Hair and instances are part of geometry; grey them out when geometry is off.
        hairCell = grid.row()
        hairCell.enabled = self.import_geometry
        hairCell.prop(self, "import_hair")
        grid.prop(self, "import_materials")
        grid.prop(self, "import_lights")
        grid.prop(self, "import_camera")
        grid.prop(self, "import_environment")
        grid.prop(self, "import_render_settings")
        grid.prop(self, "import_render_channels")
        grid.prop(self, "import_vfb_layers")
        instCell = grid.row()
        instCell.enabled = self.import_geometry
        instCell.prop(self, "import_instances")

        col = layout.column()
        col.enabled = self.import_mode == 'OBJECTS'
        col.prop(self, "create_collection")

    @classmethod
    def poll(cls, context):
        # Building a scene while an interactive render session is running would flood
        # it with per-datablock updates; require an idle renderer.
        return super().poll(context) and not activeRendererExists()

    def invoke(self, context, event):
        # Viewport drag-and-drop: the file-drop handler pre-fills directory/files.
        # Show the options dialog first (rather than importing immediately) so the
        # user can choose what to import.
        # 'directory' and 'files' are SKIP_SAVE, so they are set only by an actual drop and
        # never linger from a previous run - unlike 'filepath', which persists.
        if self.files or self.directory:
            self.filepath = self._droppedFilePath()
            return context.window_manager.invoke_props_dialog(self, width=400)

        # Menu invocation: show the file browser; options are drawn in its sidebar.
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def _droppedFilePath(self) -> str:
        """ The path of a dropped file.

            The drop handler fills 'filepath' with the full path and 'directory' + 'files'
            alongside it, but either can arrive Blender-relative ('//name.vrscene') - which
            os.path cannot read - so both are resolved and the one that names a real file wins.
        """
        candidates = [self.filepath]
        if self.directory and self.files:
            candidates.append(os.path.join(bpy.path.abspath(self.directory), self.files[0].name))

        for path in candidates:
            if path and os.path.isfile(resolved := bpy.path.abspath(path)):
                return resolved
        return self.filepath

    def execute(self, context):
        # A path typed or dropped in Blender-relative form ('//name.vrscene') has to be resolved
        # against the .blend before it can be opened.
        self.filepath = bpy.path.abspath(self.filepath)

        if not os.path.isfile(self.filepath):
            # Name what was actually received - a path that arrived relative to an unsaved .blend
            # cannot be resolved, and the raw inputs are the only way to tell that apart.
            detail = f"File does not exist: {self.filepath}"
            if self.directory or self.files:
                names = ", ".join(f.name for f in self.files)
                detail += f" (dropped directory: '{self.directory}', files: '{names}')"
            self.report({'ERROR'}, detail)
            return {'CANCELLED'}

        if self.import_mode == 'VRAYSCENE':
            # Reference the file as a single V-Ray Scene object rendered at render time;
            # no server round-trip / scene assembly needed.
            return self._importAsVRaySceneObject(context)

        self._importId = vray.importVrsceneStart(self.filepath)
        self._applyGen = None
        self._importer = None
        self._autoConnectGuard = None

        wm = context.window_manager
        wm.progress_begin(0, 100)
        self._setStatus(context, "V-Ray import: waiting for scene data...")

        # Block interaction with the scene while it is being built (the modal below
        # also swallows all input events), mirroring the Cosmos relink operator.
        context.window.cursor_modal_set('WAIT')

        # A short timer interval keeps the apply near full speed (each tick does ~_APPLY_TIME_BUDGET
        # of work) while still returning to the event loop often enough for a responsive ESC cancel.
        self._timer = wm.event_timer_add(0.02, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _importAsVRaySceneObject(self, context):
        """ Create a single V-Ray Scene reference object (rendered from the file at
            render time), mirroring vray.add_object_vrayscene. """
        from vray_blender.vray_tools import vray_proxy
        from vray_blender.lib import blender_utils

        name = f"VRayScene@{os.path.splitext(os.path.basename(self.filepath))[0]}"
        mesh = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, mesh)
        obj.location = context.scene.cursor.location
        context.scene.collection.objects.link(obj)

        obj.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Scene"]
        vrayScene = obj.data.vray.VRayScene

        if err := vray_proxy.loadVRayScenePreviewMesh(vrayScene, self.filepath):
            self.report({'ERROR'}, err)
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            return {'CANCELLED'}

        vrayScene['filepath'] = self.filepath
        blender_utils.selectObject(obj)
        self.report({'INFO'}, f"Added V-Ray Scene object for {os.path.basename(self.filepath)}")
        return {'FINISHED'}

    def modal(self, context, event):
        if event.type == 'ESC':
            return self._cancel(context)

        if event.type != 'TIMER':
            # Consume all input while the import is running.
            return {'RUNNING_MODAL'}

        try:
            if self._applyGen is not None:
                return self._advanceApply(context)
            return self._pollImportEvents(context)
        except Exception as e:
            debug.printExceptionInfo(e, "vray.import_vrscene")
            self.report({'ERROR'}, f"V-Ray scene import failed: {e}")
            return self._cancel(context, cancelServer=False)

    def _pollImportEvents(self, context):
        """ Waiting for the server: show progress, start the apply when data arrives. """
        while not _importEvents.empty():
            evt = _importEvents.get()

            if evt[1] != self._importId:
                continue   # Stale event from an abandoned session

            if evt[0] == 'progress':
                _, _, done, total, stage = evt
                if total > 0:
                    context.window_manager.progress_update(int(_SERVER_BAR_SHARE * done / total))
                    self._setStatus(context, f"V-Ray import: reading scene {done}/{total}")
                else:
                    self._setStatus(context, f"V-Ray import: {stage}")

            elif evt[0] == 'finished':
                result = evt[2]

                if result.status != _STATUS_OK:
                    self._reportImportError(result)
                    return self._cleanUp(context, 'CANCELLED', cancelServer=False)

                self._startApply(context, result)

        return {'RUNNING_MODAL'}

    def _startApply(self, context, result):
        """ Build the vrscene dict from the native data and start the phased scene build. """
        from vray_blender.vray_tools.vrscene_import import buildVrsceneDict
        from vray_blender.vray_tools.scene_import import SceneImporter, SceneImportOptions

        vrsceneDict = buildVrsceneDict(self._importId, self.filepath)

        options = SceneImportOptions(
            filePath=self.filepath,
            sceneBaseDir=result.sceneBaseDir,
            importGeometry=self.import_geometry,
            importMaterials=self.import_materials,
            importLights=self.import_lights,
            importCamera=self.import_camera,
            importEnvironment=self.import_environment,
            importRenderSettings=self.import_render_settings,
            importRenderChannels=self.import_render_channels,
            importVfbLayers=self.import_vfb_layers,
            importInstances=self.import_instances,
            importHair=self.import_hair,
            createCollection=self.create_collection,
        )

        self._autoConnectGuard = DisableAutoConnect()
        self._autoConnectGuard.__enter__()

        self._importer = SceneImporter(context, vrsceneDict, options)
        self._applyGen = self._importer.runPhases()

    def _advanceApply(self, context):
        """ Run scene-build steps for a short time budget per timer tick, then return so the
            modal loop can process events (ESC to cancel) between batches - the build runs on
            the main thread and cannot be interrupted mid-step, so per-step granularity is the
            finest cancellation point. Scene assembly occupies the bar above
            _SERVER_BAR_SHARE, which the server enumeration filled. """
        import time
        deadline = time.perf_counter() + _APPLY_TIME_BUDGET
        frac, phase = 0.0, ""
        try:
            while True:
                phase, frac = next(self._applyGen)
                if time.perf_counter() >= deadline:
                    break
        except StopIteration:
            return self._finish(context)
        percent = int(_SERVER_BAR_SHARE + (100 - _SERVER_BAR_SHARE) * frac)
        context.window_manager.progress_update(percent)
        self._setStatus(context, f"V-Ray import: {phase} ({percent}%)")
        return {'RUNNING_MODAL'}

    def _finish(self, context):
        stats = self._importer.stats
        bpy.ops.ed.undo_push(message=f"Import {os.path.basename(self.filepath)}")

        summary = stats.summary()
        self.report({'WARNING'} if stats.errors else {'INFO'}, f"V-Ray scene import: {summary}")
        debug.printInfo(f"V-Ray scene import of '{self.filepath}': {summary}")

        return self._cleanUp(context, 'FINISHED', cancelServer=False)

    def _cancel(self, context, cancelServer=True):
        if self._importer is not None:
            self._importer.rollback()
        return self._cleanUp(context, 'CANCELLED', cancelServer=cancelServer)

    def _cleanUp(self, context, status: str, cancelServer: bool):
        if cancelServer:
            vray.importVrsceneCancel(self._importId)
        vray.importVrsceneRelease(self._importId)

        if self._autoConnectGuard is not None:
            self._autoConnectGuard.__exit__(None, None, None)
            self._autoConnectGuard = None

        wm = context.window_manager
        wm.event_timer_remove(self._timer)
        wm.progress_end()
        context.window.cursor_modal_restore()
        self._setStatus(context, None)

        return {status}

    def _reportImportError(self, result):
        if result.status == _STATUS_PARSE_ERROR:
            location = f" at {result.errorFile}:{result.errorLine}" if result.errorFile else ""
            self.report({'ERROR'}, f"Failed to parse {self.filepath}{location}: {result.errorText}")
        elif result.status == _STATUS_CANCELLED:
            self.report({'INFO'}, "V-Ray scene import cancelled")
        else:
            self.report({'ERROR'}, f"V-Ray scene import failed: {result.errorText}")

    @staticmethod
    def _setStatus(context, text):
        # Append the cancel hint to live status messages (text is None only when clearing).
        context.workspace.status_text_set(f"{text}   (press ESC to cancel)" if text else text)


class VRAY_FH_vrscene_import(bpy.types.FileHandler):
    """ Enables dragging a .vrscene file onto the 3D viewport to import it. """
    bl_idname = "VRAY_FH_vrscene_import"
    bl_label = "V-Ray Scene"
    bl_import_operator = VRAY_OT_import_vrscene.bl_idname
    bl_file_extensions = ".vrscene"

    @classmethod
    def poll_drop(cls, context):
        return (context.area is not None
                and context.area.type == 'VIEW_3D'
                and VRAY_OT_import_vrscene.poll(context))


def _drawImportVrsceneMenuItem(self, context):
    layout = self.layout.column()
    layout.operator_context = 'INVOKE_DEFAULT'
    layout.operator(VRAY_OT_import_vrscene.bl_idname, text='V-Ray Scene (.vrscene)')


def register():
    if not features.isEnabled(features.Feature.VRSCENE_IMPORTER):
        return
    bpy.utils.register_class(VRAY_OT_import_vrscene)
    bpy.utils.register_class(VRAY_FH_vrscene_import)
    bpy.types.TOPBAR_MT_file_import.append(_drawImportVrsceneMenuItem)


def unregister():
    if not features.isEnabled(features.Feature.VRSCENE_IMPORTER):
        return
    bpy.types.TOPBAR_MT_file_import.remove(_drawImportVrsceneMenuItem)
    bpy.utils.unregister_class(VRAY_FH_vrscene_import)
    bpy.utils.unregister_class(VRAY_OT_import_vrscene)
