# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" The shared geometry-nodes instancing group.

    One versioned group ("ChaosScatterInstances") is shared by every scatter object; all
    per-object variance flows through modifier inputs. The group instances the prototype /
    proxy collections from the baked point attributes (position built-in, orientation, scale,
    proto_index, mask - same convention as vray_blender's .vrscene instancer import).

    Preview modes (viewport only - the render branch is ALWAYS full geometry at 100% density,
    so Cycles/EEVEE renders never depend on display settings):
      0 None, 1 Dots, 2 Boxes, 3 Wire Boxes, 4 Full
"""

import bpy


NODE_GROUP_NAME = "ChaosScatterInstances"
# The Dots branch differs per carrier flavour, and a .blend can hold both (a 4.5-authored MESH
# carrier opened in 5.1+), so each flavour gets its own group rather than one shared graph.
MESH_NODE_GROUP_NAME = "ChaosScatterInstancesMesh"
# Bump whenever _buildNodes changes: the version check short-circuits before anything else, so a
# group left at the old number is returned as-is. v3 = the per-carrier-flavour split above (a v2
# group in an existing .blend was baked from a build-time probe and may be the wrong flavour).
_NG_VERSION = 3

# Point attributes on the carrier. apply.applyScatterResult writes them and the node
# group below reads them, so both sides use these constants rather than the strings.
# Blender's own "position" is built in and needs no name.
ATTR_PROTO_INDEX = "proto_index"   # which prototype collection to instance
ATTR_MASK        = "mask"          # per-point on/off, set by the scatter core
ATTR_SCALE       = "scale"         # per-instance scale
ATTR_ORIENTATION = "orientation"   # per-instance rotation (quaternion)

# Modifier input names (group interface sockets)
IN_PROTOTYPES  = "Prototypes"
IN_BOX_PROXIES = "Box Proxies"
IN_WIRE_PROXIES = "Wire Proxies"
IN_PREVIEW_MODE = "Preview Mode"
IN_DISPLAY_PERCENTAGE = "Display Percentage"
IN_DISPLAY_LIMIT = "Display Limit"
IN_DOT_SIZE = "Dot Size"


PROXY_GROUP_NAME = "ChaosScatterProxyGeometry"
IN_PROXY_SOURCE = "Source"
_PROXY_NG_VERSION = 1


def getOrCreateProxyNodeGroup() -> bpy.types.NodeTree:
    """ The group every full-geometry preview proxy runs: pull the model's EVALUATED geometry in
        its own local space.

        Object Info goes through the depsgraph, so the model's modifier stack IS applied and mesh
        edits are live - the proxy holds no copy. transform_space='ORIGINAL' keeps the geometry in
        the model's local space; the proxy object's own matrix supplies the preserved rotation and
        scale (see lifecycle._ensureFullProxy), which is what keeps the scatter core's per-point
        quaternion+scale attributes shear-free.
    """
    ng = bpy.data.node_groups.get(PROXY_GROUP_NAME)
    if ng is not None:
        if ng.get('cscatter_version') == _PROXY_NG_VERSION:
            return ng
        ng.nodes.clear()
    else:
        ng = bpy.data.node_groups.new(PROXY_GROUP_NAME, 'GeometryNodeTree')

    ng.use_fake_user = True
    if not len(ng.interface.items_tree):
        # A Geometry input has to exist for the group to be usable as a modifier; it is left
        # unconnected on purpose - the proxy's own (empty) geometry is discarded.
        ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
        ng.interface.new_socket(IN_PROXY_SOURCE, in_out='INPUT', socket_type='NodeSocketObject')
        ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    ng['cscatter_version'] = _PROXY_NG_VERSION

    nodes, links = ng.nodes, ng.links
    groupIn = nodes.new("NodeGroupInput")
    groupOut = nodes.new("NodeGroupOutput")
    groupIn.location = (-260, 0)
    groupOut.location = (220, 0)

    info = nodes.new("GeometryNodeObjectInfo")
    info.transform_space = 'ORIGINAL'
    info.inputs["As Instance"].default_value = False
    links.new(groupIn.outputs[IN_PROXY_SOURCE], info.inputs["Object"])
    links.new(info.outputs["Geometry"], groupOut.inputs["Geometry"])
    return ng


def getOrCreateScatterNodeGroup(obj) -> bpy.types.NodeTree:
    meshCarrier = isinstance(obj.data, bpy.types.Mesh)
    groupName = MESH_NODE_GROUP_NAME if meshCarrier else NODE_GROUP_NAME

    ng = bpy.data.node_groups.get(groupName)
    if ng is not None:
        if ng.get('cscatter_version') == _NG_VERSION:
            return ng
        # Version bump: rebuild the internal graph in place but KEEP the interface, so the
        # socket identifiers stay stable and existing modifiers' input mappings survive.
        ng.nodes.clear()
    else:
        ng = bpy.data.node_groups.new(groupName, 'GeometryNodeTree')

    ng.use_fake_user = True
    if not len(ng.interface.items_tree):
        _buildInterface(ng)
    ng['cscatter_version'] = _NG_VERSION
    _buildNodes(ng, meshCarrier)
    _arrange(ng)
    return ng


def _arrange(ng: bpy.types.NodeTree):
    """ Lay the graph out. Every node is created at (0, 0), so without this the group is an
        unreadable pile the moment anyone opens it.

        Optional, and deliberately swallowing everything: this is the only V-Ray code the addon
        needs for something purely cosmetic, and it is on the object-creation path
        (getOrCreateScatterNodeGroup <- lifecycle.createScatterObject <- chaos_scatter.add). The
        import is not cheap or safe either - vray_blender/__init__.py loads the native extension
        and its whole module graph at module level, so a package that is merely installed and not
        enabled gets pulled in wholesale, and a broken one raises something other than ImportError.
        None of that may stop a scatter object being created; see the decoupling contract in
        chaos_scatter/__init__.py.
    """
    try:
        from vray_blender.nodes.tools import rearrangeTree
    except Exception:
        return
    groupOut = next((n for n in ng.nodes if n.type == 'GROUP_OUTPUT'), None)
    if groupOut is not None:
        rearrangeTree(ng, groupOut)


def inputIdentifier(ng: bpy.types.NodeTree, inputName: str) -> str | None:
    """ Stable interface identifier of a group input, for modifier value assignment. """
    for item in ng.interface.items_tree:
        if getattr(item, 'in_out', None) == 'INPUT' and item.name == inputName:
            return item.identifier
    return None


def _buildInterface(ng: bpy.types.NodeTree):
    iface = ng.interface
    iface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    iface.new_socket(IN_PROTOTYPES, in_out='INPUT', socket_type='NodeSocketCollection')
    iface.new_socket(IN_BOX_PROXIES, in_out='INPUT', socket_type='NodeSocketCollection')
    iface.new_socket(IN_WIRE_PROXIES, in_out='INPUT', socket_type='NodeSocketCollection')

    sockMode = iface.new_socket(IN_PREVIEW_MODE, in_out='INPUT', socket_type='NodeSocketInt')
    sockMode.default_value = 4
    sockMode.min_value = 0
    sockMode.max_value = 4

    sockPct = iface.new_socket(IN_DISPLAY_PERCENTAGE, in_out='INPUT', socket_type='NodeSocketFloat')
    sockPct.default_value = 100.0
    sockPct.min_value = 0.0
    sockPct.max_value = 100.0

    sockLimit = iface.new_socket(IN_DISPLAY_LIMIT, in_out='INPUT', socket_type='NodeSocketInt')
    sockLimit.default_value = 2000000
    sockLimit.min_value = 0

    sockDot = iface.new_socket(IN_DOT_SIZE, in_out='INPUT', socket_type='NodeSocketFloat')
    sockDot.default_value = 0.02
    sockDot.min_value = 0.0

    iface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')


def _buildNodes(ng: bpy.types.NodeTree, meshCarrier: bool):
    nodes, links = ng.nodes, ng.links
    groupIn = nodes.new("NodeGroupInput")
    groupOut = nodes.new("NodeGroupOutput")

    def namedAttr(attrName, dataType):
        node = nodes.new("GeometryNodeInputNamedAttribute")
        node.data_type = dataType
        node.inputs["Name"].default_value = attrName
        return node

    naIndex = namedAttr(ATTR_PROTO_INDEX, 'INT')
    naMask = namedAttr(ATTR_MASK, 'BOOLEAN')
    naScale = namedAttr(ATTR_SCALE, 'FLOAT_VECTOR')
    naRotation = namedAttr(ATTR_ORIENTATION, 'QUATERNION')

    # ---- Viewport selection: mask AND random(pct) AND index < limit ----
    pointIndex = nodes.new("GeometryNodeInputIndex")

    pctNorm = nodes.new("ShaderNodeMath")
    pctNorm.operation = 'DIVIDE'
    pctNorm.inputs[1].default_value = 100.0
    links.new(groupIn.outputs[IN_DISPLAY_PERCENTAGE], pctNorm.inputs[0])

    randomValue = nodes.new("FunctionNodeRandomValue")
    randomValue.data_type = 'FLOAT'
    randomValue.inputs["Min"].default_value = 0.0
    randomValue.inputs["Max"].default_value = 1.0
    links.new(pointIndex.outputs["Index"], randomValue.inputs["ID"])

    randomPass = nodes.new("FunctionNodeCompare")
    randomPass.data_type = 'FLOAT'
    randomPass.operation = 'LESS_EQUAL'
    links.new(randomValue.outputs["Value"], randomPass.inputs["A"])
    links.new(pctNorm.outputs["Value"], randomPass.inputs["B"])

    limitPass = nodes.new("FunctionNodeCompare")
    limitPass.data_type = 'INT'
    limitPass.operation = 'LESS_THAN'
    links.new(pointIndex.outputs["Index"], limitPass.inputs["A"])
    links.new(groupIn.outputs[IN_DISPLAY_LIMIT], limitPass.inputs["B"])

    andRandom = nodes.new("FunctionNodeBooleanMath")
    andRandom.operation = 'AND'
    links.new(naMask.outputs["Attribute"], andRandom.inputs[0])
    links.new(randomPass.outputs["Result"], andRandom.inputs[1])

    viewSelection = nodes.new("FunctionNodeBooleanMath")
    viewSelection.operation = 'AND'
    links.new(andRandom.outputs["Boolean"], viewSelection.inputs[0])
    links.new(limitPass.outputs["Result"], viewSelection.inputs[1])

    # ---- Instance branches ----
    def collectionInfo(inputName):
        node = nodes.new("GeometryNodeCollectionInfo")
        node.transform_space = 'ORIGINAL'
        node.inputs["Separate Children"].default_value = True
        # Reset Children instances each prototype's LOCAL geometry (its object transform is
        # ignored). The per-instance transform from the scatter core carries the placement and,
        # when "Preserve" is on, the model's own rotation/scale - so the model transform is
        # applied exactly once and never offset by the model's world position.
        node.inputs["Reset Children"].default_value = True
        links.new(groupIn.outputs[inputName], node.inputs["Collection"])
        return node

    def instanceOnPoints(collectionInfoNode, selectionOutput):
        node = nodes.new("GeometryNodeInstanceOnPoints")
        node.inputs["Pick Instance"].default_value = True
        links.new(groupIn.outputs["Geometry"], node.inputs["Points"])
        links.new(selectionOutput, node.inputs["Selection"])
        links.new(collectionInfoNode.outputs["Instances"], node.inputs["Instance"])
        links.new(naIndex.outputs["Attribute"], node.inputs["Instance Index"])
        links.new(naScale.outputs["Attribute"], node.inputs["Scale"])
        links.new(naRotation.outputs["Attribute"], node.inputs["Rotation"])
        return node

    ciProtos = collectionInfo(IN_PROTOTYPES)
    ciBoxes = collectionInfo(IN_BOX_PROXIES)
    ciWires = collectionInfo(IN_WIRE_PROXIES)

    fullRender = instanceOnPoints(ciProtos, naMask.outputs["Attribute"])
    fullView = instanceOnPoints(ciProtos, viewSelection.outputs["Boolean"])
    boxView = instanceOnPoints(ciBoxes, viewSelection.outputs["Boolean"])
    wireView = instanceOnPoints(ciWires, viewSelection.outputs["Boolean"])

    # ---- Dots branch: the selected points themselves, with a radius ----
    dotPoints = nodes.new("GeometryNodeSeparateGeometry")
    dotPoints.domain = 'POINT'
    links.new(groupIn.outputs["Geometry"], dotPoints.inputs["Geometry"])
    links.new(viewSelection.outputs["Boolean"], dotPoints.inputs["Selection"])

    if meshCarrier:
        # Mesh carrier: Set Point Radius only affects point clouds, so convert the vertices to
        # points; Mesh to Points carries its own radius input.
        meshToPoints = nodes.new("GeometryNodeMeshToPoints")
        links.new(dotPoints.outputs["Selection"], meshToPoints.inputs["Mesh"])
        links.new(groupIn.outputs[IN_DOT_SIZE], meshToPoints.inputs["Radius"])
        dotsOutput = meshToPoints.outputs["Points"]
    else:
        dotRadius = nodes.new("GeometryNodeSetPointRadius")
        links.new(dotPoints.outputs["Selection"], dotRadius.inputs["Points"])
        links.new(groupIn.outputs[IN_DOT_SIZE], dotRadius.inputs["Radius"])
        dotsOutput = dotRadius.outputs["Points"]

    # ---- Viewport mode select ----
    modeSwitch = nodes.new("GeometryNodeIndexSwitch")
    modeSwitch.data_type = 'GEOMETRY'
    while len(modeSwitch.index_switch_items) < 5:
        modeSwitch.index_switch_items.new()
    links.new(groupIn.outputs[IN_PREVIEW_MODE], modeSwitch.inputs["Index"])
    # inputs[0] is Index; items follow in order: 0 None (unconnected = empty geometry),
    # 1 Dots, 2 Boxes, 3 Wire Boxes, 4 Full
    links.new(dotsOutput, modeSwitch.inputs[2])
    links.new(boxView.outputs["Instances"], modeSwitch.inputs[3])
    links.new(wireView.outputs["Instances"], modeSwitch.inputs[4])
    links.new(fullView.outputs["Instances"], modeSwitch.inputs[5])

    # ---- Render always gets the full, unfiltered instances ----
    isViewport = nodes.new("GeometryNodeIsViewport")
    finalSwitch = nodes.new("GeometryNodeSwitch")
    finalSwitch.input_type = 'GEOMETRY'
    links.new(isViewport.outputs["Is Viewport"], finalSwitch.inputs["Switch"])
    links.new(fullRender.outputs["Instances"], finalSwitch.inputs["False"])
    links.new(modeSwitch.outputs["Output"], finalSwitch.inputs["True"])

    links.new(finalSwitch.outputs["Output"], groupOut.inputs["Geometry"])
