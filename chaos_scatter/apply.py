# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Bake a scatter preview result into the carrier (PointCloud, or a vertices-only Mesh on
    Blender versions where a PointCloud cannot be sized from Python - see
    utils.pointCloudResizable).

    Transforms arrive as (N, 12) V-Ray row layout (3 basis vectors + offset) in world space;
    they are decomposed into the point attributes the GN instancing group reads. The transform
    decomposition is ported from vray_blender's instancer import (kept standalone here).
"""

import bpy
import numpy as np

from chaos_scatter import nodegroup, utils


# Runtime (non-persisted) status per scatter object, keyed by session_uid. Read by the UI.
_status = {}


def getStatus(obj) -> dict:
    return _status.get(obj.session_uid, {})


def setError(obj, errorText: str):
    _status[obj.session_uid] = {'count': None, 'error': errorText, 'limitHit': False}


def applyScatterResult(obj, transforms: np.ndarray, topo: np.ndarray, limitHit=False):
    """ Write the baked points into the carrier's existing datablock.

        Baking into a fresh datablock and swapping it onto the object crashed Blender on undo
        (VBLD-2803). We run from a bpy.app.timers callback, which pushes no undo step, so the swap
        was invisible to the undo system: undo freed the datablock the timer had made and kept the
        carrier Object as unchanged, leaving obj.data dangling for the next depsgraph build.

        Rewriting in place keeps obj.data constant, so nothing is created or freed outside an
        operator. It also keeps the carrier from accumulating .001 names, and lands in edit mode,
        where assigning obj.data was silently dropped.
    """
    n = len(transforms) if transforms is not None else 0

    carrier = obj.data
    isPointCloud = isinstance(carrier, bpy.types.PointCloud)
    if isPointCloud and not utils.pointCloudResizable():
        # Blender 4.5 cannot size a PointCloud from Python at all, so a 5.1-authored carrier
        # cannot be filled here. Report it instead of raising out of the result timer.
        setError(obj, "This scatter object needs Blender 5.1+ (its point cloud carrier "
                      "cannot be resized here); re-create it to get a mesh carrier")
        return

    # Resizing keeps the attribute layout, so the writes below only overwrite values.
    if isPointCloud:
        carrier.resize(n)
    else:
        # Pre-5.1 carriers are a vertices-only MESH (see utils.pointCloudResizable)
        carrier.clear_geometry()
        if n > 0:
            carrier.vertices.add(n)

    if n > 0:
        # The scatter core has already folded the inverse of the model's preserved rotation and
        # scale into these transforms, and the preview proxies re-apply it, so the two cancel and
        # moving a model never shifts the instances. See params.preservedLinearMatrix.
        # Imported lazily because chaos_scatter must not require vray_blender to load.
        from vray_blender.lib.transform_utils import decompose4x4
        translation, quaternion, scale = decompose4x4(_to4x4(np.asarray(transforms, dtype=np.float32)))
        carrier.attributes['position'].data.foreach_set('vector', translation.ravel())
        _writePointAttr(carrier, nodegroup.ATTR_ORIENTATION, 'QUATERNION', quaternion)
        _writePointAttr(carrier, nodegroup.ATTR_SCALE, 'FLOAT_VECTOR', scale)
        _writePointAttr(carrier, nodegroup.ATTR_PROTO_INDEX, 'INT', np.asarray(topo, dtype=np.int32))
        _writePointAttr(carrier, nodegroup.ATTR_MASK, 'BOOLEAN', np.ones(n, dtype=bool))
        if isinstance(carrier, bpy.types.Mesh):
            carrier.update()

    _status[obj.session_uid] = {'count': n, 'error': "", 'limitHit': limitHit}
    obj.update_tag(refresh={'DATA'})


def _writePointAttr(pc, name: str, blType: str, values: np.ndarray):
    # The datablock is reused, so the attribute normally exists and new() would raise on it.
    # One whose type or domain no longer matches is replaced.
    attr = pc.attributes.get(name)
    if attr is not None and (attr.data_type != blType or attr.domain != 'POINT'):
        pc.attributes.remove(attr)
        attr = None
    if attr is None:
        attr = pc.attributes.new(name, blType, 'POINT')
    if blType == 'FLOAT_VECTOR':
        attr.data.foreach_set('vector', np.ascontiguousarray(values, dtype=np.float32).ravel())
    elif blType == 'QUATERNION':
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=np.float32).ravel())
    elif blType == 'INT':
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=np.int32).ravel())
    elif blType == 'BOOLEAN':
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=bool).ravel())
    else:
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=np.float32).ravel())


def _to4x4(tms12: np.ndarray) -> np.ndarray:
    """ (N, 12) V-Ray transforms (v0, v1, v2 basis rows + offset) -> (N, 4, 4) affine matrices.
        Blender's 3x3 has the V-Ray basis vectors as columns, so the 3x3 is basis^T.
    """
    n = tms12.shape[0]
    basis = tms12[:, :9].reshape(n, 3, 3).astype(np.float64)
    out = np.zeros((n, 4, 4), dtype=np.float64)
    out[:, :3, :3] = np.transpose(basis, (0, 2, 1))
    out[:, :3, 3] = tms12[:, 9:12].astype(np.float64)
    out[:, 3, 3] = 1.0
    return out

