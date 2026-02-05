import bpy

from vray_blender.exporting.node_export import _exportConverters
from vray_blender.exporting.tools import getFarNodeLink
from vray_blender.lib.defs import AColor, NodeContext, PluginDesc
from vray_blender.lib.export_utils import exportPluginCommon, commonNodesExport
from vray_blender.lib.mixin import VRayNodeBase, VRayOperatorBase
from vray_blender.lib.names import Names
from vray_blender.nodes import color_ramp
from vray_blender.nodes.sockets import addInput, removeInputs, VRaySocket, VRaySocketAColor
from vray_blender.nodes.utils import selectedObjectTagUpdate


_TEX_SOCKET_PREFIX = 'Point '


def createColorRampNode(parentNode: bpy.types.Node, toSocket: bpy.types.NodeSocket):
    """Creates a Color Ramp node and connects it to its parent.

    Args:
        parentNode (Node): The parent node.
        toSocket (NodeSocket): The socket where the gradient ramp will connect.
    """
    nodeTree = parentNode.id_data

    # Create a new Color Ramp Node.
    rampNode = nodeTree.nodes.new(type=VRayNodeColorRamp.bl_idname)

    # Set location for the new node according to socket index and mouse position.
    rampSockIndices = {}
    index = 0
    for sock in parentNode.inputs:
        if sock.bl_idname == VRaySocketColorRamp.bl_idname:
            rampSockIndices[sock.identifier] = index
            index += 1

    # If parentNode's location is (0, 0) and the type is not NODE_EDITOR, the
    # node is created by scene loading or a script - use offset from parentNode.
    # If parentNode's location is not (0, 0) (Create button), use parentNode's location.
    pixelOffset = 300
    if bpy.context.space_data is not None:
        if (parentNode.location.x != 0 or parentNode.location.y != 0
            or bpy.context.space_data.type != "NODE_EDITOR"
        ):
            locationX = parentNode.location.x - pixelOffset
            locationY = parentNode.location.y - (
                rampSockIndices[toSocket.identifier] * pixelOffset)
        else:
            # If parentNode's location is (0, 0), and NODE_EDITOR is opened, the node
            # is being created by NodeInit, initiated by the user - use mouse position.
            mousePos = bpy.context.space_data.cursor_location
            locationX = mousePos.x - pixelOffset
            locationY = mousePos.y - (
                rampSockIndices[toSocket.identifier] * pixelOffset)
    else:
        locationX = locationY = 0.0

    rampNode.location = (locationX, locationY)

    # Prepare the ramp texture based on the node it was created from.
    rampTex = rampNode.texture
    if parentNode.vray_plugin == "TexGradRamp":
        _prepareRampForTexGradRamp(rampTex)
    elif parentNode.vray_plugin == "BRDFToonMtl":
        # toSocket.name comes from BRDFToonMtl.json.
        if toSocket.name == "Diffuse Ramp":
            _prepareRampForBRDFToonMtlDiffuse(rampTex)
        elif toSocket.name == "Specular Ramp":
            _prepareRampForBRDFToonMtlReflection(rampTex)

    fromSock = rampNode.outputs.get("Ramp")

    nodeTree.nodes.active = rampNode

    # Link the new node to the parent.
    nodeTree.links.new(fromSock, toSocket)

    # When a scene is loaded, parentNode's location is always (0, 0) at this point.
    # This timer reevaluates parentNode's position and moves Ramp node.
    def moveNode():
        """Callback for the timer. Can't use lambda as it needs to assign value.
        Moves the Ramp node relative to parentNode after parentNode location evaluation."""
        if toSocket.identifier not in rampSockIndices:
            return
        locationX = parentNode.location.x - pixelOffset
        locationY = parentNode.location.y - (
            rampSockIndices[toSocket.identifier] * pixelOffset)
        rampNode.location = (locationX, locationY)

    bpy.app.timers.register(moveNode)


def _prepareRampForTexGradRamp(tex: bpy.types.Texture):
    """Prepares the ramp for use with TexGradRamp. Uses default settings."""
    tex.color_ramp.color_mode = 'RGB'
    tex.color_ramp.interpolation = 'LINEAR'
    tex.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    tex.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)


def _prepareRampForBRDFToonMtlDiffuse(tex: bpy.types.Texture):
    """Prepares the ramp for use with BRDFToonMtl"""
    tex.color_ramp.interpolation = 'CONSTANT'
    tex.color_ramp.elements.new(0.33)
    tex.color_ramp.elements[2].position = 0.66
    tex.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    tex.color_ramp.elements[1].color = (0.5, 0.5, 0.5, 1.0)
    tex.color_ramp.elements[2].color = (1.0, 1.0, 1.0, 1.0)


def _prepareRampForBRDFToonMtlReflection(tex: bpy.types.Texture):
    """Prepares the ramp for use with BRDFToonMtl"""
    tex.color_ramp.interpolation = 'CONSTANT'
    tex.color_ramp.elements[1].position = 0.2
    tex.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    tex.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)


