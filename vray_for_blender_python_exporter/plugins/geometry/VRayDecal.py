# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import bmesh
import gpu
import math
import mathutils

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.exporting.tools import getFarNodeLink, getInputSocketByAttr, isObjectVRayDecal, MESH_OBJECT_TYPES
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.names import Names
from vray_blender.nodes.utils import getNodeByType, treeHasNodes

from gpu_extras.batch import batch_for_shader

plugin_utils.loadPluginOnModule(globals(), __name__)


def drawDecalAspectRatioButtons(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup: bpy.types.PropertyGroup, widgetAttr):
    split = layout.split(factor=0.3)
    split.column()

    col = split.column(align=True)
    col.operator('vray.fit_aspect_ratio_from_file', text="Aspect Ratio from Bitmap")
    col.operator('vray.fit_aspect_ratio', text="Aspect Ratio from Material").mode = 'material'
    col.operator('vray.fit_aspect_ratio', text="Aspect Ratio from Mask").mode = 'mask'
    col.operator('vray.fit_aspect_ratio', text="Aspect Ratio from Displacement").mode = 'displacement'


def getVRayDecalPluginName(obj: bpy.types.Object):
    assert isObjectVRayDecal(obj)
    return Names.pluginObject("vraydecal", Names.object(obj))


def isPluginVRayDecal(pluginName: str):
    return pluginName.startswith('vraydecal@')


def getDecalPropGroup(obj: bpy.types.Object):
    assert isObjectVRayDecal(obj)

    if treeHasNodes(obj.vray.ntree) and (outputNode := getNodeByType(obj.vray.ntree, 'VRayNodeDecalOutput')):
        return outputNode.VRayDecal
    return obj.data.vray.VRayDecal


def setAspectRatio(obj, img):
    if obj is None or obj.type != 'MESH' or not obj.vray.isVRayDecal:
        return
    if not img or img.size[0] == 0.0 or img.size[1] == 0.0:
        return

    propGroup = getDecalPropGroup(obj)

    aspectRatio = img.size[0] / img.size[1]
    propGroup.length = propGroup.width / aspectRatio

class VRAY_OT_fit_aspect_ratio_from_file(VRayOperatorBase):
    bl_idname = 'vray.fit_aspect_ratio_from_file'
    bl_options = { 'REGISTER', 'UNDO', 'INTERNAL' }
    bl_label = 'Fit Aspect Ratio to Bitmap'
    bl_description = 'Fit decal aspect ratio from a bitmap on disk'

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')

    filter_glob: bpy.props.StringProperty(
        default='*.png;*.bmp;*.tga;*.hdr;*.sgi;*.rgb;*.rgba;*.jpg;*.jpeg;*.jpe;*.exr;*.pic;*.tif;*.tiff;*.tx;*.tex;*.psd',
        options={'HIDDEN'}
    )

    def execute(self, context):
        if not self.filepath:
            self.report({'ERROR'}, 'No file selected')
            return {'CANCELLED'}

        try:
            img = bpy.data.images.load(self.filepath, check_existing=False)
        except Exception as e:
            self.report({'ERROR'}, f'Failed to load image: {e}')
            return {'CANCELLED'}

        setAspectRatio(context.object, img)

        bpy.data.images.remove(img)

        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


