# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import time

from vray_blender import debug
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.lib import lib_utils
from vray_blender.lib.defs import ProdRenderMode
from vray_blender.ui import classes
from vray_blender.lib.mixin import VRayOperatorBase



def _fixSquareResolution(self, context):
    vrayScene = context.scene.vray
    selectedItem = vrayScene.BatchBake.getSelectedItem()
    
    if selectedItem.square_resolution:
        selectedItem.height = selectedItem.width
    

class VRAY_UL_BakeRenderItem(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.prop(item, 'use', text="")
        sub = row.column()
        sub.active = item.use
        if item.ob:
            sub.label(text=item.ob.name, icon='OBJECT_DATA')
        else:
            sub.label(text="Object not found!", icon='ERROR')


class VRayBatchBakeItem(bpy.types.PropertyGroup):
    """ Holder for the bake-related settings which are set per object """
    ob : bpy.props.PointerProperty(
        type = bpy.types.Object
    )

    use: bpy.props.BoolProperty(
        default = True
    )

    width: bpy.props.IntProperty(
        name = "Width",
        default = 512,
        update = _fixSquareResolution
    )

    height: bpy.props.IntProperty(
        name = "Height",
        default = 512
    )

    dilation: bpy.props.FloatProperty(
        name = "Dilation",
        description = "Number of pixels to expand around the geometry",
        default = 2.0
    )

    flip_derivs: bpy.props.BoolProperty(
        name = "Flip derivatives",
        description = "Flip direction derivatives (reverse bump mapping)",
        default = False
    )

    square_resolution: bpy.props.BoolProperty(
        name = "Square resolution",
        description = "Output square texture",
        default = True,
        update = _fixSquareResolution
    )

    img_dir: bpy.props.StringProperty(
        name    = "Directory Path",
        subtype = 'DIR_PATH',
        default = "//bake_$F",
        description = "Output directory (Variables: %s; $O - Object name)" % lib_utils.formatVariablesDesc()
    )

    img_file: bpy.props.StringProperty(
        name    = "File Name",
        default = "bake_$O",
        description = "Output filename (Variables: %s; $O - Object name)" % lib_utils.formatVariablesDesc()
    )

    img_format: bpy.props.EnumProperty(
        name        = "Image format",
        description = "Image format",
        items       = (
            ('0',   'PNG',        '' ),
            ('1',   'JPEG',       '' ),
            ('2',   'TIFF',       '' ),
            ('3',   'TGA',        '' ),
            ('4',   'SGI',        '' ),
            ('5',   'OpenEXR',    '' ),
            ('6',   'VRayImage',  'V-Ray Image Format' )
        ),
        default     = '0'
    )
    

class VRayPropGroupMultiBake(bpy.types.PropertyGroup):
    """ Bake settings. This class' current values are also used as defaults for  
        the new objects added to the objects list in 'LIST' mode.
    """
    work_mode: bpy.props.EnumProperty(
        name        = "Mode",
        description = "Bake mode",
        items       = (
            ('SELECTION', "Selection", "Bake selected objects"),
            ('LIST',      "List",      "Bake objects from list"),
        ),
        default     = 'SELECTION'
    )


    # Properties holder for 'LIST' mode. Each object has its own properties' set.
    list_items: bpy.props.CollectionProperty(
        type = VRayBatchBakeItem
    )

    # Index of the item selected in the list box
    list_item_selected: bpy.props.IntProperty(
        default = -1,
        min     = -1,
        max     = 100
    )

    # Properties holder for 'SELECTION' mode. The same properties are
    # applied to all selected objects
    default_item: bpy.props.PointerProperty(
        type = VRayBatchBakeItem
    )

    # The batch item being currently rendered. This is set by the rendering procedure. Non-UI property. 
    active_item: bpy.props.PointerProperty(
        type = VRayBatchBakeItem
    )

    def getSelectedItem(self):
        if (self.work_mode == 'LIST') and (self.list_item_selected != -1 ):
            return self.list_items[self.list_item_selected]
        else:
            return self.default_item


class VRAY_OT_batch_bake_add_selection(VRayOperatorBase):
    bl_idname      = 'vray.batch_bake_add_selection'
    bl_label       = "Add Selection"
    bl_description = "Add selected objects to baking list"

    def execute(self, context):
        VRayScene = context.scene.vray
        BatchBake = VRayScene.BatchBake

        for ob in context.selected_objects:
            if ob.type not in {'MESH'}:
                continue

            if any(item.ob == ob for item in BatchBake.list_items): 
                # Skip the items that are already on the list
                continue

            item: VRayBatchBakeItem = BatchBake.list_items.add()
            dataSource = BatchBake.default_item

            item.ob                 = ob
            item.width              = dataSource.width
            item.height             = dataSource.height
            item.flip_derivs        = dataSource.flip_derivs
            item.square_resolution  = dataSource.square_resolution
            item.dilation           = dataSource.dilation
            item.img_file           = dataSource.img_file
            item.img_dir            = dataSource.img_dir
            item.img_format         = dataSource.img_format

        BatchBake.list_item_selected = len(BatchBake.list_items) - 1

        return {'FINISHED'}



class VRAY_PT_Bake(classes.VRayOutputPanel):
    """ Location: Render -> Render -> Bake  """

    bl_label = "Bake"
    bl_options = {'DEFAULT_CLOSED'}
    
    def draw(self, context):
        # The Bake panel has 2 modes: 
        #  - 'SELECTION', in which textures are baked for all objects selected in the viewport. 
        #       In this mode the same settings are applied for all objects 
        #  - 'LIST', in which the user adds specific objects to a list. In this mode different settings can
        #       be set for each object.
        layout = self.layout

        VRayScene = context.scene.vray
        BatchBake: VRayPropGroupMultiBake = VRayScene.BatchBake

        split = layout.split()
        row = split.row(align=True)
        row.operator("vray.batch_bake", icon='GROUP_UVS', text="Bake")
        row.prop(context.scene.render, "use_lock_interface", text="")
        layout.separator()

        layout.label(text="Mode:")
        layout.prop(BatchBake, 'work_mode', expand=True)

        if BatchBake.work_mode == 'LIST':
            layout.label(text="Objects To Bake:")
            classes.drawListWidget(layout, 'vray.BatchBake', 'VRAY_UL_BakeRenderItem',
                "Bake Object",
                itemAddOp='vray.batch_bake_add_selection')
        else:
            layout.separator()

        box = layout.box()
        box.label(text="Bake Settings:")
        split     = box.split()
        colLeft   = split.column()
        colRight  = split.column()
        
        dataSrc: VRayBatchBakeItem = BatchBake.getSelectedItem()

        # Show default property values
        colLeft.prop(dataSrc, 'width')
        
        row = colLeft.row()
        row.enabled = (not dataSrc.square_resolution)
        row.prop(dataSrc, 'height')
        
        colLeft.prop(dataSrc, 'dilation')
            
        colRight.prop(dataSrc, 'square_resolution')
        colRight.prop(dataSrc, 'flip_derivs')
        
        layout.separator()
        layout.prop(dataSrc, 'img_dir')
        layout.prop(dataSrc, 'img_file')
        layout.prop(dataSrc, 'img_format')


class VRAY_OT_batch_bake(VRayOperatorBase):
    """ This operator runs a bake render job for one or multiple scene objects.

        It implements both 'INVOKE_DEFAULT' and 'EXECUTE_DEFAULT' verbs. 
        * In 'INVOKE_DEFAULT' mode, it behaves as amodal operator. It will show the progress 
          in the UI and will handle cancellation requests by the user. 
        * 'EXECUTE_DEFAULT' is meant to be used in headless mode. It will carry out all tasks
          in blocking mode.
    """  
    bl_idname      = "vray.batch_bake"
    bl_label       = "Batch Bake"
    bl_description = "Batch bake tool"

    _timer = None
    _items = []
    _currentIndex = 0
    _waitingForRender = False

    # Store scene settings that need to be changed during the execution
    # and then restored back.
    _backupAutoSaveRender = False

    
    def invoke(self, context: bpy.types.Context, event):
        self._collectData(context)

        if not self._items:
            debug.report('ERROR', "Bake texture operation ont started: No object selected.")
            return {'CANCELLED'}
        
        # Report messages shown from the timer handler are delayed until the rendering
        # completes, so we need to show the one for the first object here
        obj = self._items[0][0]
        debug.report('INFO', f"Baking object {obj.name} [1 of {len(self._items)}] ...")

        self._updateSceneSettings(context)
        self._currentIndex = 0
        self._waitingForRender = False

        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)

        return {'RUNNING_MODAL'}


    def modal(self, context: bpy.types.Context, event: bpy.types.Event):
        match event.type:
            case 'TIMER':
                from vray_blender.engine.render_engine import VRayRenderEngine
                from vray_blender.engine.renderer_prod import VRayRendererProd
                
                isRendering = VRayRendererProd.isActive() or bpy.app.is_job_running('RENDER')
                
                if self._waitingForRender and (not isRendering):
                    if VRayRenderEngine.prodRenderer and VRayRenderEngine.prodRenderer.isAborted():
                        self._finish(context)
                        return {'CANCELLED'}
                    
                    # Render finished, move to the next item
                    self._waitingForRender = False
                    self._currentIndex += 1
                    
                if self._waitingForRender:
                    # Still rendering
                    return {'PASS_THROUGH'}
                
                totalObjs = len(self._items)
                
                if self._currentIndex < totalObjs:
                    obj, dataSrc = self._items[self._currentIndex]
                    
                    if self._currentIndex < totalObjs - 1:
                        # Blender will show this message after the render job completes so print it
                        # for the next object in the list. 
                        nextIndex = self._currentIndex + 1
                        nextObj = self._items[nextIndex][0]
                        debug.report('INFO', f"Baking object {nextObj.name} [{nextIndex + 1} of {totalObjs}] ...")

                    if self._startBake(context, obj, dataSrc, block=False):
                        self._waitingForRender = True
                    else:
                        # Rendering the current item failed, move to the next one 
                        self._currentIndex += 1
                else:
                    self._finish(context)
                    debug.report('INFO', 'Bake job finished')
                    return {'FINISHED'}
                    
            case 'ESC':
                self._finish(context)
                return {'CANCELLED'}
             
        return {'PASS_THROUGH'}


    def execute(self, context: bpy.types.Context):

        self._collectData(context)

        if len(self._items) == 0:
            self.report({'WARNING'}, "Bake texture failed: No object selected.")
            return {'CANCELLED'}
        
        self._updateSceneSettings(context)

        for item in self._items:
            try:
                obj, dataSrc = item
                self._startBake(context, obj, dataSrc, block = True)

            except Exception as e:
                errMsg = f"Error baking object {obj.name_full}"
                debug.printExceptionInfo(e, "VRAY_OT_batch_bake")
                self.report({'ERROR'}, errMsg)
            
        self._restoreSettings(context)

        return {'FINISHED'}
             

    def _startBake(self, context: bpy.types.Context, obj: bpy.types.Object, dataSrc: bpy.types.PropertyGroup, block: bool):
        vrayScene = context.scene.vray
        batchBake = vrayScene.BatchBake
        
        debug.printInfo(f"Baking: {obj.name}...")
        
        if len(obj.data.uv_layers) == 0:
            self.report({'ERROR'}, f"Bake texture: UV Map is not defined for object {obj.name_full}")
            return False
            
        formatDict = lib_utils.getDefFormatDict()
        formatDict['$O'] = ("Object Name", lib_utils.cleanString(obj.name, stripSigns=False))
        
        bakeItem = batchBake.active_item
        bakeItem.ob             = obj
        bakeItem.width          = dataSrc.width
        bakeItem.height         = dataSrc.height
        bakeItem.dilation       = dataSrc.dilation
        bakeItem.flip_derivs    = dataSrc.flip_derivs
        bakeItem.img_file       = lib_utils.formatName(dataSrc.img_file, formatDict)
        bakeItem.img_dir        = lib_utils.formatName(dataSrc.img_dir,  formatDict)
        bakeItem.img_format     = dataSrc.img_format
        
        vrayScene.BakeView.uv_channel = 0
        vrayScene.Exporter.isBakeMode = True
        
        VfbEventHandler.changeViewportModeSync(newMode='SOLID') 
        VfbEventHandler.startProdRenderSync(renderMode = ProdRenderMode.RENDER, block=block)
        
        return True
    

    def _finish(self, context: bpy.types.Context):
        wm = context.window_manager
        if self._timer:
            wm.event_timer_remove(self._timer)
            self._timer = None
        
        self._restoreSettings(context)


    def _collectData(self, context: bpy.types.Context):
        batchBake = context.scene.vray.BatchBake

        self._items = []
        
        if batchBake.work_mode == 'SELECTION':
             for ob in [ob for ob in context.selected_objects]:
                 self._items.append((ob, batchBake.default_item))
        elif batchBake.work_mode == 'LIST':
             for item in batchBake.list_items:
                 if item.use and item.ob:
                     self._items.append((item.ob, item))


    def _updateSceneSettings(self, context: bpy.types.Context):
        vrayScene = context.scene.vray

        self._backupAutoSaveRender = vrayScene.Exporter.auto_save_render
        vrayScene.Exporter.auto_save_render = True


    def _restoreSettings(self, context: bpy.types.Context):
        """ Restore scene settings at the end of a Bake render job. """
        vrayScene = context.scene.vray

        vrayScene.Exporter.auto_save_render = self._backupAutoSaveRender
        vrayScene.Exporter.isBakeMode = False
        vrayScene.BakeView.uv_channel = 0
        vrayScene.BakeView.bake_node  = ""



########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRayBatchBakeItem,
        VRAY_UL_BakeRenderItem,
        VRAY_OT_batch_bake_add_selection,
        VRayPropGroupMultiBake,
        VRAY_OT_batch_bake,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)

    from vray_blender import plugins

    plugins.VRayScene.BatchBake = bpy.props.PointerProperty(
        name        = "V-Ray Batch Bake Settings",
        type        =  VRayPropGroupMultiBake,
        description = "V-Ray batch bake settings"
    )



def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