class VRaySocketColorRampTexture(VRaySocketAColor):
    """A custom class that represets each Color Ramp Point Texture"""
    bl_idname = 'VRaySocketColorRampTexture'
    bl_label  = 'Color Ramp Texture'

    def _onUpdate(self: bpy.types.NodeSocket, ctx: bpy.types.Context):
        """Runs on any change to the values"""
        selectedObjectTagUpdate(self, ctx)
        if self.node.texture:
            onUpdateColorRampSocket(self, self.node.texture)

    # Redefine the value property to assign a new update function
    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        size = 4,
        update = _onUpdate,
        default = AColor((1.0, 1.0, 1.0, 1.0))
    )

    def draw_impl(self, context, layout, node, text):
        """Executed by the draw() function."""
        if self.hasActiveFarLink():
            split = layout.split(factor=0.4)
            split.prop(self, 'value', text="")
            split.prop(self, 'multiplier', text=text)
        else:
            split = layout.split(factor=0.4)
            split.prop(self, 'value', text="")
            split.label(text=self._sockLabel())


class VRAY_OT_create_gradient_ramp_btn(VRayOperatorBase):
    """Represents the button for creating a new Gradient Ramp Node.
    The button is located on any node that has the VRaySocketColorRamp socket.
    It only appears if the socket is not connected."""
    bl_idname = "grad_ramp_node.create"
    bl_label = " Create Gradient Ramp Node Button"

    def execute(self, context):
        """Callback function for the button"""
        currNode = context.node
        toSocket = context.socket

        createColorRampNode(currNode, toSocket)

        return {'FINISHED'}


class VRaySocketColorRamp(VRaySocket):
    """Custom Color Ramp node socket. It can only connect to others of its kind."""
    bl_idname = 'VRaySocketColorRamp'
    bl_label = "Color Ramp Socket"

    # Optional function for drawing the socket input value
    def draw_impl(self, context, layout, node, text):
        """Executed by the draw() function."""
        # Only draw the label if it's an output socket
        if self.is_output:
            if text:
                layout.label(text=text)
            return

        row = layout.row()
        if text:
            sub1 = row.row()
            sub1.label(text=text)
        # Draw a "Create" button if the socket isn't connected.
        if not self.is_linked:
            sub2 = row.row()
            sub2.scale_x = 0.75
            sub2.operator("grad_ramp_node.create", text="Create")

    @classmethod
    def draw_color_simple(cls):
        """Socket color."""
        return (1.0, 0.4, 0.216, 0.5)


class VRayNodeColorRamp(VRayNodeBase):
    """Custom Color Ramp Node. Holds a fake texture to use as color ramp."""
    bl_idname = 'VRayNodeColorRamp'
    bl_label = "V-Ray Color Ramp"
    bl_icon = 'TEXTURE'

    vray_type = "NONE"
    vray_plugin = "NONE"

    def _onUpdateTexture(self, context):
        """Wrapper for the texture property to use."""
        _manageRampSockets(self, self.texture.color_ramp)

    texture: bpy.props.PointerProperty(
        name = "Texture",
        type = bpy.types.Texture,
        description = "Fake texture for internal usage",
        update = _onUpdateTexture
    )

    def init(self, context):
        """Creates the output socket and sockets for each ramp point element."""
        self._create_fake_texture()
        self.outputs.new(VRaySocketColorRamp.bl_idname, "Ramp")

        _manageRampSockets(self, self.texture.color_ramp)

    def copy(self, node: bpy.types.Node):
        """Initializes a copied node from an existing one.
        Args:
            self: The newly created node.
            node: The original node that this one is copied from.
        """
        def assignTexture():
            """Callback for the timer. Can't use lambda as it needs to assign value."""
            self.texture = node.texture.copy()
            color_ramp.registerColorRamp(self, 'texture', self.texture)
        bpy.app.timers.register(assignTexture)

    def free(self):
        """Clean up on removal."""
        color_ramp.unregisterColorRamp(self, 'texture', self.texture)
        self.texture.use_fake_user = False
        bpy.data.textures.remove(self.texture)

    def draw_buttons(self, context, layout):
        """Widgets displayed on the node."""
        # Needs some milliseconds for init. Errors out without the check.
        if self.texture:
            layout.label(text="Color Ramp")
            layout.template_color_ramp(self.texture, "color_ramp", expand=True)
            layout.label(text="Point Textures")

    def draw_buttons_ext(self, context, layout):
        """Widgets displayed in the side menu for the node. Uses draw_buttons if not defined."""
        layout.label(text="Color Ramp")
        layout.template_color_ramp(self.texture, "color_ramp")
        layout.label(text="Point Textures")
        for sock in self.inputs:
            layout.prop(sock, 'value', text=sock.identifier)

    def _create_fake_texture(self):
        """Prepares the gradient ramp texture parameters."""
        tex = color_ramp.createRampTexture(self, "texture")
        color_ramp.registerColorRamp(self, 'texture', tex)

    def exportGradTreeNode(self, nodeCtx: NodeContext):
        """
        Fills plugin color ramp attributes from bpy.types.ColorRamp
        and exports TexAColor plugins for the ramp stops.
        """
        outputSocket = None

        for sock in self.outputs:
            if sock.bl_idname == VRaySocketColorRamp.bl_idname:
                outputSocket = sock
                break

        # Node isn't connected.
        if not outputSocket.is_linked:
            return

        ramp: bpy.types.ColorRamp = self.texture.color_ramp

        colors = [] # diffuse, specular, etc. colors.
        positions = [] # diffuse, specular, etc. positions.
        elemNum = 0

        for elem in ramp.elements:
            colPluginName = f"{Names.node(self)}Pos{elemNum}"
            pos = elem.position

            texturePlugin = PluginDesc(colPluginName, "TexAColor")
            texturePlugin.setAttribute('texture', AColor(elem.color))

            colors.append(exportPluginCommon(nodeCtx.exporterCtx, texturePlugin))
            positions.append(pos)
            elemNum += 1

        interpolation = {
            'CONSTANT': 0,
            'LINEAR': 1,
            'EASE': 2,
            'CARDINAL': 3,
            'B_SPLINE': 4
        }.get(ramp.interpolation, 1)

        for sock in self.inputs:
            if nodeLink := getFarNodeLink(sock):
                texturePlugin = commonNodesExport.exportVRayNode(nodeCtx, nodeLink)
                texIndex = _sockNameToTexIndex(sock.name)
                colors[texIndex] = _exportConverters(nodeCtx, sock, texturePlugin)

        return colors, positions, interpolation