class VRAY_OT_fit_aspect_ratio(VRayOperatorBase):
    bl_idname = 'vray.fit_aspect_ratio'
    bl_options = { 'REGISTER', 'UNDO', 'INTERNAL' }
    bl_label = 'Fit Aspect Ratio'
    bl_description = 'Fit decal aspect ratio'

    mode: bpy.props.EnumProperty(default='mask', options={'HIDDEN'}, items=(
        ('material', 'material', "Fit decal aspect ratio to match that of the first eligible image in decal's material"),
        ('mask', 'mask', "Fit decal aspect ratio to match that of the decal's mask image"),
        ('displacement', 'displacement', "Fit decal aspect ratio to match that of the decal's displacement image"),
    ))

    @classmethod
    def description(cls, context, properties):
        return properties.bl_rna.properties['mode'].enum_items[properties.mode].description

    def execute(self, context):
        obj = context.object
        startNode = None
        if self.mode == 'material':
            if len(obj.data.materials)>0 and (material := obj.data.materials[0]) and material.use_nodes:
                startNode = getNodeByType(material.node_tree, 'VRayNodeOutputMaterial')
        elif treeHasNodes(obj.vray.ntree) and (outputNode := getNodeByType(obj.vray.ntree, 'VRayNodeDecalOutput')):
            decalOutputAttr = 'mask' if self.mode == 'mask' else 'displacement_tex_color'
            inputSocket = getInputSocketByAttr(outputNode, decalOutputAttr)
            if farLink := getFarNodeLink(inputSocket):
                startNode = farLink.from_node

        if not startNode:
            self.report({'WARNING'}, f'Could not set decal aspect ratio, no valid {self.mode} node')
            return { 'FINISHED' }

        if img := __class__._traverseNodesForImage(startNode):
            setAspectRatio(obj, img)
        else:
            self.report({'WARNING'}, f'Could not set decal aspect ratio, no valid {self.mode} image node found')

        return { 'FINISHED' }

    @staticmethod
    def _traverseNodesForImage(node: bpy.types.Node):
        """ Find an image node in a node tree rooted at 'node' """

        if node.bl_idname == 'VRayNodeMetaImageTexture':
            return node.texture.image
        elif node.bl_idname ==  'ShaderNodeTexImage':
            return node.image

        for input in node.inputs:
            if newSock := getFarNodeLink(input):
                if not newSock.from_node:
                    continue
                if resultImage := __class__._traverseNodesForImage(newSock.from_node):
                    return resultImage
        return None


