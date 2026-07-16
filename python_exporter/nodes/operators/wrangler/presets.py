# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" V-Ray material presets.
    Ports the preset tables from 3ds Max (`vrender/src/material.cpp`) to
    `BRDFVRayMtl`.

    Two tables are provided:
    - `_PRESETS_VRAYMTL`  classic V-Ray Mtl shading model (brdf_type=GGX).
    - `_PRESETS_OPENPBR`  OpenPBR shading model (same names, different
                          defaults for glossiness / roughness / fog).

    The active table is picked from `option_shading_model` on the node's
    prop group ('0' -> VRayMtl, '1' -> OpenPBR) so applying a preset
    respects the user's current shading model.

    Metal presets use Ole Gulbrandsen's reflectivity + edgetint conversion
    (values already precomputed in the source C++). IOR values come from
    the same provenance comments in material.cpp.
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.nodes.tools import deselectNodes
from vray_blender.nodes.operators.wrangler.poll import (
    isVrayEditor, hasEditTree,
)


# Preset tuple columns. Same order as 3ds Max's `struct Preset` so diffing
# the C++ table against this file stays trivial.
#
# 0:  name
# 1:  diffuse          (r, g, b)
# 2:  brdf_type        (int - 4 = GGX, stored as string on the enum)
# 3:  reflect          (r, g, b)
# 4:  reflect_glossiness
# 5:  fresnel          (bool)
# 6:  option_glossy_fresnel (bool)
# 7:  fresnel_ior_lock (bool)
# 8:  metalness
# 9:  option_use_roughness (bool)
# 10: anisotropy
# 11: reflect_depth    (int)
# 12: option_reflect_on_back (bool)
# 13: refract          (r, g, b)
# 14: refract_glossiness
# 15: refract_ior
# 16: fog_color        (r, g, b)
# 17: fog_mult         (user-facing; V-Ray inverts at export)
# 18: refract_depth    (int)
# 19: dispersion_on    (bool)
# 20: dispersion       (Abbe number)
# 21: gtr_gamma        ("GGX Tail Falloff")
# 22: sheen_color      (r, g, b)
# 23: sheen_glossiness
# 24: refract_thin_walled (bool)
# 25: thin_film_on     (bool)
# 26: thin_film_thickness_min
# 27: thin_film_thickness_max
# 28: thin_film_ior
# 29: coat_amount
# 30: coat_ior
# 31: coat_darkening
# 32: coat_anisotropy
# 33: coat_anisotropy_rotation