def _texIndexToSockName(i: int):
    return f"{_TEX_SOCKET_PREFIX}{i+1}"


def _sockNameToTexIndex(sockName: str):
    return int(sockName[len(_TEX_SOCKET_PREFIX):]) - 1


def registerColorRamps():
    """
    Called from the Load Post event handler.
    Adds all ColorRamp nodes to a list for sync. Used with Undo/Redo as well.
    """
    nodeTrees = (
        (bpy.data.materials, 'MATERIAL'),
        (bpy.data.worlds, 'WORLD'),
        (bpy.data.lights, 'LIGHT')
    )

    for tree in nodeTrees:
        for item in tree[0]:
            if not item.node_tree:
                continue
            for node in item.node_tree.nodes:
                if node.bl_idname == VRayNodeColorRamp.bl_idname:
                    color_ramp.registerColorRamp(node, 'texture', node.texture)


def syncColorRamps():
    """
    Called from Update Depsgraph Pre event handler.
    Manages the node sockets according to the ramp points.
    """
    for node, _, texture in color_ramp.COLOR_RAMPS.values():
        # If the node has a vray_plugin that is not NONE, it's not using gradient_ramp.
        # SoftboxTex is such case.
        if hasattr(node, 'vray_plugin') and node.vray_plugin == "NONE":
            _manageRampSockets(node, texture.color_ramp)


def _manageRampSockets(node: bpy.types.Node, ramp):
    """
    Modifies the number of sockets on the node and their values
    according to the ramp's points.
    """
    elemCount = len(ramp.elements)
    socketCount = len(node.inputs)

    if socketCount < elemCount:
        # Create sockets for any added ramp elements.
        for i in range(socketCount, elemCount):
            addInput(node, VRaySocketColorRampTexture.bl_idname, _texIndexToSockName(i))
    elif elemCount < socketCount:
        # Remove the sockets corresponding to removed ramp elements.
        sockNames = [_texIndexToSockName(i) for i in range(elemCount, socketCount)]
        removeInputs(node, sockNames, removeLinked=True)

    # Sync the colors between the ramp stops and the node sockets.
    for i in range(elemCount):
        sock = node.inputs[_texIndexToSockName(i)]

        if sock.value[:] != ramp.elements[i].color[:]:
            sock.value = ramp.elements[i].color[:]


def onUpdateColorRampSocket(socket, texture):
    """Update ramp colors from socket colors."""
    ramp = texture.color_ramp
    elemIndex = _sockNameToTexIndex(socket.name)
    if ramp.elements[elemIndex].color[:] != socket.value[:]:
        ramp.elements[elemIndex].color = socket.value[:]


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##


def register():
    """Register classes for Blender to recognize and use them."""
    bpy.utils.register_class(VRaySocketColorRampTexture)
    bpy.utils.register_class(VRAY_OT_create_gradient_ramp_btn)
    bpy.utils.register_class(VRaySocketColorRamp)
    bpy.utils.register_class(VRayNodeColorRamp)


def unregister():
    """Unregister classes from Blender to stop using them."""
    bpy.utils.unregister_class(VRaySocketColorRampTexture)
    bpy.utils.unregister_class(VRAY_OT_create_gradient_ramp_btn)
    bpy.utils.unregister_class(VRaySocketColorRamp)
    bpy.utils.unregister_class(VRayNodeColorRamp)
