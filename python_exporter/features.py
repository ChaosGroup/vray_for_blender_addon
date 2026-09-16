# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Feature flags for the V-Ray for Blender addon.

    This is the centralized place where in-development features can be turned on or
    off. A disabled feature is hidden from the UI and from Blender's F3 operator
    search, while all of its underlying code remains in the addon. The goal is to
    present only the officially-supported featureset to the user; a user manually
    re-enabling a hidden feature is not a concern.

    Each feature has:
      * a string name    - a stable identifier used in code and logs
      * a default state  - whether the feature is enabled (visible) by default

    To gate a feature, call isEnabled() where the feature's classes are registered and
    where its UI is drawn, and act only when it returns True. There is no class-level
    tagging - gating lives at the individual registration/draw sites.

    The default on/off state of every feature is defined here (the second tuple
    element). All features can be force-enabled at startup with the
    --vray-with-all-features command-line flag (intended for internal testing and
    development):

        blender.exe <blender args> -- --vray-with-all-features

    When --vray-with-all-features is present, every feature is enabled regardless of its
    default. When it is absent, the per-feature defaults defined below are used.
"""

import sys

from enum import Enum

from vray_blender import debug


class Feature(Enum):
    """ All feature flags of the project.

        Each member carries (string name, default enabled state):
          * string name - stable identifier, handy for logging and debugging
          * enabled     - whether the feature is enabled (visible) by default
    """
    # Tuples of (name, default enabled)
    GAUSSIAN_SPLATS  = ("gaussian_splats",  True)
    OBJECT_LISTER    = ("object_lister",    True)
    VRSCENE_IMPORTER = ("vrscene_importer", True)

    def __init__(self, featureName: str, enabled: bool):
        self.featureName = featureName
        self.enabled = enabled


# The command-line flag that force-enables every feature, ignoring the per-feature
# defaults. It takes no value.
_WITH_ALL_FEATURES_ARG = "--vray-with-all-features"

# Runtime enabled state, keyed by Feature. Populated in init() from the per-feature
# defaults, or all-enabled when --vray-with-all-features is passed.
_enabled: dict[Feature, bool] = {}


def isEnabled(feature: Feature) -> bool:
    """ Return True if the feature is currently enabled (visible to the user). """
    return _enabled.get(feature, feature.enabled)


def init():
    """ Initialize the feature states.

        All features are enabled when the --vray-with-all-features command-line flag is
        present; otherwise the per-feature defaults defined above are used.

        Called once during addon registration, before any UI or operators are
        registered, so that gating decisions see the final feature states.
    """
    withAllFeatures = _WITH_ALL_FEATURES_ARG in sys.argv

    for feature in Feature:
        _enabled[feature] = True if withAllFeatures else feature.enabled

    if withAllFeatures:
        debug.printAlways(f"All V-Ray features force-enabled via {_WITH_ALL_FEATURES_ARG}.")