_PRESETS_VRAYMTL = [
    ('Aluminium',                   (0.9056,0.9154,0.9219), 4, (0.962,0.978,0.987), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.002,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Aluminium (Rough)',           (0.9056,0.9154,0.9219), 4, (0.962,0.978,0.987), 0.12,  True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.002,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Aluminium (Brushed)',         (0.9056,0.9154,0.9219), 4, (0.962,0.978,0.987), 0.3,   True, True, True, 1.0, True, 0.8, 8,  False, (0.0,0.0,0.0),     1.0, 1.002,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Chrome',                      (0.5556,0.5545,0.5548), 4, (0.570,0.557,0.689), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.03,    (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Copper',                      (0.9352,0.6235,0.5383), 4, (0.996,0.906,0.848), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.21901, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Copper (Rough)',              (0.9352,0.6235,0.5383), 4, (0.996,0.906,0.848), 0.1,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.21901, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Gold',                        (0.9565,0.7916,0.4082), 4, (0.998,0.981,0.766), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.35002, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Gold (Rough)',                (0.9565,0.7916,0.4082), 4, (0.998,0.981,0.766), 0.15,  True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.35002, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Iron',                        (0.8898,0.8782,0.8256), 4, (0.948,0.962,0.955), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.006,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Lead',                        (0.6578,0.6614,0.6904), 4, (0.747,0.753,0.819), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.016,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Silver',                      (0.9898,0.9831,0.9802), 4, (0.999,0.999,0.999), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.082,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Silver (Rough)',              (0.9898,0.9831,0.9802), 4, (0.999,0.999,0.999), 0.11,  True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.082,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Diamond',                     (0.0,0.0,0.0),          4, (1.0,1.0,1.0),       0.98,  True, True, True, 0.0, False,0.0, 10, True,  (1.0,1.0,1.0),     1.0, 2.42,    (1.0,1.0,1.0),     1.0,  10, True,  15.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass',                       (0.0,0.0,0.0),          4, (0.914,0.914,0.914), 1.0,   True, True, True, 0.0, False,0.0, 8,  False, (0.977,0.977,0.977), 1.0, 1.517, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass (Frosted)',             (0.0,0.0,0.0),          4, (0.914,0.914,0.914), 0.75,  True, True, True, 0.0, False,0.0, 8,  False, (0.977,0.977,0.977), 0.8, 1.517, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass (Tinted)',              (0.0,0.0,0.0),          4, (0.914,0.914,0.914), 1.0,   True, True, True, 0.0, False,0.0, 8,  False, (0.977,0.977,0.977), 1.0, 1.517, (0.702,0.95,0.702),1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass (Architectural)',       (0.0,0.0,0.0),          4, (0.471,0.471,0.471), 0.975, True, True, True, 0.0, False,0.0, 25, True,  (1.0,1.0,1.0),     1.0, 1.6,     (0.980,1.0,0.992), 1.0,  25, False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Water',                       (0.0,0.0,0.0),          4, (0.784,0.784,0.784), 1.0,   True, True, True, 0.0, False,0.0, 8,  False, (1.0,1.0,1.0),     1.0, 1.33,    (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Chocolate',                   (0.032,0.019,0.009),    4, (0.442,0.442,0.442), 0.68,  True, True, True, 0.0, False,0.0, 8,  False, (0.195,0.195,0.195), 0.6, 1.59,  (0.184,0.039,0.007), 2.0, 8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Ceramic',                     (0.7764,0.6941,0.6352), 4, (0.996,1.0,0.988),   0.99,  True, True, True, 0.0, False,0.0, 8,  False, (0.0,0.0,0.0),     0.6, 1.504,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Plastic',                     (0.745,0.545,0.0),      4, (0.471,0.471,0.471), 0.85,  True, True, True, 0.0, False,0.0, 8,  True,  (0.0,0.0,0.0),     1.0, 1.8,     (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Rubber',                      (0.008,0.01,0.01),      4, (0.929,0.975,1.0),   0.472, True, True, True, 0.0, False,0.0, 8,  False, (0.0,0.0,0.0),     0.6, 1.468,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Red Velvet',                  (0.059,0.0,0.004),      4, (0.231,0.169,0.196), 0.248, True, True, True, 0.0, False,0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.403,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 1.946, (0.376,0.039,0.059),0.878, False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('White Satin',                 (0.772,0.761,0.643),    4, (0.365,0.294,0.282), 0.634, True, True, True, 0.0, False,0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.7,     (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.035, (0.294,0.255,0.235),0.323, False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Pink Satin',                  (0.451,0.023,0.133),    4, (0.263,0.267,0.263), 0.543, True, True, True, 0.0, False,0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.614,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.259,0.176,0.243),0.673, False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Soap Bubble',                 (0.0,0.0,0.0),          4, (1.0,1.0,1.0),       1.0,   True, True, True, 0.0, False,0.0, 8,  False, (1.0,1.0,1.0),     1.0, 1.0,     (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   True,  True,  500.0, 600.0, 1.35, 1.0, 1.35, 0.0, 0.0, 0.0),
]


_PRESETS_OPENPBR = [
    ('Aluminium',                   (0.9056,0.9154,0.9219), 4, (0.962,0.978,0.987), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.002,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Aluminium (Rough)',           (0.9056,0.9154,0.9219), 4, (0.962,0.978,0.987), 0.12,  True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.002,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Aluminium (Brushed)',         (0.9056,0.9154,0.9219), 4, (0.962,0.978,0.987), 0.3,   True, True, True, 1.0, True, 0.8, 8,  False, (0.0,0.0,0.0),     1.0, 1.002,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Chrome',                      (0.5556,0.5545,0.5548), 4, (0.570,0.557,0.689), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.03,    (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Copper',                      (0.9352,0.6235,0.5383), 4, (0.996,0.906,0.848), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.21901, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Copper (Rough)',              (0.9352,0.6235,0.5383), 4, (0.996,0.906,0.848), 0.1,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.21901, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Gold',                        (0.9565,0.7916,0.4082), 4, (0.998,0.981,0.766), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.35002, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Gold (Rough)',                (0.9565,0.7916,0.4082), 4, (0.998,0.981,0.766), 0.15,  True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.35002, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Iron',                        (0.8898,0.8782,0.8256), 4, (0.948,0.962,0.955), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.006,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Lead',                        (0.6578,0.6614,0.6904), 4, (0.747,0.753,0.819), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.016,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Silver',                      (0.9898,0.9831,0.9802), 4, (0.999,0.999,0.999), 0.0,   True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.082,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Silver (Rough)',              (0.9898,0.9831,0.9802), 4, (0.999,0.999,0.999), 0.11,  True, True, True, 1.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     1.0, 1.082,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Diamond',                     (0.0,0.0,0.0),          4, (1.0,1.0,1.0),       0.02,  True, True, True, 0.0, True, 0.0, 10, True,  (1.0,1.0,1.0),     0.0, 2.42,    (1.0,1.0,1.0),     1.0,  10, True,  15.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass',                       (0.0,0.0,0.0),          4, (0.914,0.914,0.914), 0.0,   True, True, True, 0.0, True, 0.0, 8,  False, (0.977,0.977,0.977), 0.0, 1.517, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass (Frosted)',             (0.0,0.0,0.0),          4, (0.914,0.914,0.914), 0.25,  True, True, True, 0.0, True, 0.0, 8,  False, (0.977,0.977,0.977), 0.2, 1.517, (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass (Tinted)',              (0.0,0.0,0.0),          4, (0.914,0.914,0.914), 0.0,   True, True, True, 0.0, True, 0.0, 8,  False, (0.977,0.977,0.977), 0.0, 1.517, (0.702,0.95,0.702),1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Glass (Architectural)',       (0.0,0.0,0.0),          4, (0.471,0.471,0.471), 0.025, True, True, True, 0.0, True, 0.0, 25, True,  (1.0,1.0,1.0),     0.0, 1.6,     (0.980,1.0,0.992), 1.0,  25, False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Water',                       (0.0,0.0,0.0),          4, (0.784,0.784,0.784), 0.0,   True, True, True, 0.0, True, 0.0, 8,  False, (1.0,1.0,1.0),     0.0, 1.33,    (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Chocolate',                   (0.032,0.019,0.009),    4, (0.442,0.442,0.442), 0.32,  True, True, True, 0.0, True, 0.0, 8,  False, (0.195,0.195,0.195), 0.4, 1.59,  (0.184,0.039,0.007), 2.0, 8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Ceramic',                     (0.7764,0.6941,0.6352), 4, (0.996,1.0,0.988),   0.01,  True, True, True, 0.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     0.4, 1.504,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Plastic',                     (0.745,0.545,0.0),      4, (0.471,0.471,0.471), 0.15,  True, True, True, 0.0, True, 0.0, 8,  True,  (0.0,0.0,0.0),     0.0, 1.8,     (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Rubber',                      (0.008,0.01,0.01),      4, (0.929,0.975,1.0),   0.528, True, True, True, 0.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     0.4, 1.468,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Red Velvet',                  (0.059,0.0,0.004),      4, (0.231,0.169,0.196), 0.752, True, True, True, 0.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     0.0, 1.403,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 1.946, (0.376,0.039,0.059),0.4,   False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('White Satin',                 (0.772,0.761,0.643),    4, (0.365,0.294,0.282), 0.366, True, True, True, 0.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     0.0, 1.7,     (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.035, (0.294,0.255,0.235),0.323, False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Pink Satin',                  (0.451,0.023,0.133),    4, (0.263,0.267,0.263), 0.457, True, True, True, 0.0, True, 0.0, 8,  False, (0.0,0.0,0.0),     0.0, 1.614,   (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.259,0.176,0.243),0.673, False, False, 250.0, 400.0, 1.47, 0.0, 1.6,  0.0, 0.0, 0.0),
    ('Soap Bubble',                 (0.0,0.0,0.0),          4, (1.0,1.0,1.0),       0.0,   True, True, True, 0.0, True, 0.0, 8,  False, (1.0,1.0,1.0),     0.0, 1.0,     (1.0,1.0,1.0),     1.0,  8,  False, 50.0, 2.0,   (0.0,0.0,0.0),     0.8,   True,  True,  500.0, 600.0, 1.35, 1.0, 1.35, 0.0, 0.0, 0.0),
]


# Maps preset-column index -> (attr_name, setter_kind). Set by a single walk
# rather than 34 lines of assignments in `_applyPreset` so the column
# order can be cross-checked against the C++ struct at a glance.
#
# Kinds:
#   'rgb'   - (r, g, b) tuple; we promote to (r, g, b, 1.0) before set.
#   'enum'  - int; stringified for V-Ray's enum prop keys.
#   'bool'  - plain bool.
#   other   - direct assignment.
_COLUMNS = [
    # 0 is the preset name; skipped.
    (1,  'diffuse',                    'rgb'),
    (2,  'brdf_type',                  'enum'),
    (3,  'reflect',                    'rgb'),
    (4,  'reflect_glossiness',         'float'),
    (5,  'fresnel',                    'bool'),
    (6,  'option_glossy_fresnel',      'bool'),
    (7,  'fresnel_ior_lock',           'bool'),
    (8,  'metalness',                  'float'),
    (9,  'option_use_roughness',       'bool'),
    (10, 'anisotropy',                 'float'),
    (11, 'reflect_depth',              'int'),
    (12, 'option_reflect_on_back',     'bool'),
    (13, 'refract',                    'rgb'),
    (14, 'refract_glossiness',         'float'),
    (15, 'refract_ior',                'float'),
    (16, 'fog_color',                  'rgb'),
    (17, 'fog_mult',                   'float'),
    (18, 'refract_depth',              'int'),
    (19, 'dispersion_on',              'bool'),
    (20, 'dispersion',                 'float'),
    (21, 'gtr_gamma',                  'float'),
    (22, 'sheen_color',                'rgb'),
    (23, 'sheen_glossiness',           'float'),
    (24, 'refract_thin_walled',        'bool'),
    (25, 'thin_film_on',               'bool'),
    (26, 'thin_film_thickness_min',    'float'),
    (27, 'thin_film_thickness_max',    'float'),
    (28, 'thin_film_ior',              'float'),
    (29, 'coat_amount',                'float'),
    (30, 'coat_ior',                   'float'),
    (31, 'coat_darkening',             'float'),
    (32, 'coat_anisotropy',            'float'),
    (33, 'coat_anisotropy_rotation',   'float'),
]


# Some attrs are backed by a meta-socket whose vray_attr differs from the
# plugin attr name. Most notably `fog_color` is hidden on the node; the
# visible socket is `fog_color_colortex` (a COLOR_TEXTURE meta), which
# exports to `fog_color` when unlinked. The socket lookup pass uses this
# map so the node UI and export both pick up the preset value.
_ATTR_SOCKET_ALIASES = {
    'fog_color': 'fog_color_colortex',
}


# Attrs the 3ds Max `setPreset` always forces regardless of preset row.
# Replicated here so presets behave identically across DCCs.
_FORCED_ATTRS = {
    'refract_affect_shadows': True,
}


def _setSocketValue(socket, kind, value):
    """ Write a preset value into a V-Ray node socket. Color sockets can be
        size 3 or size 4 depending on the socket class, so probe the existing
        value's length rather than assuming.

        Returns True on success, False if the socket can't accept the value.
    """
    socketValue = getattr(socket, 'value', None)
    if socketValue is None:
        return False
    try:
        if kind == 'rgb':
            if hasattr(socketValue, '__len__') and len(socketValue) == 4:
                socket.value = (value[0], value[1], value[2], 1.0)
            else:
                socket.value = (value[0], value[1], value[2])
        elif kind == 'bool':
            socket.value = bool(value)
        else:
            socket.value = value
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def _applyPreset(node, propGroup, row):
    """ Apply a preset tuple to a BRDFVRayMtl node.
        Color and float-like attrs are written to their input sockets
        (so the node UI reflects the change and exportUnlinked picks them
        up). Everything else - enums, bools without sockets - goes on the
        propGroup directly. When in doubt the code writes both so either
        export path stays coherent.
    """
    for colIdx, attrName, kind in _COLUMNS:
        value = row[colIdx]
        wroteSocket = False

        # Try the socket first. Colors and floats live behind input sockets
        # and the socket's `value` is what the node draws + what the export
        # picks up when nothing is plugged in. Some attrs (`fog_color`) are
        # driven by a meta-socket whose vray_attr differs from the plugin
        # attr, so consult the alias map before giving up.
        socketAttr = _ATTR_SOCKET_ALIASES.get(attrName, attrName)
        socket = getInputSocketByAttr(node, socketAttr)
        if socket is not None:
            wroteSocket = _setSocketValue(socket, kind, value)

        # Enums, some bools, and attrs with no socket live on the prop group.
        # Also mirror successful socket writes onto the prop group so any
        # read path that goes directly through the prop group stays in sync.
        if hasattr(propGroup, attrName):
            try:
                if kind == 'rgb':
                    # Plugin COLOR props are size-3, TEXTURE-backed props are
                    # size-4. Probe the existing value so we send the right
                    # number of components.
                    existing = getattr(propGroup, attrName)
                    if hasattr(existing, '__len__') and len(existing) == 3:
                        setattr(propGroup, attrName, (value[0], value[1], value[2]))
                    else:
                        setattr(propGroup, attrName, (value[0], value[1], value[2], 1.0))
                elif kind == 'enum':
                    setattr(propGroup, attrName, str(value))
                elif kind == 'bool':
                    setattr(propGroup, attrName, bool(value))
                else:
                    setattr(propGroup, attrName, value)
            except (TypeError, ValueError, AttributeError):
                # Stricter prop (ENUM with a limited set, range-clamped
                # float) refuses the preset value. Skip rather than fail.
                if not wroteSocket:
                    continue

    # Constants the 3ds Max implementation forces alongside the preset row.
    for attrName, value in _FORCED_ATTRS.items():
        socketAttr = _ATTR_SOCKET_ALIASES.get(attrName, attrName)
        socket = getInputSocketByAttr(node, socketAttr)
        if socket is not None:
            _setSocketValue(socket, 'bool', value)
        if hasattr(propGroup, attrName):
            try:
                setattr(propGroup, attrName, value)
            except (TypeError, ValueError, AttributeError):
                pass


def _slug(name: str) -> str:
    return name.upper().replace(' ', '_').replace('(', '').replace(')', '')


# Built once at module load - EnumProperty items callbacks fire on every redraw
# of any panel that references the prop, and the source tables never change.
_PRESET_ITEMS = tuple(
    (_slug(row[0]), row[0], f"Apply the {row[0]} preset")
    for row in _PRESETS_VRAYMTL
)
_PRESET_BY_SLUG_VRAYMTL = {_slug(row[0]): row for row in _PRESETS_VRAYMTL}
_PRESET_BY_SLUG_OPENPBR = {_slug(row[0]): row for row in _PRESETS_OPENPBR}


def _presetItems(_self, _context):
    return _PRESET_ITEMS


def _lookupPreset(key: str, openPbr: bool):
    table = _PRESET_BY_SLUG_OPENPBR if openPbr else _PRESET_BY_SLUG_VRAYMTL
    return table.get(_slug(key))


class VRAY_OT_WR_apply_preset(VRayOperatorBase):
    """ Add a new BRDFVRayMtl node with the chosen preset applied. """
    bl_idname = "vray.wr_apply_preset"
    bl_label = "Apply V-Ray Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset: bpy.props.EnumProperty(
        name="Preset",
        items=_presetItems,
    )

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context)

    def invoke(self, context, event):
        # Capture the cursor position in node space so execute can place the
        # new node under the mouse, matching Blender's Add Node menu behaviour.
        space = context.space_data
        space.cursor_location_from_region(event.mouse_region_x, event.mouse_region_y)
        self._cursor = tuple(space.cursor_location)
        return self.execute(context)

    def execute(self, context):
        ntree = context.space_data.edit_tree

        try:
            mtl = ntree.nodes.new('VRayNodeBRDFVRayMtl')
        except RuntimeError as ex:
            self.report({'WARNING'}, f"Could not create BRDFVRayMtl: {ex}")
            return {'CANCELLED'}

        cursor = getattr(self, '_cursor', None)
        if cursor is not None:
            mtl.location = cursor
        else:
            mtl.location = (0.0, 0.0)

        propGroup = getattr(mtl, 'BRDFVRayMtl', None)
        if propGroup is None:
            self.report({'WARNING'}, "BRDFVRayMtl prop group missing on target node")
            return {'CANCELLED'}

        openPbr = getattr(propGroup, 'option_shading_model', '0') == '1'
        row = _lookupPreset(self.preset, openPbr)
        if row is None:
            self.report({'WARNING'}, f"Unknown preset: {self.preset!r}")
            return {'CANCELLED'}

        _applyPreset(mtl, propGroup, row)
        mtl.label = row[0]

        deselectNodes(ntree)
        mtl.select = True
        ntree.nodes.active = mtl
        # When invoked interactively (cursor known), hand the node to the
        # user exactly like Blender's Add Node menu does.
        if cursor is not None:
            bpy.ops.node.translate_attach('INVOKE_DEFAULT')

        ntree.update_tag()
        self.report({'INFO'}, f"Added preset: {row[0]} ({'OpenPBR' if openPbr else 'VRayMtl'})")
        return {'FINISHED'}


class VRAY_MT_WR_presets(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_presets"
    bl_label = "V-Ray Material Presets"

    def draw(self, context):
        layout = self.layout
        # Organise by category for readability.
        categories = (
            ("Metals", ('Aluminium', 'Aluminium (Rough)', 'Aluminium (Brushed)',
                        'Chrome', 'Copper', 'Copper (Rough)', 'Gold', 'Gold (Rough)',
                        'Iron', 'Lead', 'Silver', 'Silver (Rough)')),
            ("Glass & Liquids", ('Diamond', 'Glass', 'Glass (Frosted)', 'Glass (Tinted)',
                                 'Glass (Architectural)', 'Water', 'Soap Bubble')),
            ("Organics", ('Chocolate', 'Ceramic', 'Plastic', 'Rubber',
                          'Red Velvet', 'White Satin', 'Pink Satin')),
        )
        for header, names in categories:
            layout.label(text=header)
            for name in names:
                op = layout.operator("vray.wr_apply_preset", text=name)
                op.preset = _slug(name)
            layout.separator()


def getRegClasses():
    return (
        VRAY_OT_WR_apply_preset,
        VRAY_MT_WR_presets,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
