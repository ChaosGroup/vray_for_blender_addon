# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Batched transform maths: V-Ray matrices <-> Blender's separate position / orientation
    (QUATERNION) / scale point attributes. Callers decompose millions of instances per update,
    so everything is vectorized rather than a per-point mathutils.Matrix.decompose().

    Pure numpy - no bpy, no VRayBlenderLib.
"""

import numpy as np


def normalizeRotationScale(m3: np.ndarray):
    """ Split (N, 3, 3) linear parts into a proper rotation and a scale.

        Returns (rotNorm, scale). Columns are the transformed axes, so the scale is the per-column
        norm. A negative determinant means the basis is mirrored, which a quaternion cannot carry;
        the flip is folded into the Z scale instead so rotation * scale still reconstructs it.
    """
    scale = np.linalg.norm(m3, axis=1)
    safe = np.where(scale > 1e-12, scale, 1.0)
    rotNorm = m3 / safe[:, None, :]

    flip = np.linalg.det(rotNorm) < 0.0
    rotNorm[flip, :, 2] *= -1.0
    scale[flip, 2] *= -1.0
    return rotNorm, scale


def matricesToQuaternions(rot: np.ndarray) -> np.ndarray:
    """ Batched proper-rotation-matrix -> quaternion (w, x, y, z), Shepperd's method. """
    n = rot.shape[0]
    m00, m01, m02 = rot[:, 0, 0], rot[:, 0, 1], rot[:, 0, 2]
    m10, m11, m12 = rot[:, 1, 0], rot[:, 1, 1], rot[:, 1, 2]
    m20, m21, m22 = rot[:, 2, 0], rot[:, 2, 1], rot[:, 2, 2]
    trace = m00 + m11 + m22

    w = np.empty(n); x = np.empty(n); y = np.empty(n); z = np.empty(n)

    c0 = trace > 0.0
    c1 = (~c0) & (m00 >= m11) & (m00 >= m22)
    c2 = (~c0) & (~c1) & (m11 >= m22)
    c3 = ~(c0 | c1 | c2)

    s = np.sqrt(np.maximum(trace[c0] + 1.0, 0.0)) * 2.0
    w[c0] = 0.25 * s; x[c0] = (m21[c0] - m12[c0]) / s
    y[c0] = (m02[c0] - m20[c0]) / s; z[c0] = (m10[c0] - m01[c0]) / s

    s = np.sqrt(np.maximum(1.0 + m00[c1] - m11[c1] - m22[c1], 0.0)) * 2.0
    w[c1] = (m21[c1] - m12[c1]) / s; x[c1] = 0.25 * s
    y[c1] = (m01[c1] + m10[c1]) / s; z[c1] = (m02[c1] + m20[c1]) / s

    s = np.sqrt(np.maximum(1.0 + m11[c2] - m00[c2] - m22[c2], 0.0)) * 2.0
    w[c2] = (m02[c2] - m20[c2]) / s; x[c2] = (m01[c2] + m10[c2]) / s
    y[c2] = 0.25 * s; z[c2] = (m12[c2] + m21[c2]) / s

    s = np.sqrt(np.maximum(1.0 + m22[c3] - m00[c3] - m11[c3], 0.0)) * 2.0
    w[c3] = (m10[c3] - m01[c3]) / s; x[c3] = (m02[c3] + m20[c3]) / s
    y[c3] = (m12[c3] + m21[c3]) / s; z[c3] = 0.25 * s

    quat = np.stack([w, x, y, z], axis=1)
    norm = np.linalg.norm(quat, axis=1, keepdims=True)
    quat /= np.where(norm > 1e-12, norm, 1.0)
    return quat


def decompose4x4(mats: np.ndarray):
    """ (N, 4, 4) affine matrices -> translation (N, 3), quaternion (N, 4, w x y z), scale (N, 3). """
    offs = mats[:, :3, 3]
    rotNorm, scale = normalizeRotationScale(mats[:, :3, :3].copy())
    quaternion = matricesToQuaternions(rotNorm)
    return offs.astype(np.float32), quaternion.astype(np.float32), scale.astype(np.float32)
