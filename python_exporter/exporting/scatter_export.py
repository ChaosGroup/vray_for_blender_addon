# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Chaos Scatter render export.

    Exports the PointCloud carrier object of a Chaos Scatter setup (created by the separate
    'chaos_scatter' addon) as the plugin chain Node -> GeomInstancer(instances_generator) ->
    GeomScatter. Placement is computed by V-Ray at render time from the high-level GeomScatter
    parameters; the baked geometry-node preview instances are skipped by instancer_export.

    The scalar parameter dict comes from chaos_scatter.params.buildScatterParams(); list ordering
    comes from chaos_scatter.resolve. The remaining non-scalar inputs are resolved here and in
    chaos_scatter.backend.vray_backend separately - params.OWNED_ATTRS names every one of them and
    which side owns it, so a param cannot silently end up handled on only one side again.
"""

import bpy

from typing import TYPE_CHECKING

from mathutils import Color

if TYPE_CHECKING:
    # Type-only. chaos_scatter is a soft dependency - the runtime imports stay inside the
    # functions so this module keeps loading when the addon is not installed.
    from chaos_scatter.resolve import ResolvedModel, ResolvedTarget

from vray_blender import debug
from vray_blender.exporting.node_export import exportNodePlugin
from vray_blender.exporting import tools
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib import export_utils, image_utils, lib_utils, path_utils
from vray_blender.lib.defs import AttrListValue, AttrPlugin, ExporterContext, PluginDesc
from vray_blender.lib.names import Names

from vray_blender.bin import VRayBlenderLib as vray


def prepassResolveTargets(geomExporter) -> tuple[dict, dict]:
    """ Resolve every scatter carrier's target list before the geometry export loop starts.

        {objTrackId: list[ResolvedTarget]}, {curveName: polylines}. Must run while no object
        holds a temp mesh: resolving tessellates curve targets, and to_mesh() frees the previous
        temp mesh on entry - mid-loop that would free one exportGeometry is still reading on a
        worker thread. Also avoids re-tessellating a curve shared by several scatters.
    """
    try:
        from chaos_scatter import resolve as scatterResolve
    except ImportError:
        return {}, {}

    targetCache, polylineCache = {}, {}

    def cachePolylines(curveObj):
        if curveObj is not None and curveObj.type == 'CURVE' and curveObj.name not in polylineCache:
            polylineCache[curveObj.name] = scatterResolve.curvePolylines(curveObj, geomExporter.dg)

    for obj in geomExporter.dg.objects:
        if not tools.isObjectChaosScatter(obj):
            continue
        cs = obj.chaos_scatter
        if cs.scatter_type == '0':
            targetCache[getObjTrackId(obj)] = []   # 1D spline mode has no target plugins
            for item in cs.targets:
                cachePolylines(item.object)
        else:
            targetCache[getObjTrackId(obj)] = scatterResolve.resolveTargets(cs, geomExporter.dg)
        for item in cs.area_modifiers:
            cachePolylines(item.object)

    return targetCache, polylineCache


def tearDownScattersReferencing(geomExporter, prunedTrackIds: set) -> None:
    """ Drop the GeomScatter chain of every carrier referencing an object about to be pruned.

        prunePlugins() removes the Node while the live GeomScatter still lists it and the RT
        engine keeps tracing, so a hit shades through a freed material. The object pass
        re-exports the carrier straight after.

        TODO: mitigation only. Core's deletePlugin does not log the referrers ABOVE the direct
        one (our Node -> GeomInstancer -> GeomScatter chain), so the compiled geometry is never
        invalidated. Fixed upstream by ASDK-2191, master only - not in the 7.4 appsdk we pin.
        Re-test on the next appsdk bump and delete this if it is in.
    """
    if not prunedTrackIds:
        return

    try:
        from chaos_scatter import resolve as scatterResolve
    except ImportError:
        return  # addon not installed - nothing to tear down

    for carrier in geomExporter.allObjects:
        cs = getattr(carrier, "chaos_scatter", None)
        if (cs is None) or (not cs.is_scatter):
            continue

        # Whole hierarchy, not just cs.models: children get their own Nodes too.
        try:
            referenced = {getObjTrackId(m.object)
                          for m in scatterResolve.resolveModels(cs, expandHierarchy=True)
                          if m.object is not None}
        except Exception:
            referenced = {getObjTrackId(item.object)
                          for item in cs.models if item.object is not None}

        if not (referenced & prunedTrackIds):
            continue

        objName       = Names.object(carrier)
        scatterName   = Names.pluginObject("scatter", objName)
        instancerName = Names.pluginObject("instancer", scatterName)
        objTrackId    = getObjTrackId(carrier)

        # Referrers first: Node -> GeomInstancer -> GeomScatter.
        for pluginName in (Names.vrayNode(objName), instancerName, scatterName):
            vray.pluginRemove(geomExporter.renderer, pluginName)
            geomExporter.objTracker.forgetPlugin(objTrackId, pluginName)


def exportChaosScatter(geomExporter, obj: bpy.types.Object, isVisible: bool, force=False):
    """ Export the full GeomScatter chain for a scatter carrier object.

        geomExporter: the GeometryExporter running the object pass.
    """
    assert obj.is_evaluated or force, f"Evaluated object expected: {obj.name}"
    cs = obj.chaos_scatter

    try:
        from chaos_scatter import params as scatterParams
        from chaos_scatter import resolve as scatterResolve
    except ImportError:
        debug.printError("Chaos Scatter addon module not found; scatter object "
                         f"'{obj.name}' will not be exported")
        return

    objTrackId    = getObjTrackId(obj)
    objName       = Names.object(obj)
    scatterName   = Names.pluginObject("scatter", objName)
    instancerName = Names.pluginObject("instancer", scatterName)

    scatterDesc = PluginDesc(scatterName, "GeomScatter")
    for name, value in scatterParams.buildScatterParams(cs, forPreview=False).items():
        scatterDesc.setAttribute(name, value)

    # Every list attribute and plugin reference below is written on EVERY export, empty included.
    # An unwritten attribute keeps its previous value on the server, so a cleared list or a
    # disabled feature would stay live for the rest of an IPR session.
    resolvedTargets = []
    verts, counts, targets, factors = [], [], [], []

    if cs.scatter_type == '0':
        # 1D spline scattering: geometry travels as polylines, not as target Nodes. Vertices are
        # pre-transformed to world space so spline_transforms can stay at its identity default
        # (the generic list wire has no transform element type).
        verts, counts = _collectSplinePolylines(geomExporter, (i.object for i in cs.targets))
        if not counts:
            debug.printDebug(f"Chaos Scatter '{obj.name}' has no valid spline targets")
    else:
        # From the prepass - see prepassResolveTargets for why it must not happen here
        resolvedTargets = geomExporter.scatterTargetCache.get(objTrackId)
        if resolvedTargets is None:
            resolvedTargets = scatterResolve.resolveTargets(cs, geomExporter.dg)
        targets, factors = _collectTargets(geomExporter, resolvedTargets, objTrackId)
        if not targets:
            debug.printDebug(f"Chaos Scatter '{obj.name}' has no valid distribution targets")

    resolvedModels = scatterResolve.resolveModels(cs, expandHierarchy=True)

    # cs.models keeps a deleted object's datablock alive, so resolveModels still yields it, and
    # emitting it would re-create a bare Node whose geometry obj_export just removed. parentIndex
    # values are positions in this list, so survivors must be renumbered, not just filtered.
    sceneObjects = geomExporter.dg.scene.objects
    kept = [i for i, m in enumerate(resolvedModels) if sceneObjects.get(m.object.name) is not None]
    if len(kept) != len(resolvedModels):
        remap = {old: new for new, old in enumerate(kept)}
        survivors = []
        for old in kept:
            model = resolvedModels[old]
            # A dropped parent makes its children roots.
            model.parentIndex = remap.get(model.parentIndex, -1)
            survivors.append(model)
        resolvedModels = survivors

    # Degenerate: hand the core nothing rather than an empty GeomScatter, which it reads
    # unconditionally. Mirrors the preview's refusal in recompute._submit.
    if (not counts and not targets) or not resolvedModels:
        debug.printDebug(f"Chaos Scatter '{obj.name}' has no targets or no models; skipping export")
        # Skipping would leave the live GeomScatter referencing model Nodes that obj_export just
        # removed, and the rayserver faults freeing their geometry. Tear it down, referrers first.
        for pluginName in (Names.vrayNode(objName), instancerName, scatterName):
            vray.pluginRemove(geomExporter.renderer, pluginName)
            geomExporter.objTracker.forgetPlugin(objTrackId, pluginName)
        return

    scatterDesc.setAttribute("spline_vertices", _vectorListValue(verts) if verts else [])
    scatterDesc.setAttribute("spline_vertex_counts", counts)
    scatterDesc.setAttribute("targets", targets)
    scatterDesc.setAttribute("target_factors", factors)

    _fillModels(geomExporter, resolvedModels, scatterDesc)
    _fillFalloffCurves(cs, scatterDesc)
    _fillLookAt(geomExporter, cs, scatterDesc, objName, objTrackId)
    _fillDensityMap(geomExporter, cs, scatterDesc, objName, objTrackId)
    _fillAreaModifiers(geomExporter, cs, scatterDesc)
    _fillClippingCamera(geomExporter, cs, scatterDesc, objName, objTrackId)
    _fillClusterColorMap(geomExporter, cs, scatterDesc, objName, objTrackId)
    _fillTransformMaps(geomExporter, cs, scatterDesc, objName, objTrackId)
    _fillClusterStrokes(geomExporter, cs, scatterDesc, resolvedTargets, resolvedModels)
    _fillInstanceOverride(geomExporter, cs, scatterDesc, resolvedTargets)

    export_utils.exportPlugin(geomExporter, scatterDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, scatterName)

    instancerDesc = PluginDesc(instancerName, "GeomInstancer")
    instancerDesc.setAttribute("instances_generator", AttrPlugin(scatterName))
    instancerDesc.setAttribute("use_rayserver", False)
    # use_source_transform=1 (the default) is what makes the model's Blender object transform NOT
    # move the scatter: GeomScatter pre-multiplies every instance by the model Node's INVERSE world
    # matrix (scatter.cpp:2135 "the instancer will add the node transform to the instances, we
    # don't want that"), and GeomInstancer re-applies that same node transform - so the two cancel
    # and moving/scaling/rotating the model leaves the scatter unchanged. (Setting it to 0 leaves
    # the un-cancelled inverse and the scatter then follows the model.) Matches C4D/SketchUp.
    instancerDesc.setAttribute("use_source_transform", 1)
    export_utils.exportPlugin(geomExporter, instancerDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, instancerName)

    # isInstancer=True wraps with an identity transform (GeomScatter emits world-space instances).
    exportNodePlugin(geomExporter, obj, AttrPlugin(instancerName), objName,
                     geomExporter.objTracker, isInstancer=True, visible=isVisible)

    # Apply the carrier object's transform so moving/rotating/scaling the scatter object moves
    # the whole result, matching the viewport preview (whose baked points are carrier-relative:
    # Blender applies matrix_world to the geometry-node output). The placement itself is
    # world-space from the targets, so at the carrier's default identity this is a no-op.
    from vray_blender.lib import plugin_utils
    plugin_utils.updateValue(geomExporter.renderer, Names.vrayNode(objName), "transform", obj.matrix_world)


def scatterNeedsUpdate(exporterCtx: ExporterContext, obj: bpy.types.Object) -> bool:
    """ True when any object referenced by the scatter setup changed this cycle, so the
        GeomScatter chain must re-export even though the carrier object itself did not.
    """
    cs = getattr(obj.original, 'chaos_scatter', None)
    if cs is None:
        return False

    updates = exporterCtx.dgUpdates['all']

    def changed(o):
        return (o is not None) and (getObjTrackId(o) in updates)

    for item in cs.targets:
        if changed(item.object):
            return True
    for item in cs.models:
        if changed(item.object):
            return True
        if item.object is not None and any(changed(c) for c in item.object.children_recursive):
            return True
    for item in cs.area_modifiers:
        if changed(item.object):
            return True
    # The camera the frustum is actually built from, not just the picked one. An ORTHOGRAPHIC render
    # camera is baked into a RenderView by _fillClippingCamera even in Render Camera mode, so moving
    # it has to re-export the scatter or the IPR keeps culling to the old frustum. A perspective
    # render camera needs no entry here - no RenderView is written and the core reads live frame
    # data - but resolving through the shared helper keeps the two sides from drifting again.
    from chaos_scatter import params as scatterParams

    return changed(cs.look_at.look_at_target) \
        or changed(scatterParams.clippingCameraObject(cs, exporterCtx.dg.scene))


def _collectTargets(geomExporter, resolvedTargets: list["ResolvedTarget"], objTrackId: int):
    """ Resolve the ordered target list to plugins. A mesh target references its own scene Node
        (pre-created so the reference is valid regardless of export order; reference_collector
        force-exports hidden ones); a face-less curve's polylines become GeomScatterSpline
        plugins placed directly in the targets list, matching V-Ray for Maya.
    """
    from chaos_scatter.resolve import TARGET_SPLINE

    targets, factors = [], []
    splineIndex = {}

    for entry in resolvedTargets:
        if entry.kind == TARGET_SPLINE:
            baseName = Names.pluginObject("scatterspline", Names.object(entry.object))
            idx = splineIndex.get(baseName, 0)
            splineIndex[baseName] = idx + 1
            name = f"{baseName}|{idx}"
            flatVerts = [c for co in entry.polyline for c in co]
            vray.pluginCreate(geomExporter.renderer, name, "GeomScatterSpline")
            vray.pluginUpdateVectorList(geomExporter.renderer, name, "vertices", flatVerts)
            vray.pluginUpdateInt(geomExporter.renderer, name, "triangulate",
                                 1 if entry.closed else 0, True)
            # These are owned by the scatter, not by the curve object, so the tracker has to know
            # about them or removing a curve target mid-IPR leaves them orphaned on the server.
            geomExporter.objTracker.trackPlugin(objTrackId, name)
            targets.append(AttrPlugin(name))
        else:
            nodeName = Names.vrayNode(Names.object(entry.object))
            vray.pluginCreate(geomExporter.renderer, nodeName, 'Node')
            targets.append(AttrPlugin(nodeName))
        factors.append(entry.factor)

    return targets, factors


def _fillModels(geomExporter, resolvedModels: list["ResolvedModel"], scatterDesc: PluginDesc):
    """ Fill models + parallel per-model arrays. Each model item contributes its whole object
        hierarchy; children reference the index of their hierarchy root in model_parents
        (-1 for the roots themselves), mirroring the C4D exporter.

        The models reference the objects' own scene Node plugins (pre-created so the references are
        valid regardless of export order; reference_collector force-exports hidden ones). These are
        normal, renderable nodes, so their geometry is reliably compiled on the first pass - a
        hidden / instance-prototype node defers compilation, leaving the scatter empty until an IPR
        update (and never rendering in production). The model's own object transform does not move
        the scatter - see the use_source_transform note in exportChaosScatter.

        A light model references the light plugin itself - a light has no Node wrapper, and
        GeomInstancer clones the light per instance, force-enabling the clones so the source light
        can stay hidden. Same as the C4D exporter (exporter_geometry.cpp isAllowedScatterModel).
    """
    models, colors = [], []
    for entry in resolvedModels:
        if entry.object.type == 'LIGHT':
            lightName = Names.object(entry.object)
            # Pre-created with the type LightExporter will use: the light pass runs after this one.
            vray.pluginCreate(geomExporter.renderer, lightName,
                              lib_utils.getLightPluginType(entry.object.data))
            models.append(AttrPlugin(lightName))
        else:
            nodeName = Names.vrayNode(Names.object(entry.object))
            vray.pluginCreate(geomExporter.renderer, nodeName, 'Node')
            models.append(AttrPlugin(nodeName))
        colors.append(Color(entry.object.color[:3]))

    scatterDesc.setAttribute("models", models)
    scatterDesc.setAttribute("model_parents", [m.parentIndex for m in resolvedModels])
    scatterDesc.setAttribute("model_frequencies", [m.frequency for m in resolvedModels])
    scatterDesc.setAttribute("model_cluster_group_ids", [m.clusterGroupId for m in resolvedModels])
    scatterDesc.setAttribute("model_instance_colors",
                             _colorListValue(colors) if colors else [])


def _vectorListValue(points) -> AttrListValue:
    """ Pack 3-component tuples as a generic list of Vector elements ('v' wire type). The
        server converts them to a ValueList of VRay::Vector, which the AppSDK accepts for
        VECTOR_LIST params. NOTE: requires the 'v'/'c' cases in interop/conversion.cpp
        (part of the scatter rebuild batch).
    """
    from mathutils import Vector
    listValue = AttrListValue()
    for p in points:
        listValue.append(Vector((p[0], p[1], p[2])))
    return listValue


def _colorListValue(colors) -> AttrListValue:
    """ COLOR_LIST counterpart of _vectorListValue ('c' wire type). """
    listValue = AttrListValue()
    for c in colors:
        listValue.append(Color((c[0], c[1], c[2])))
    return listValue


def _fillFalloffCurves(cs, scatterDesc: PluginDesc):
    """ Both falloff curves. Decoding is shared with the preview. """
    from chaos_scatter.curves import parseFalloffData
    from chaos_scatter.params import FALLOFF_INTERP_LINEAR

    for attr, data in (("surface_altitude_limit_falloff_curve",
                        cs.surface.surface_altitude_limit_falloff_data),
                       ("look_at_falloff_curve", cs.look_at.look_at_falloff_data)):
        curve = parseFalloffData(data)
        scatterDesc.setAttribute(attr, _vectorListValue(curve) if curve else [])
        scatterDesc.setAttribute(attr + "_interp", FALLOFF_INTERP_LINEAR)


def _fillLookAt(geomExporter, cs, scatterDesc: PluginDesc, objName: str, objTrackId: int):
    """ The look-at target is referenced through a helper Node carrying only its world transform
        (same trick as the C4D exporter - the target may be any object, e.g. an Empty).
    """
    lookAt = cs.look_at
    if not lookAt.look_at_enabled or lookAt.look_at_target is None:
        scatterDesc.setAttribute("look_at_target", [])
        return

    helperName = Names.pluginObject("scatterlookat", objName)
    helperDesc = PluginDesc(helperName, "Node")
    helperDesc.setAttribute("transform", lookAt.look_at_target.matrix_world)
    helperDesc.setAttribute("visible", False)
    export_utils.exportPlugin(geomExporter, helperDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, helperName)

    scatterDesc.setAttribute("look_at_target", AttrPlugin(helperName))


def _fillDensityMap(geomExporter, cs, scatterDesc: PluginDesc, objName: str, objTrackId: int):
    """ The Custom Map density pattern, as a TexBitmap over the user's image. The map is read as
        data (raw / linear), not as color - it modulates density, not shading.
    """
    # Only the Custom Map pattern (1) uses a texture - the other patterns are core procedurals.
    # GeomScatter attaches its density callback on the presence of the texture alone, not on the
    # pattern, so leaving it wired would keep the map live after the user picks None. V-Ray for
    # 3ds Max gates the export on the same pattern value.
    surface = cs.surface
    image = surface.surface_random_density_map
    if (image is None) or (surface.surface_random_density_map_pattern != '1'):
        scatterDesc.setAttribute("surface_random_density_map", [])
        return

    bufName = Names.pluginObject("scatterdensitybuf", objName)
    bufDesc = PluginDesc(bufName, "BitmapBuffer")
    bufDesc.setAttributes({
        "file": path_utils.formatResourcePath(image_utils.getTrackedImagePath(image),
                                              geomExporter.exportOnly),
        "gamma": 1,
        "rgb_color_space": "raw",
        "transfer_function": 0,  # linear
    })
    export_utils.exportPlugin(geomExporter, bufDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, bufName)

    # A TexBitmap with no uvwgen returns nouvw_color (0.5) - a constant, which made the whole
    # pattern inert. uvw_channel -1 leaves the channel choice with GeomScatter: the core only
    # consults the texture's own channel when compatibility_single_channel_limitation is off
    # (we pin it on), and a negative channel falls back to the UVW it derives from map_channel /
    # map_channel_name either way. Same plugin and value the C4D integration attaches.
    uvwName = Names.pluginObject("scatterdensityuvw", objName)
    uvwDesc = PluginDesc(uvwName, "UVWGenChannel")
    uvwDesc.setAttribute("uvw_channel", -1)
    export_utils.exportPlugin(geomExporter, uvwDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, uvwName)

    texName = Names.pluginObject("scatterdensity", objName)
    texDesc = PluginDesc(texName, "TexBitmap")
    texDesc.setAttribute("bitmap", AttrPlugin(bufName))
    texDesc.setAttribute("uvwgen", AttrPlugin(uvwName))
    export_utils.exportPlugin(geomExporter, texDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, texName)

    # The param is a float texture and TexBitmap only offers a float interface through its
    # intensity output; without it GeomScatter caches a null densityTex and never even attaches
    # its density callback. V-Ray for 3ds Max passes the same "intensity" output here.
    scatterDesc.setAttribute("surface_random_density_map", AttrPlugin(texName, "out_intensity"))


def _fillColorTextureMap(geomExporter, scatterDesc: PluginDesc, objName: str, objTrackId: int,
                         attr: str, image, tag: str, active: bool):
    """ Attach a user image to one of GeomScatter's TEXTURE parameters as a TexBitmap, or write the
        attribute EMPTY when it is not in use.

        Shared by every colour-typed map (cluster colour, transforms translation/rotation/scale).
        The density map is deliberately NOT routed through here: it is a FLOAT_TEXTURE read as data,
        so it needs raw/linear colour management and the texture's out_intensity output, while these
        are read as colour and take the plugin reference directly.

        `active` is the caller's gate. GeomScatter attaches its callbacks on the presence of the
        texture alone, never on the mode that is supposed to consume it, so a map left wired stays
        live after the user switches away - hence writing [] rather than simply skipping.
    """
    if (image is None) or not active:
        scatterDesc.setAttribute(attr, [])
        return

    bufName = Names.pluginObject(f"scatter{tag}buf", objName)
    bufDesc = PluginDesc(bufName, "BitmapBuffer")
    bufDesc.setAttribute("file", path_utils.formatResourcePath(
        image_utils.getTrackedImagePath(image), geomExporter.exportOnly))
    export_utils.exportPlugin(geomExporter, bufDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, bufName)

    # uvw_channel -1 leaves the channel choice with GeomScatter, as for the density map.
    uvwName = Names.pluginObject(f"scatter{tag}uvw", objName)
    uvwDesc = PluginDesc(uvwName, "UVWGenChannel")
    uvwDesc.setAttribute("uvw_channel", -1)
    export_utils.exportPlugin(geomExporter, uvwDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, uvwName)

    texName = Names.pluginObject(f"scatter{tag}tex", objName)
    texDesc = PluginDesc(texName, "TexBitmap")
    texDesc.setAttribute("bitmap", AttrPlugin(bufName))
    texDesc.setAttribute("uvwgen", AttrPlugin(uvwName))
    export_utils.exportPlugin(geomExporter, texDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, texName)

    scatterDesc.setAttribute(attr, AttrPlugin(texName))


def _fillTransformMaps(geomExporter, cs, scatterDesc: PluginDesc, objName: str, objTrackId: int):
    """ The per-instance translation / rotation / scale maps.

        The core samples each with sampleColorTex into three floats, so the texture's RGB drives
        XYZ (scatter.cpp translationMapCallback and friends). The companion scalars are already
        exported by the generic path: _mode ('0' Fixed / '1' Random amount, matching the plugin's
        own enum) and the per-axis _x/_y/_z enables, which the core gathers off the base name.

        Attachment is by presence: the core wires its callback only when the texture is non-null,
        so an unset map must be written EMPTY to switch it off. V-Ray for 3ds Max and for Maya both
        pass the texture straight through with no wrapper, which is what this does.
    """
    transforms = cs.transforms
    for attr, tag in (('transforms_translation_map', 'xftranslation'),
                      ('transforms_rotation_map',    'xfrotation'),
                      ('transforms_scale_map',       'xfscale')):
        image = getattr(transforms, attr)
        _fillColorTextureMap(geomExporter, scatterDesc, objName, objTrackId,
                             attr, image, tag, active=image is not None)


def _fillClippingCamera(geomExporter, cs, scatterDesc: PluginDesc, objName: str, objTrackId: int):
    """ Camera clipping: point camera_clipping_selected_cam at a RenderView carrying the clipping
        camera's frustum, or write it EMPTY and let the core use the render camera's frame data.

        The core reads only fov / focalDistance / transform / orthographic off whatever plugin this
        points at, and RenderView is the one plugin that has all four. It is built from the SAME
        packer the viewport preview uses (chaos_scatter.collect.packCamera), so the two frustums
        cannot drift. dont_affect_settings keeps this extra view out of the renderer's sequence and
        frame data, so it never competes with the real render camera.

        Render Camera mode normally writes the attribute EMPTY - the frame data is the real frustum
        and beats any reconstruction. The exception is an ORTHOGRAPHIC camera, where the frame data
        is not the real frustum at all: the true half-width lives in RenderView.orthographicWidth,
        which the core never reads, while the fov and focalDistance it does read are the camera's
        PERSPECTIVE values (_fillCameraData writes camera.angle and the DoF distance regardless of
        camera type). Measured on the QA matrix, that frustum is a fraction of the real one - the
        render kept 0.014 coverage where nothing at all should have been culled. packCamera encodes
        ortho the way the core's single frustum model wants it, so ortho goes through the selected
        camera path even when the user asked for Render Camera.
    """
    from chaos_scatter import collect as scatterCollect
    from chaos_scatter import params as scatterParams

    camObj = scatterParams.clippingCameraObject(cs, geomExporter.dg.scene)
    if camObj is None:
        scatterDesc.setAttribute("camera_clipping_selected_cam", [])
        return

    # No aspect correction here, unlike the preview (vray_backend._aspectFix): the core takes the
    # frustum's vertical extent from imgWidth/imgHeight on the frame data, which for a real render
    # already IS the render aspect packCamera computed the fov against.
    camera = scatterCollect.packCamera(camObj, geomExporter.dg.scene)
    if cs.camera_clipping.camera_clipping_mode != '1' and not camera['isOrtho']:
        scatterDesc.setAttribute("camera_clipping_selected_cam", [])
        return

    # Overrides the mode buildScatterParams emitted: it applies the C4D fallback to Render Camera
    # for an unset picker, and knows nothing about the orthographic exception above.
    scatterDesc.setAttribute("camera_clipping_mode", 1)

    viewName = Names.pluginObject("scatterclipview", objName)
    viewDesc = PluginDesc(viewName, "RenderView")
    viewDesc.setAttributes({
        "transform": camObj.matrix_world,
        "fov": camera['fovRad'],
        "focalDistance": camera['focalDistance'],
        "orthographic": camera['isOrtho'],
        "orthographicWidth": camera['orthoWidth'],
        "dont_affect_settings": True,
        "use_scene_offset": False,
    })
    export_utils.exportPlugin(geomExporter, viewDesc)
    geomExporter.objTracker.trackPlugin(objTrackId, viewName)

    scatterDesc.setAttribute("camera_clipping_selected_cam", AttrPlugin(viewName))


def _fillClusterColorMap(geomExporter, cs, scatterDesc: PluginDesc, objName: str, objTrackId: int):
    """ The Color Map clustering texture: the core samples it at each candidate point and places
        the model whose own instance colour matches the sample, so it is a MATCH KEY rather than a
        tint (the core quantizes both sides to 8 bits per channel). Measured: a model whose colour
        is absent from the map is never placed; with no model matching at all the region is still
        filled, so a wrong map looks like it works.
    """
    from chaos_scatter import params as scatterParams

    _fillColorTextureMap(
        geomExporter, scatterDesc, objName, objTrackId,
        "cluster_instances_color_map", cs.clusters.cluster_instances_color_map, "clustercolor",
        active=scatterParams.colorMapClusteringActive(cs))


def _fillAreaModifiers(geomExporter, cs, scatterDesc: PluginDesc):
    """ Include/exclude spline areas: each curve object contributes its polylines; the parallel
        per-area arrays are repeated per polyline so the lists stay aligned.
    """
    allVerts, allCounts = [], []
    operations, foNear, foFar, scales, densities, axes = [], [], [], [], [], []

    for item in cs.area_modifiers:
        areaObj = item.object
        if areaObj is None or areaObj.type != 'CURVE':
            continue
        verts, counts = _collectSplinePolylines(geomExporter, [areaObj])
        if not counts:
            continue
        allVerts.extend(verts)
        allCounts.extend(counts)
        for _ in counts:
            operations.append(int(item.operation))
            foNear.append(item.falloff_near)
            foFar.append(item.falloff_far)
            scales.append(item.scale)
            densities.append(item.density)
            axes.append(int(item.axis))

    scatterDesc.setAttribute("area_modifiers_vertices",
                             _vectorListValue(allVerts) if allVerts else [])
    scatterDesc.setAttribute("area_modifiers_vertex_counts", allCounts)
    scatterDesc.setAttribute("area_modifiers_operation", operations)
    scatterDesc.setAttribute("area_modifiers_falloff_near", foNear)
    scatterDesc.setAttribute("area_modifiers_falloff_far", foFar)
    scatterDesc.setAttribute("area_modifiers_scale", scales)
    scatterDesc.setAttribute("area_modifiers_density", densities)
    scatterDesc.setAttribute("area_modifiers_axis", axes)


def _fillClusterStrokes(geomExporter, cs, scatterDesc: PluginDesc,
                        resolvedTargets: list["ResolvedTarget"],
                        resolvedModels: list["ResolvedModel"]):
    """ ClustersByLayers ('Paint' mode): model_handles + clusters_layer_* arrays. Shares the packing
        with the viewport preview (params.buildClusterArrays) so preview and render cannot drift.
        Stored stroke points are object-space; they are re-resolved to triangle+barycentric on the
        current evaluated target meshes here (matching the preview). When clustering-by-layers is
        off or invalid the arrays are written EMPTY rather than left at the last painted state. """
    from chaos_scatter import params as scatterParams
    from chaos_scatter import utils as scatterUtils
    resolver = scatterUtils.makeStrokePointResolver(geomExporter.dg, cs)
    arrays = scatterParams.buildClusterArrays(cs, resolver, resolvedTargets, resolvedModels)
    for name, value in arrays.items():
        scatterDesc.setAttribute(name, list(value))


def _fillInstanceOverride(geomExporter, cs, scatterDesc: PluginDesc,
                          resolvedTargets: list["ResolvedTarget"]):
    """ Hand-painted individual instances (instance_override_* PLACED). Shares the packing with the
        viewport preview (params.buildInstanceOverrideArrays). Placements travel as a VECTOR_LIST
        {baryU, baryV, float(globalTriangleIndex)}; info packs as a flat INT_LIST.

        Stored placements are object-space points, re-resolved to triangle + barycentric on the
        current evaluated target meshes here, exactly as the preview does. """
    from chaos_scatter import params as scatterParams
    from chaos_scatter import utils as scatterUtils
    resolver = scatterUtils.makeStrokePointResolver(geomExporter.dg, cs)
    ov = scatterParams.buildInstanceOverrideArrays(cs, resolver, resolvedTargets)
    scatterDesc.setAttribute("instance_override_info_packs", list(ov['info_packs']))
    scatterDesc.setAttribute("instance_override_placements",
                             _vectorListValue(ov['placements']) if ov['placements'] else [])
    # Always empty, but written so a live IPR cannot keep a stale list (params.OWNED_ATTRS).
    scatterDesc.setAttribute("instance_override_transforms", [])


def _collectSplinePolylines(geomExporter, curveObjects):
    """ Sample curve objects into world-space polylines, via the shared resolver so the render
        and the viewport preview reassemble chains identically.

        Returns (vertices, vertexCounts): flat vector list and per-polyline point counts. The
        vertices are pre-transformed to world space so spline_transforms stays at its identity
        default (the generic list wire has no transform element type).
    """
    allVerts, counts = [], []
    for curveObj in curveObjects:
        if curveObj is None or curveObj.type != 'CURVE':
            continue
        # From the prepass only - tessellating here would free a temp mesh that is in flight
        # (see prepassResolveTargets).
        for coords, _closed in geomExporter.scatterPolylineCache.get(curveObj.name, ()):
            allVerts.extend(tuple(c) for c in coords)
            counts.append(len(coords))

    return allVerts, counts


