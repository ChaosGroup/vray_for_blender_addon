# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Preview of V-Ray node graphs.

    bake.py evaluates a texture subgraph by having V-Ray sample it over UV space through the
    AppSDK sampler, with no render job. thumbs/ shows the results as per-node thumbnails in the
    V-Ray node editor.
"""
