// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <vector>

#include <nanobind/nanobind.h>

#include "base_types.h"

namespace VrayZmqWrapper {
	struct PluginPropertyValueData;
}

namespace VRayForBlender {
namespace Interop {

namespace nb = nanobind;

/// Convert an imported AttrValue to the Python shape of the vrscene dict: scalars,
/// float tuples for colors/vectors/matrices, "name::output" strings for plugin refs.
/// preferNdarray turns numeric lists into zero-copy numpy arrays whose capsule keeps
/// the AttrList alive; it propagates into nested lists (keeps map_channels fast).
nb::object attrValueToPython(const VRayBaseTypes::AttrValue& value, bool preferNdarray);

/// Convert plugin property values read back from V-Ray to a list of
/// (pluginName, propertyName, value) tuples. The caller must hold the GIL.
nb::list pluginPropertyValuesToPython(const std::vector<VrayZmqWrapper::PluginPropertyValueData>& values);

} // namespace Interop
} // namespace VRayForBlender