class DecalBBoxGizmoGroup(bpy.types.GizmoGroup):
    bl_idname = 'OBJECT_GGT_decal_bbox'
    bl_label = 'Decal Bounding Box Gizmos'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'3D', 'PERSISTENT'}

    # Axes along which each gizmo point operates
    axesBox = [
        mathutils.Vector((1, 0, 0)),
        mathutils.Vector((-1, 0, 0)),
        mathutils.Vector((0, 1, 0)),
        mathutils.Vector((0, -1, 0)),
        mathutils.Vector((0, 0, 1)),
        mathutils.Vector((0, 0, 0)) # This point cannot be moved
    ]

    axesCylinder = [
        mathutils.Vector((1, 0, 0)),
        mathutils.Vector((-1, 0, 0)),
        mathutils.Vector((0, 1, 0)),
        mathutils.Vector((0, -1, 0)),
        mathutils.Vector((0, 0, 1)),
        mathutils.Vector((0, 0, -1))
    ]

    @classmethod
    def poll(cls, context):
        obj = context.active_object

        if not obj or obj.type != 'MESH' or not obj.vray.isVRayDecal or context.area.type != 'VIEW_3D':
            return False

        if not obj.visible_in_viewport_get(context.space_data) or not obj.select_get():
            return False

        vrayDecal = getDecalPropGroup(obj)

        if not vrayDecal.enabled:
            return False

        return True


    def setup(self, context):
        for _ in range(6):
            gizmo = self.gizmos.new('GIZMO_GT_move_3d')
            gizmo.draw_style = 'RING_2D'
            gizmo.use_draw_modal = True
            gizmo.color = (0.8, 0.3, 0.3)
            gizmo.alpha = 1
            gizmo.color_highlight = (1, 1, 1)
            gizmo.alpha_highlight = 1.0
            gizmo.draw_options = { 'ALIGN_VIEW', 'FILL' }
            self.undoDone = False

        def moveGetPos(index: int):
            """ Return gizmo point position calculated from the current decal object properties.
                Called by Blender when preview object's properties are updated.
            """
            vrayDecal = getDecalPropGroup(context.object)

            width = vrayDecal.width
            length = vrayDecal.length
            height = vrayDecal.height
            heightOffset = height * vrayDecal.height_offset

            hx, hy, hz = width / 2, length / 2, height / 2

            match vrayDecal.decal_type:
                case '0':
                    # Box
                    positions = [
                        mathutils.Vector(( hx, 0,  hz - heightOffset)),
                        mathutils.Vector((-hx, 0,  hz - heightOffset)),
                        mathutils.Vector((0,  hy, hz - heightOffset)),
                        mathutils.Vector((0, -hy, hz - heightOffset)),
                        mathutils.Vector((0, 0, height - heightOffset)),
                        mathutils.Vector((0, 0, 0))
                    ]
                case '1':
                    # Cylinder
                    innerRadius, outerRadius, startAngle, endAngle, heightOffset = _getCylinderParams(vrayDecal)

                    startPointPos = mathutils.Vector((outerRadius * math.sin(startAngle), 0, outerRadius * math.cos(startAngle) - innerRadius - heightOffset))
                    endPointPos = mathutils.Vector((outerRadius * math.sin(endAngle), 0, outerRadius * math.cos(endAngle) - innerRadius - heightOffset))

                    positions = [
                        startPointPos,
                        endPointPos,
                        mathutils.Vector((0,  hy, height - heightOffset)),
                        mathutils.Vector((0, -hy, height - heightOffset)),
                        mathutils.Vector((0, 0, height - heightOffset)),
                        mathutils.Vector((0, 0, vrayDecal.bend - 2 * math.pi))
                    ]
                case _:
                    assert False, f"Unknown decal type {vrayDecal.decal_type}"

            return positions[index]


        def moveSetPos(val: mathutils.Vector, index: int):
            """ Update object properties given the gizmo point position.
                Called by Blender when a gizmo's position changes in response to user action.
            """
            obj = context.object
            vrayDecal = getDecalPropGroup(obj)

            if not self.undoDone:
                bpy.ops.ed.undo_push(message='Decal resize')
                self.undoDone = True

            axes = self.axesBox if vrayDecal.decal_type == '0' else self.axesCylinder
            axis = axes[index]

            heightOffset = vrayDecal.height * vrayDecal.height_offset

            match vrayDecal.decal_type:
                case '0':
                    # Box
                    if axis[0]:
                        vrayDecal.width = val[0] * axis[0] * 2
                    elif axis[1]:
                        vrayDecal.length = val[1] * axis[1] * 2
                    elif axis[2]:
                        vrayDecal.height = val[2] * axis[2] + heightOffset

                    if index == 5:
                        # Handle the immovable control point.
                        # If none of the properties is updated, the gizmo is not redrawn
                        # at the correct position. Fake an update to force the redraw.
                        vrayDecal.width = vrayDecal.width
                case '1':
                    # Cylinder
                    if axis[0] == 1.0:
                        _, _, startAngle, _, heightOffset = _getCylinderParams(vrayDecal)
                        outerRadius = val[0] / math.sin(startAngle)
                        innerRadius = outerRadius - vrayDecal.height
                        vrayDecal.width = (innerRadius + heightOffset) * vrayDecal.bend
                    elif axis[0] == -1.0:
                        _, _, _, endAngle, heightOffset = _getCylinderParams(vrayDecal)
                        outerRadius = val[0] / math.sin(endAngle)
                        innerRadius = outerRadius - vrayDecal.height
                        vrayDecal.width = (innerRadius + heightOffset) * vrayDecal.bend
                    elif axis[1]:
                        vrayDecal.length = val[1] * axis[1] * 2
                    elif axis[2] == 1.0:
                        vrayDecal.height = val[2] * axis[2] + heightOffset
                    elif axis[2] == -1.0:
                        vrayDecal.bend = 2 * math.pi + val[2]
                case _:
                    assert False, f"Unknown decal type {vrayDecal.decal_type}"

        # Register get/set callbacks for gizmo positions
        for index, gizmo in enumerate(self.gizmos):
            gizmo.target_set_handler('offset', get=lambda index=index: moveGetPos(index), set=lambda val, index=index: moveSetPos(val, index))


    def invoke_prepare(self, context, gizmo):
        self.undoDone = False

    def draw_prepare(self, context: bpy.types.Context):
        obj = context.object
        if obj is None or obj.type != 'MESH' or not obj.vray.isVRayDecal:
            return

        for index, gizmo in enumerate(self.gizmos):
            mat = context.object.matrix_world.copy()
            gizmo = self.gizmos[index]
            gizmo.matrix_space = mat
            gizmo.scale_basis = 0.1 / mat.to_scale().length


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    vrayDecal = pluginDesc.vrayPropGroup

    # The plugin doesn't have a type parameter so reset the bend to 0.
    if vrayDecal.decal_type != '1':
        pluginDesc.setAttribute('bend', 0.0)

    # The plugin expects an actual percentage value.
    pluginDesc.setAttribute('height_offset', vrayDecal.height_offset * 100.0)

    # Enabled is also used to handle object visiblity.
    if pluginDesc.getAttribute('enabled') and vrayDecal.enabled:
        pluginDesc.setAttribute('enabled', True)
    else:
        pluginDesc.setAttribute('enabled', False)

    if vrayDecal.decal_object_selector.exportToPluginDesc(exporterCtx, pluginDesc):
        for attrPlugin in pluginDesc.getAttribute('exclusion_nodes'):
            vray.pluginCreate(exporterCtx.renderer, attrPlugin.name, 'Node')

    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)


