
import bpy

from vray_blender import debug
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.lib import lib_utils
from vray_blender.lib.defs import ProdRenderMode
from vray_blender.ui import classes


# Temporary cache for global settings that we need to change during the 
# bake render job. The values will be restored after the job is complete.
SettingsBackup = {}

def _valueBackup(propGroup, attrName):
    global SettingsBackup

    propGroupName = propGroup.bl_rna.name
    # Strip the 'VRay' prefix
    if (propGroupName.startswith('VRay')):
        propGroupName = propGroupName[len('VRay'):]

    if propGroupName not in SettingsBackup:
        SettingsBackup[propGroupName] = {}

    SettingsBackup[propGroupName][attrName] = getattr(propGroup, attrName)


def _valueRestore(propGroup, attrName):
    global SettingsBackup

    propGroupName = propGroup.bl_rna.name
    # Strip the 'VRay' prefix
    if (propGroupName.startswith('VRay')):
        propGroupName = propGroupName[len('VRay'):]

    setattr(propGroup, attrName, SettingsBackup[propGroupName][attrName])


def _restoreSettings(scene):
    global SettingsBackup

    VRayScene = scene.vray
    VRayScene.Exporter.wait = False

    for propGroupName in SettingsBackup:
        for attrName in SettingsBackup[propGroupName]:
            if hasattr(VRayScene, propGroupName):
                propGroup = getattr(VRayScene, propGroupName)
                if hasattr(propGroup, attrName):
                    setattr(propGroup, attrName, SettingsBackup[propGroupName][attrName])

    _valueRestore(VRayScene.Exporter, 'auto_save_render')

    VRayScene.BakeView.uv_channel = 0
    VRayScene.BakeView.bake_node  = ""

    SettingsBackup = {}


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


class VRAY_OT_batch_bake_add_selection(bpy.types.Operator):
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


class VRAY_OT_batch_bake(bpy.types.Operator):
    """ This operator runs a bake render job. In the UI, it is attached to the Bake button in the 
        'Bake' section.
    """  
    bl_idname      = "vray.batch_bake"
    bl_label       = "Batch Bake"
    bl_description = "Batch bake tool"

    def execute(self, context):
        vrayScene = context.scene.vray
        batchBake = vrayScene.BatchBake

        # Copy selection
        selection = [ob for ob in context.selected_objects]

        formatDict = lib_utils.getDefFormatDict()

        obList = None
        if batchBake.work_mode == 'SELECTION':
            obList = selection
        elif batchBake.work_mode == 'LIST':
            obList = [item.ob for item in batchBake.list_items if item.use and item.ob]

        numObjects = len(obList)

        # Backup some settings
        _valueBackup(vrayScene.Exporter, 'auto_save_render')

        if numObjects == 0:
            debug.printError("Bake texture failed: No object selected.")
            self.report({'ERROR'}, 'Bake texture failed: No object selected.')

        vrayScene.Exporter.auto_save_render = True

        try:
            for itemIndex, ob in enumerate(obList):
                debug.printInfo(f"Baking: {ob.name}...")
                
                if len(ob.data.uv_layers) == 0:
                    debug.printError(f"Bake texture: UV Map is not defined for object {ob.name_full}")
                    self.report({'ERROR'}, "Object has no UV map")
                    continue
                
                # Output path format is currently the same for all textures
                formatDict['$O'] = ("Object Name", lib_utils.cleanString(ob.name, stripSigns=False))

                # Fill the active item with values from either the object list or the
                # or the common properties depending on the object selection mode
                bakeItem = batchBake.active_item
                
                if batchBake.work_mode == 'LIST':
                    # Copy the properties from the current list item
                    dataSrc = batchBake.list_items[itemIndex]
                else:
                    dataSrc = batchBake.default_item
                
                bakeItem.ob             = ob
                bakeItem.width          = dataSrc.width
                bakeItem.height         = dataSrc.height
                bakeItem.dilation       = dataSrc.dilation
                bakeItem.flip_derivs    = dataSrc.flip_derivs
                bakeItem.img_file       = lib_utils.formatName(dataSrc.img_file, formatDict)
                bakeItem.img_dir        = lib_utils.formatName(dataSrc.img_dir,  formatDict)
                bakeItem.img_format     = dataSrc.img_format

                # Uv channel number does not seem to have any effect on the channel selection.
                # The channel is selected by name 
                vrayScene.BakeView.uv_channel = 0

                # Render
                # Set the bake 'active' flag for the duration of the render job(s) only.
                # This will tell the renderer that a bake job has been requested.
                vrayScene.Exporter.isBakeMode = True

                # Render the scene synchronously so that the settings changed for the duration
                # of the baking could be reset at the end of this function.
                VfbEventHandler.changeViewportModeSync(newMode='SOLID') 
                VfbEventHandler.startProdRenderSync(renderMode = ProdRenderMode.RENDER)
                

        except Exception as e:
            errMsg = f"Error baking object: {ob.name_full}"
            debug.printError(errMsg)
            debug.printExceptionInfo(e)
            self.report({'ERROR'}, errMsg)
        finally:
            vrayScene.Exporter.isBakeMode = False
            _restoreSettings(context.scene)

        # Restore selection
        for ob in selection:
            ob.select_set(True)

        return {'FINISHED'}


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
