# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Scatter object lifecycle: creation, prototype collections, display proxies, duplication
    and orphan cleanup.

    Data layout (all keyed by the object's curve_ns so renames are harmless):

    "ChaosScatter Data"                 hidden root collection (collection-level hide flags
      cs.<ns>.protos / .NN              ONLY - object-level hide flags would drop the contents
      cs.<ns>.boxes  / .NN              from the depsgraph and empty the Collection Info nodes)
      cs.<ns>.wires  / .NN

    Model objects are LINKED into their proto collections, never moved - they stay members of
    whatever collections the user keeps them in. proto_NN sub-collection order == the model
    list order == the proto_index/topo values baked into the point cloud.
"""

import uuid
import bpy

from chaos_scatter import nodegroup, params, resolve, utils


ROOT_COLLECTION_NAME = "ChaosScatter Data"
MODIFIER_NAME = "ChaosScatter"
_PROXY_BASE_MESH = "cs.proxyBase"
_PROXY_MODIFIER = "ChaosScatterProxy"


def _dataKey(curveNs: str, kind: str) -> str:
    return f"cs.{curveNs}.{kind}"


def _isGeneratedProxy(name: str) -> bool:
    """ A box/wire/full proxy object this module owns, by name. """
    return name.startswith("cs.") and ".obj." in name


def _getRootCollection(create=False):
    root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
    if (root is None) and create:
        root = bpy.data.collections.new(ROOT_COLLECTION_NAME)
        root.hide_viewport = True
        root.hide_render = True
    if (root is not None) and (root.name not in bpy.context.scene.collection.children):
        try:
            bpy.context.scene.collection.children.link(root)
        except RuntimeError:
            pass  # already linked elsewhere in this scene
    return root


def createScatterObject(context) -> bpy.types.Object:
    """ Create a scatter carrier: empty PointCloud + the shared GN modifier. The selected
        geometry objects at invoke time become the initial distribution targets.
    """
    if utils.pointCloudResizable():
        carrier = bpy.data.pointclouds.new("ChaosScatter")
    else:
        # Pre-5.1 cannot size a PointCloud from Python; carry the points on a vertices-only Mesh
        carrier = bpy.data.meshes.new("ChaosScatter")
    obj = bpy.data.objects.new("ChaosScatter", carrier)
    context.collection.objects.link(obj)

    cs = obj.chaos_scatter
    cs.is_scatter = True
    cs.curve_ns = uuid.uuid4().hex[:12]

    from chaos_scatter import curves
    for slot in curves.SLOTS:
        curves.ensureCurveNode(obj, slot)

    targets = resolve.selectedTargets(context)
    for selObj in targets:
        item = cs.targets.add()
        item.object = selObj

    # A selection of nothing but face-less curves starts in 1D mode; anything carrying a surface
    # keeps the 2D default
    depsgraph = context.evaluated_depsgraph_get()
    if targets and all(resolve.isSplineTarget(o, depsgraph) for o in targets):
        cs.scatter_type = '0'

    mod = obj.modifiers.new(MODIFIER_NAME, 'NODES')
    mod.node_group = nodegroup.getOrCreateScatterNodeGroup(obj)

    rebuildProtoCollections(obj)
    syncModifierInputs(obj)

    return obj


def _getScatterModifier(obj):
    mod = obj.modifiers.get(MODIFIER_NAME)
    if (mod is None) or (mod.type != 'NODES'):
        # The user may have renamed it; fall back to any modifier using one of our groups
        for m in obj.modifiers:
            if m.type == 'NODES' and m.node_group is not None \
                    and m.node_group.name in (nodegroup.NODE_GROUP_NAME,
                                              nodegroup.MESH_NODE_GROUP_NAME):
                return m
    return mod


def _setModifierInput(mod, identifier: str, value):
    """ Assign one input of a geometry-nodes modifier.

        Blender 5.2 replaced the modifier's own ID properties with a typed interface, where each
        input is a struct holding a 'value' (see Blender's bl_operators/geometry_nodes.py and
        object_quick_effects.py). Before 5.2 the values are ID properties of the modifier itself.
        Only valid while mod.node_group is set - 'properties' is None without a node group.
    """
    if (properties := getattr(mod, 'properties', None)) is not None:
        getattr(properties.inputs, identifier).value = value
    else:
        mod[identifier] = value


def syncModifierNodeGroup(obj):
    """ Refresh the shared instancing group and bind the modifier to the one matching this
        carrier's datablock flavour (PointCloud vs Mesh - they need different Dots branches).
    """
    ng = nodegroup.getOrCreateScatterNodeGroup(obj)
    mod = _getScatterModifier(obj)
    if mod is not None and mod.node_group is not ng:
        mod.node_group = ng
        rebuildProtoCollections(obj)
        syncModifierInputs(obj)


def syncModifierInputs(obj):
    """ Push the display settings into the GN modifier inputs (no recompute). """
    mod = _getScatterModifier(obj)
    if mod is None or mod.node_group is None:
        return
    ng = mod.node_group
    display = obj.chaos_scatter.display

    def setInput(name, value):
        if identifier := nodegroup.inputIdentifier(ng, name):
            _setModifierInput(mod, identifier, value)

    # Non-V-Ray engines (e.g. Cycles) render the GN preview itself, so the viewport must
    # always instance the full geometry; V-Ray renders GeomScatter natively and respects
    # the user's lightweight preview choice.
    previewMode = int(display.preview_mode) if utils.isVRayEngine() else 4
    setInput(nodegroup.IN_PREVIEW_MODE, previewMode)
    setInput(nodegroup.IN_DISPLAY_PERCENTAGE, display.display_percentage)
    setInput(nodegroup.IN_DISPLAY_LIMIT, display.display_limit)
    setInput(nodegroup.IN_DOT_SIZE, display.dot_size)

    obj.update_tag(refresh={'DATA'})


def rebuildProtoCollections(obj, previewModels=None, depsgraph=None, childMap=None):
    """ Rebuild the prototype/proxy collection trees to match the model list. Idempotent.

        previewModels is _submit's already-resolved resolveModels(expandHierarchy=False) list;
        omitted, it is resolved here. depsgraph is likewise _submit's - see refreshProxyMeshes,
        which must not fetch one of its own after this function has dirtied it. childMap is
        _submit's parent->child map, reused rather than rebuilt per hierarchy expansion.
    """
    cs = obj.chaos_scatter
    if not cs.curve_ns:
        return

    root = _getRootCollection(create=True)
    ns = cs.curve_ns

    protosColl = _ensureChildCollection(root, _dataKey(ns, "protos"))
    boxesColl = _ensureChildCollection(root, _dataKey(ns, "boxes"))
    wiresColl = _ensureChildCollection(root, _dataKey(ns, "wires"))

    # SAME list the preview request is built from: proto sub-collection order IS the
    # proto_index the server bakes into the points, so the two must not diverge.
    if previewModels is None:
        previewModels = resolve.resolveModels(cs, expandHierarchy=False, childMap=childMap)

    _syncProtoChildren(protosColl, ns, "protos", len(previewModels))
    _syncProtoChildren(boxesColl, ns, "boxes", len(previewModels))
    _syncProtoChildren(wiresColl, ns, "wires", len(previewModels))

    # Before the proto loop below, which falls back to the wire proxy of a model that has no
    # geometry of its own.
    refreshProxyMeshes(obj, previewModels, depsgraph, childMap)

    # The render expands each model item into its whole geometry hierarchy; mirror that here so
    # Full preview mode shows the same objects the render will scatter. Grouped by itemIndex, so
    # the proto sub-collection order still matches the preview's model list (== proto_index).
    membersByItem = {}
    for m in resolve.resolveModels(cs, expandHierarchy=True, childMap=childMap):
        membersByItem.setdefault(m.itemIndex, []).append(m.object)

    for i, entry in enumerate(previewModels):
        protoChild = protosColl.children[f"{_dataKey(ns, 'protos')}.{i:02d}"]
        # Proxies rather than the real objects, because a real object's world transform would
        # land on top of the scatter placement. Each proxy sits at the preserved rotation and
        # scale combined with its offset from the root, so the hierarchy keeps its layout.
        rootInv = entry.object.matrix_world.inverted_safe()
        preserved = params.preservedLinearMatrix(cs, entry.object)
        proxies = [_ensureFullProxy(ns, i, j, member, preserved @ (rootInv @ member.matrix_world))
                   for j, member in enumerate(membersByItem[entry.itemIndex])
                   if member.type != 'LIGHT']
        if not proxies:
            # A light-only model has nothing for Object Info to pull, so Full mode would draw
            # nothing. Share the wire box instead; it stays linked in the wires collection, so
            # _syncLinkedObjects will not reap it.
            wireProxy = bpy.data.objects.get(f"{_dataKey(ns, 'wires')}.obj.{i:02d}")
            proxies = [wireProxy] if wireProxy is not None else []
        _syncLinkedObjects(protoChild, proxies)

    # Wire the collections into the modifier
    mod = _getScatterModifier(obj)
    if mod is not None and mod.node_group is not None:
        ng = mod.node_group
        for inputName, coll in ((nodegroup.IN_PROTOTYPES, protosColl),
                                (nodegroup.IN_BOX_PROXIES, boxesColl),
                                (nodegroup.IN_WIRE_PROXIES, wiresColl)):
            if identifier := nodegroup.inputIdentifier(ng, inputName):
                _setModifierInput(mod, identifier, coll)

    obj.update_tag(refresh={'DATA'})


def _proxyBaseMesh():
    """ One empty mesh shared by every proxy - the geometry-nodes modifier replaces it. """
    return bpy.data.meshes.get(_PROXY_BASE_MESH) or bpy.data.meshes.new(_PROXY_BASE_MESH)


def _ensureFullProxy(ns: str, index: int, subIndex: int, sourceObj, matrix):
    """ Create/update one full-geometry preview prototype for a model.

        The proxy holds no geometry: a geometry-nodes modifier pulls sourceObj's EVALUATED
        geometry through Object Info, so modifiers apply and edits stay live with no copy.
        Instancing the model directly is not an option - its world transform would land on top
        of the scatter placement.

        `matrix` is the preserved rotation/scale composed with the object's offset from its
        hierarchy root - see params.preservedLinearMatrix.
    """
    objName = f"{_dataKey(ns, 'protos')}.obj.{index:02d}.{subIndex:02d}"
    proxy = bpy.data.objects.get(objName)
    if proxy is None:
        # Always a MESH object whatever the model's type - the modifier supplies the geometry, so
        # a model changing from mesh to curve no longer needs the proxy recreating.
        proxy = bpy.data.objects.new(objName, _proxyBaseMesh())

    mod = proxy.modifiers.get(_PROXY_MODIFIER) or proxy.modifiers.new(_PROXY_MODIFIER, 'NODES')
    ng = nodegroup.getOrCreateProxyNodeGroup()
    if mod.node_group is not ng:
        mod.node_group = ng
    if identifier := nodegroup.inputIdentifier(ng, nodegroup.IN_PROXY_SOURCE):
        _setModifierInput(mod, identifier, sourceObj)

    proxy.matrix_world = matrix
    return proxy


def refreshProxyMeshes(obj, previewModels: list[resolve.ResolvedModel], depsgraph=None, childMap=None):
    """ Regenerate the box/wire proxy meshes from the models' LOCAL bounding boxes, and place the
        proxy objects at the model's rotation+scale (no translation) - matching the full-geometry
        proxies and the readScatterData source node, so Box/Wire modes preserve rotation/scale
        consistently with Full mode and the V-Ray render. The model's translation never enters.

        previewModels is the caller's resolveModels(expandHierarchy=False) list.
    """
    cs = obj.chaos_scatter
    ns = cs.curve_ns
    root = _getRootCollection()
    if root is None:
        return

    # Evaluated bounds, so the Box/Wire proxies match what Full mode and the render show.
    # Take the caller's depsgraph: rebuildProtoCollections has just created/relinked the proxy
    # objects, so evaluated_depsgraph_get() here forces a full re-evaluation of the scene (~195 ms
    # in a 20k-object file) to read model bounds that the caller's depsgraph already holds - the
    # proxies are outputs of this function, never inputs to the bounds.
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()

    if childMap is None:
        childMap = resolve.buildChildMap()
    for i, entry in enumerate(previewModels):
        modelObj = entry.object
        bboxMin, bboxMax = resolve.hierarchyLocalBounds(modelObj, depsgraph, childMap)
        preservedLinear = params.preservedLinearMatrix(cs, modelObj)
        _ensureBoxProxy(root, ns, "boxes", i, bboxMin, bboxMax, preservedLinear, wire=False)
        _ensureBoxProxy(root, ns, "wires", i, bboxMin, bboxMax, preservedLinear, wire=True)


def _boxGeometry(bboxMin, bboxMax, wire: bool):
    x0, y0, z0 = bboxMin
    x1, y1, z1 = bboxMax
    verts = [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]
    if wire:
        edges = [(0, 1), (1, 2), (2, 3), (3, 0),
                 (4, 5), (5, 6), (6, 7), (7, 4),
                 (0, 4), (1, 5), (2, 6), (3, 7)]
        return verts, edges, []
    faces = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
             (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return verts, [], faces


def _ensureBoxProxy(root, ns, kind, index, bboxMin, bboxMax, preservedLinear, wire: bool):
    collName = f"{_dataKey(ns, kind)}.{index:02d}"
    coll = bpy.data.collections.get(collName)
    if coll is None:
        return

    objName = f"{_dataKey(ns, kind)}.obj.{index:02d}"
    verts, edges, faces = _boxGeometry(bboxMin, bboxMax, wire)

    proxyObj = bpy.data.objects.get(objName)
    if proxyObj is None:
        mesh = bpy.data.meshes.new(objName)
        mesh.from_pydata(verts, edges, faces)
        proxyObj = bpy.data.objects.new(objName, mesh)
    else:
        mesh = proxyObj.data
        mesh.clear_geometry()
        mesh.from_pydata(verts, edges, faces)

    # see params.preservedLinearMatrix
    proxyObj.matrix_world = preservedLinear

    if proxyObj.name not in coll.objects:
        coll.objects.link(proxyObj)


def _ensureChildCollection(parent, name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if coll.name not in parent.children:
        parent.children.link(coll)
    return coll


def _syncProtoChildren(parent, ns, kind, count):
    """ Keep exactly `count` zero-padded proto sub-collections, in order. """
    wanted = [f"{_dataKey(ns, kind)}.{i:02d}" for i in range(count)]
    for child in list(parent.children):
        if child.name not in wanted:
            _removeCollectionTree(child)
    for name in wanted:
        _ensureChildCollection(parent, name)


def _syncLinkedObjects(coll, objects):
    wantedNames = {o.name for o in objects}
    for existing in list(coll.objects):
        if existing.name not in wantedNames:
            coll.objects.unlink(existing)
            # Generated proxies belong to us; drop them rather than leaving 0-user orphans that
            # no cleanup path walks (they are not in any collection any more).
            if _isGeneratedProxy(existing.name) and existing.users == 0:
                bpy.data.objects.remove(existing)
    for o in objects:
        if o.name not in coll.objects:
            coll.objects.link(o)
        # The collection-level hide flags do the hiding; per-object flags would remove the
        # object from the depsgraph and break Collection Info (see the .vrscene importer).


def _removeCollectionTree(coll, ownerPrefix: str = None):
    """ Remove a collection, its sub-collections and the generated proxy objects it owns.

        ownerPrefix is the tree's object-name prefix, read off the collection name when not
        given. A proxy from ANOTHER tree is only unlinked - a light-only model shares its wire
        proxy into the protos collection, and reaping it there would free the wires tree's mesh.
    """
    if ownerPrefix is None:
        parts = coll.name.split(".")            # cs.<ns>.<kind>[.NN]
        if len(parts) >= 3 and parts[0] == "cs":
            ownerPrefix = f"cs.{parts[1]}.{parts[2]}.obj."

    for child in list(coll.children):
        _removeCollectionTree(child, ownerPrefix)
    for o in list(coll.objects):
        if _isGeneratedProxy(o.name) and (ownerPrefix is None or o.name.startswith(ownerPrefix)):
            # Box/wire proxies own the generated box mesh (free it); full-geometry proto proxies
            # (".protos.obj.") all share the one empty _PROXY_BASE_MESH, which must survive.
            ownsData = ".protos.obj." not in o.name
            data = o.data
            bpy.data.objects.remove(o)
            if ownsData and data is not None and data.users == 0:
                if isinstance(data, bpy.types.Mesh):
                    bpy.data.meshes.remove(data)
    bpy.data.collections.remove(coll)


def handleDuplicates():
    """ Detect duplicated scatter objects (Shift+D copies the propgroup incl. curve_ns) and
        give the newer copies their own namespace, curve widgets and collections.
    """
    byNs = {}
    for obj in utils.allScatterObjects():
        byNs.setdefault(obj.chaos_scatter.curve_ns, []).append(obj)

    for ns, objects in byNs.items():
        if len(objects) < 2:
            continue
        # Keep the first (arbitrary but stable) owner; re-namespace the rest
        for dupObj in objects[1:]:
            _renamespace(dupObj, oldNs=ns)


def _renamespace(obj, oldNs: str):
    from chaos_scatter import curves

    cs = obj.chaos_scatter
    cs.curve_ns = uuid.uuid4().hex[:12]

    for slot in curves.SLOTS:
        curves.ensureCurveNode(obj, slot)
    curves.copyCurves(oldNs, obj)

    # Duplicates initially share the pointcloud datablock; give the copy its own so the next
    # recompute of either object does not clobber the other
    if obj.data is not None and obj.data.users > 1:
        obj.data = obj.data.copy()

    rebuildProtoCollections(obj)
    syncModifierInputs(obj)


def cleanupOrphans():
    """ Remove data owned by scatter objects that no longer exist. The collections and curve nodes
        reaped here are file-global, so the live set must be too - scoping it to one scene would
        destroy the data of every scatter object in the other scenes.
    """
    liveNamespaces = {obj.chaos_scatter.curve_ns for obj in utils.allScatterObjects()}

    root = bpy.data.collections.get(ROOT_COLLECTION_NAME)
    if root is not None:
        for child in list(root.children):
            parts = child.name.split(".")
            if len(parts) >= 3 and parts[0] == "cs" and parts[1] not in liveNamespaces:
                _removeCollectionTree(child)

    from chaos_scatter import curves
    curves.cleanupOrphanCurveNodes(liveNamespaces)
