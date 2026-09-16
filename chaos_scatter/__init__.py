# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Chaos Scatter for Blender.

    A standalone scattering addon: distributes instance models over target geometry and bakes
    the placement into a PointCloud + geometry-nodes setup that renders in any engine. The
    placement itself is computed by the Chaos Scatter core (exact parity with V-Ray renders)
    through the V-Ray ZMQ server when the V-Ray for Blender package is installed; rendering
    with V-Ray exports the high-level GeomScatter plugin instead of baked instances.

    Decoupling contract: this addon never requires the vray_blender ADDON to be enabled - only
    its installed package (for the compute backend), and it degrades gracefully without it.
"""

bl_info = {
    "name":        "Chaos Scatter",
    "author":      "Chaos Software",
    "blender":     (4, 5, 0),  # pre-5.1 uses a Mesh carrier (no PointCloud.resize there)
    "location":    "Add menu, Object properties",
    "description": "Chaos Scatter object scattering",
    "doc_url":     "https://documentation.chaos.com/space/VBLD",
    "category":    "Object",
    "version":     (1, 0, 0),
}


def register():
    from chaos_scatter import params, properties, operators, paint, ui, recompute

    # BEFORE anything is registered: checkContract only compares two plain dicts, and raising
    # after properties.register() would leave the propgroups registered with no unregister to
    # match, so every later enable would fail with a confusing "already registered" instead.
    params.checkContract()

    properties.register()
    operators.register()
    paint.register()   # cluster-paint operators (reference the propgroups; register after them)
    ui.register()
    recompute.register()


def unregister():
    from chaos_scatter import properties, operators, paint, ui, recompute
    from chaos_scatter.backend import shutdownBackend

    recompute.unregister()
    # Release the compute session AFTER recompute handlers/timers are gone, so nothing resubmits.
    shutdownBackend()
    ui.unregister()
    paint.unregister()
    operators.unregister()
    properties.unregister()
