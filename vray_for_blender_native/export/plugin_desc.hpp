// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <string>
#include <unordered_map>

#include <base_types.h>


struct PluginAttr {
	using AttrValue = VRayBaseTypes::AttrValue;
	using PluginUpdateFlags = VRayBaseTypes::PluginUpdateFlags;

	PluginAttr() : time(0), flags(PluginUpdateFlags::PluginValueAnimatable) {}
	PluginAttr(const std::string& attrName, const AttrValue& attrValue, bool forceUpdate = false, double time = 0.0) :
		attrName(attrName),
		attrValue(attrValue),
		flags(PluginUpdateFlags::PluginValueAnimatable | (forceUpdate ? PluginUpdateFlags::PluginValueForceUpdate : 0)),
		time(time)
	{}

	std::string  attrName;	  ///< Plugin name
	AttrValue    attrValue;	  ///< Property value
	uint32_t     flags;       ///< Bifield with PluginUpdateFlags
	double       time;        ///< The frame time for the export
};


struct PluginDesc {
	using PluginAttrs = std::unordered_map<std::string, PluginAttr>;
	using AttrValue   = VRayBaseTypes::AttrValue;

	PluginDesc(	const std::string& plugin_name,
				const std::string& plugin_id,
				const std::string& prefix = "") :
		pluginName(plugin_name),
		pluginID(plugin_id) {

		if (!prefix.empty()) {
			pluginName.insert(0, prefix);
		}

	}

	void add(const PluginAttr& attr) {
		pluginAttrs[attr.attrName] = attr;
	}

	void add(const std::string& attrName, const AttrValue& attrValue, const float& time = 0.0f) {
		add(PluginAttr(attrName, attrValue, time));
	}

	std::string  pluginName;    ///< The name of the instance of this plugin
	std::string  pluginID;      ///< The name of the plugin (it's type)
	PluginAttrs  pluginAttrs;   ///< Map of all attribute for this plugin instance
};