def createCylinderPreviewMesh(objDecal: bpy.types.Object):
    vrayDecal = getDecalPropGroup(objDecal)
    innerRadius, outerRadius, startAngle, endAngle, heightOffset = _getCylinderParams(vrayDecal)

    bm = bmesh.new()

    SEGMENTS = 20
    vertsBottom, vertsTop = [], []

    for i in range(SEGMENTS+1):
        t = i / SEGMENTS
        angle = startAngle + (endAngle - startAngle) * t
        cosA = math.cos(angle)
        sinA = math.sin(angle)
        vertsBottom.append(bm.verts.new((outerRadius * sinA, -vrayDecal.length/2, outerRadius * cosA)))
        vertsTop.append(bm.verts.new((outerRadius * sinA, vrayDecal.length/2, outerRadius * cosA)))

    bm.verts.ensure_lookup_table()
    for i in range(SEGMENTS):
        bm.faces.new((vertsBottom[i], vertsBottom[i+1], vertsTop[i+1], vertsTop[i]))
    bm.transform(mathutils.Matrix.Translation([0, 0, -innerRadius - heightOffset]))
    bm.to_mesh(objDecal.data)
    bm.free()


def createBoxPreviewMesh(objDecal: bpy.types.Object):
    vrayDecal = getDecalPropGroup(objDecal)

    width, height, length, heightOffset = vrayDecal.width, vrayDecal.height, vrayDecal.length, vrayDecal.height * vrayDecal.height_offset
    hx, hy, hz = width / 2, length / 2, (height - heightOffset)
    newVerts = [(-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]

    bm = bmesh.new()
    for v in newVerts:
        bm.verts.new(v)
    bm.faces.new(bm.verts)
    bm.to_mesh(objDecal.data)


def createDecalObject(context: bpy.types.Context, name="VRayDecal"):
    decalMesh = bpy.data.meshes.new(name)
    decalObj = bpy.data.objects.new(name, decalMesh)
    decalObj.vray.isVRayDecal = True
    decalObj.location = context.scene.cursor.location
    decalObj.rotation_euler = [math.radians(90), 0, 0]

    context.collection.objects.link(decalObj)

    return decalObj


def linkSelectedObjects(context, decalObj: bpy.types.Object):
    # Add all eligible selected objects to the decal's object list.
    for obj in context.selected_objects:
        if obj.type in MESH_OBJECT_TYPES:
            decalObj.data.vray.VRayDecal.decal_object_selector.addListItem(context, obj)


def generateDecalPreviewMesh(obj: bpy.types.Object, propGroup):
    if (not obj) or (not isObjectVRayDecal(obj)):
        # This function is called from sites that pass context.active_object to it.
        # During decal object creation, the active object may not be the decal itself.
        return

    match propGroup.decal_type:
        case '0':
            createBoxPreviewMesh(obj)
        case '1':
            createCylinderPreviewMesh(obj)
        case _:
            assert False, f"Unknown decal type {propGroup.decal_type}"


def onUpdateDecalLockAspectRatio(propGroup, context: bpy.types.Context, attrName: str):
    assert attrName == 'lock_aspect_ratio'
    width, length = propGroup.width, propGroup.length
    propGroup.aspect_ratio = width/length if (width!=0.0 and length!=0.0) else 1.0

ALLOW_UPDATE_ASPECT_RATIO = True

def onUpdateDecalMesh(propGroup, context: bpy.types.Context, attrName: str):
    global ALLOW_UPDATE_ASPECT_RATIO
    width, length, aspectRatio = propGroup.width, propGroup.length, propGroup.aspect_ratio
    if ALLOW_UPDATE_ASPECT_RATIO and propGroup.lock_aspect_ratio:
        ALLOW_UPDATE_ASPECT_RATIO = False
        if attrName == 'width':
            propGroup.length = width / aspectRatio
        elif attrName == 'length':
            propGroup.width = length * aspectRatio

    ALLOW_UPDATE_ASPECT_RATIO = True
    generateDecalPreviewMesh(context.active_object, propGroup)


def drawDecalGizmoCallback():
    """ Draws the wireframe for the decal gizmo """

    obj = bpy.context.object
    if obj is None or getattr(obj, 'type', '') != 'MESH' or not obj.vray.isVRayDecal or not obj.select_get():
        return
    if not obj.visible_in_viewport_get(bpy.context.space_data):
        return

    vrayDecal = getDecalPropGroup(obj)

    width, height, length = vrayDecal.width, vrayDecal.height, vrayDecal.length
    heightOffset = vrayDecal.height * vrayDecal.height_offset
    vertsLocal, edges = [], []

    match vrayDecal.decal_type:
        case '0':
            # Box
            hx, hy, = width / 2, length / 2
            vertsLocal = [
                (-hx, -hy, -heightOffset), ( hx, -hy, -heightOffset), ( hx,  hy, -heightOffset), (-hx,  hy, -heightOffset),
                (-hx, -hy, height - heightOffset), ( hx, -hy,  height - heightOffset), ( hx,  hy,  height - heightOffset), (-hx,  hy,  height - heightOffset),
            ]

            edges = [
                (0,1), (1,2), (2,3), (3,0),
                (4,5), (5,6), (6,7), (7,4),
                (0,4), (1,5), (2,6), (3,7)
            ]

        case '1':
            # Cylinder
            innerRadius, outerRadius, startAngle, endAngle, heightOffset = _getCylinderParams(vrayDecal)

            SEGMENTS = 20
            for i in range(SEGMENTS+1):
                t = i / SEGMENTS
                angle = startAngle + (endAngle - startAngle) * t
                cosA = math.cos(angle)
                sinA = math.sin(angle)
                vertsLocal.append((innerRadius * sinA, -length/2, innerRadius * cosA - innerRadius - heightOffset))
                vertsLocal.append((innerRadius * sinA,  length/2, innerRadius * cosA - innerRadius - heightOffset))

            edges.append(((0, 1)))
            for i in range(0, SEGMENTS * 2, 2):
                edges.append((i + 0, i + 2))
                edges.append((i + 1, i + 3))
            edges.append(((SEGMENTS*2, SEGMENTS*2+1)))

            startSin, endSin = math.sin(startAngle), math.sin(endAngle)
            startCos, endCos = math.cos(startAngle), math.cos(endAngle)
            vertsLocal.append((outerRadius * startSin, -length / 2, outerRadius * startCos - innerRadius - heightOffset))
            vertsLocal.append((outerRadius * startSin,  length / 2, outerRadius * startCos - innerRadius - heightOffset))
            vertsLocal.append((outerRadius * endSin, -length / 2, outerRadius * endCos - innerRadius - heightOffset))
            vertsLocal.append((outerRadius * endSin,  length / 2, outerRadius * endCos - innerRadius - heightOffset))
            edges.append((0, SEGMENTS * 2 + 2))
            edges.append((1, SEGMENTS * 2 + 3))
            edges.append((SEGMENTS * 2 + 0, SEGMENTS * 2 + 4))
            edges.append((SEGMENTS * 2 + 1, SEGMENTS * 2 + 5))

        case _:
            assert False, f"Unknown decal type {vrayDecal.decal_type}"

    mw = obj.matrix_world
    verts = [mw @ mathutils.Vector(v) for v in vertsLocal]

    oldWidth = gpu.state.line_width_get()
    gpu.state.line_width_set(2)
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')

    batch = batch_for_shader(shader, 'LINES', {'pos': verts}, indices=edges)

    shader.bind()
    theme = bpy.context.preferences.themes[0]
    activeColor = theme.view_3d.object_active
    shader.uniform_float('color', (activeColor[0], activeColor[1], activeColor[2], 1.0))

    batch.draw(shader)
    gpu.state.line_width_set(oldWidth)

_drawHandler = None
_originalPolls = {}


def _getCylinderParams(vrayDecal: bpy.types.PropertyGroup):
    bend = max(vrayDecal.bend, 0.01)
    heightOffset = vrayDecal.height * vrayDecal.height_offset
    innerRadius = (vrayDecal.width / bend) - heightOffset
    outerRadius = innerRadius + vrayDecal.height
    if innerRadius < 0:
        innerRadius = 0
        heightOffset = vrayDecal.width / bend
    if outerRadius < 0:
        outerRadius = 0

    startAngle = -bend / 2.0
    endAngle = bend / 2.0

    return innerRadius, outerRadius, startAngle, endAngle, heightOffset


def _hidePanels():
    """ Remove the standard panels from Properties->Data for VRayDecal objects """

    for panel in bpy.types.Panel.__subclasses__():
        if getattr(panel, 'bl_context', None) == 'data':
            if not hasattr(panel, 'poll'):
                continue

            if panel in _originalPolls:
                continue

            originalPoll = panel.poll
            _originalPolls[panel] = originalPoll

            def makePoll(orig):
                def vrayPoll(cls, context):
                    obj = context.object
                    if obj and hasattr(obj, 'vray') and obj.vray.isVRayDecal:
                        return False
                    return orig(context)
                return vrayPoll

            panel.poll = classmethod(makePoll(originalPoll))


def _restorePanels():
    for panel, orig in _originalPolls.items():
        panel.poll = orig
    _originalPolls.clear()


def getRegClasses():
    return (
        DecalBBoxGizmoGroup,
        VRAY_OT_fit_aspect_ratio_from_file,
        VRAY_OT_fit_aspect_ratio
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)

    _hidePanels()

    global _drawHandler
    if _drawHandler is None:
        _drawHandler = bpy.types.SpaceView3D.draw_handler_add(
            drawDecalGizmoCallback,
            (),
            'WINDOW',
            'POST_VIEW'
        )

def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)

    _restorePanels()

    global _drawHandler
    if _drawHandler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_drawHandler, 'WINDOW')
        _drawHandler = None
