# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# V-Ray RenderElement::Type ID for the Cryptomatte channel. Cryptomatte is a multi-layer
# pass and isn't a regular entry in RE - it is requested separately via this constant.
CRYPTOMATTE_CHANNEL_TYPE = 158

# V-Ray RenderElement::Type ID for ObjectSelect (USER + 5006 = 1000 + 5006).
# ObjectSelect is a multi-layer element: layerIndex 0=matte, 1=filter, 2=alpha
# per AppSDK GetDataOptions::layerIndex semantics.
OBJECT_SELECT_CHANNEL_TYPE = 6006

# ObjectSelect sub-layer indices (must match VRay::RenderElement::GetDataOptions::layerIndex).
OBJECT_SELECT_SUBINDEX_MATTE  = 0
OBJECT_SELECT_SUBINDEX_FILTER = 1
OBJECT_SELECT_SUBINDEX_ALPHA  = 2


def cryptomatteTypePrefix(idType) -> str:
    """ Map a V-Ray RenderChannelCryptomatte.id_type to the Blender pass name TypePrefix.
        0 = Node name        -> CryptoObject
        1 = Material name    -> CryptoMaterial
        anything else        -> CryptoObject (unsupported by Blender's compositor picking;
                                              the engine emits a one-shot warning).
    """
    return "CryptoMaterial" if str(idType) == "1" else "CryptoObject"


_BL_IDNAME_CRYPTOMATTE   = "VRayNodeRenderChannelCryptomatte"
_BL_IDNAME_OBJECT_SELECT = "VRayNodeRenderChannelObjectSelect"
_BL_IDNAME_DENOISER      = "VRayNodeRenderChannelDenoiser"
_BL_IDNAME_ENHANCER      = "VRayNodeRenderChannelEnhancer"

_SPECIAL_CHANNEL_IDNAMES = frozenset((
    _BL_IDNAME_CRYPTOMATTE,
    _BL_IDNAME_OBJECT_SELECT,
    _BL_IDNAME_DENOISER,
    _BL_IDNAME_ENHANCER,
))


def iterChannelLinks(world):
    """ Yield (inSock, fromNode) for each enabled channel-output input that resolves to a node. """
    if not (world and world.node_tree):
        return
    from vray_blender.nodes import utils as NodesUtils
    from vray_blender.exporting.tools import getFarNodeLink, socketHasActiveNearLinks

    channelsNode = NodesUtils.getChannelsOutputNode(world.node_tree)
    if not channelsNode:
        return
    for inSock in channelsNode.inputs:
        if not (socketHasActiveNearLinks(inSock) and getattr(inSock, 'use', False)):
            continue
        link = getFarNodeLink(inSock)
        if link and link.from_node:
            yield inSock, link.from_node


def enumerateCryptomatteNodes(world):
    """ Yield (node, instanceName, typePrefix, idType, numPasses) for each wired Cryptomatte. """
    for _inSock, node in iterChannelLinks(world):
        if node.bl_idname != _BL_IDNAME_CRYPTOMATTE:
            continue
        cryptoProps = node.RenderChannelCryptomatte
        instanceName = cryptoProps.name or "Cryptomatte"
        idType = cryptoProps.id_type
        numPasses = (cryptoProps.num_level + 1) // 2
        yield node, instanceName, cryptomatteTypePrefix(idType), str(idType), numPasses


def cryptomattePassName(typePrefix: str, instanceName: str, layerIdx: int) -> str:
    return f"{typePrefix}_{instanceName}{layerIdx:02d}"


def enumerateObjectSelectNodes(world):
    """ Yield (node, instanceName) for each wired ObjectSelect. """
    for _inSock, node in iterChannelLinks(world):
        if node.bl_idname != _BL_IDNAME_OBJECT_SELECT:
            continue
        yield node, (node.RenderChannelObjectSelect.name or "Object Select")


def objectSelectPassName(instanceName: str, subIndex: int) -> str:
    if subIndex == OBJECT_SELECT_SUBINDEX_MATTE:
        return instanceName
    if subIndex == OBJECT_SELECT_SUBINDEX_FILTER:
        return f"{instanceName}_filter"
    if subIndex == OBJECT_SELECT_SUBINDEX_ALPHA:
        return f"{instanceName}_alpha"
    raise ValueError(f"Unknown ObjectSelect subIndex {subIndex}")

# Maps a V-Ray render-element node's bl_label to the Blender RenderPass name we want
# users to see in the compositor. Only entries where the two differ need to be listed.
NODE_LABEL_TO_PASS_NAME = {
    "Z Depth": "Depth",
}


def _isDenoiserWired(world) -> bool:
    return any(node.bl_idname == _BL_IDNAME_DENOISER for _, node in iterChannelLinks(world))


def enumerateSpecialPasses(world):
    """ Yield (passName, channelType, instanceName) for passes not tied to a channel node:
        'Effects Result' (always-on) and 'Denoised' (when a denoiser is wired -- its
        instance name is read from the singleton RenderChannelDenoiser on world.vray).
        Both update_render_passes and _setupElementPasses must walk this list.
    """
    yield ("Effects Result", RE["Effects Result"]["channelType"], "Effects Result")

    if not _isDenoiserWired(world):
        return

    denoiserProps = getattr(world.vray, "RenderChannelDenoiser", None)
    if denoiserProps is None:
        return
    denoiserInstanceName = getattr(denoiserProps, "name", "") or "Denoiser"
    yield ("Denoised", RE["Denoised"]["channelType"], denoiserInstanceName)


# Channel labels we've already warned have no compositor mapping, to avoid spamming
# the report log on every update_render_passes / setup poll.
_warnedUnmappedChannels: set[str] = set()

# Render elements that are intentionally VFB-only: they have no Blender compositor pass
# by design, so the "missing mapping" warning would just be noise for them.
_VFB_ONLY_PASSES: frozenset[str] = frozenset({"Lighting Analysis", "Light Mix"})


def resetUnmappedChannelWarnings():
    """ Clear the warning dedup set so a newly loaded scene re-warns about its own
        unmapped channels. Called from the load_post handler. """
    _warnedUnmappedChannels.clear()


def enumerateGenericChannelNodes(world):
    """ Yield (node, channelType, instanceName, reType) for each wired non-special channel node. """
    for _inSock, node in iterChannelLinks(world):
        if node.bl_idname in _SPECIAL_CHANNEL_IDNAMES:
            continue
        passName = NODE_LABEL_TO_PASS_NAME.get(node.bl_label, node.bl_label)
        reInfo = RE.get(passName)
        if not reInfo or "channelType" not in reInfo:
            # VFB-only elements (Lighting Analysis, Light Mix) have no compositor pass by
            # design - skip the warning for them; warn once for anything else unmapped.
            if passName not in _VFB_ONLY_PASSES and passName not in _warnedUnmappedChannels:
                _warnedUnmappedChannels.add(passName)
                from vray_blender import debug
                debug.reportAsync("WARNING",
                    f"Render element '{passName}' has no Blender compositor mapping; it renders "
                    "in the V-Ray VFB but will not appear as a render pass.")
            continue
        # Fall back to the pass name (derived from bl_label) if the node has no
        # plugin-name property.
        instanceName = passName
        vrayPluginType = getattr(node, "vray_plugin", "")
        if vrayPluginType:
            pluginProps = getattr(node, vrayPluginType, None)
            if pluginProps is not None:
                name = getattr(pluginProps, "name", "")
                if name:
                    instanceName = name
        yield node, reInfo["channelType"], instanceName, reInfo.get("type", "color")

# Render element definitions keyed by Blender RenderPass name. The channelType values
# are VRay::RenderElement::Type integers from the AppSDK.
RE = {
    # Built-in plugin nodes
    "Normals":                        { "type": "vector", "channelType": 123 },
    "Depth":                          { "type": "value",  "channelType": 117 },
    "Bump Normals":                   { "type": "vector", "channelType": 131 },
    "Velocity":                       { "type": "vector", "channelType": 113 },
    "Render ID":                      { "type": "value",  "channelType": 114 },
    "Object ID":                      { "type": "value",  "channelType": 116 },

    # Beauty (RenderChannelColor)
    "Atmospheric Effects":            { "type": "color",  "channelType": 100 },
    "Diffuse":                        { "type": "color",  "channelType": 101 },
    "Reflection":                     { "type": "color",  "channelType": 102 },
    "Refraction":                     { "type": "color",  "channelType": 103 },
    "Self-Illumination":              { "type": "color",  "channelType": 104 },
    "Shadow":                         { "type": "color",  "channelType": 105 },
    "Specular":                       { "type": "color",  "channelType": 106 },
    "Lighting":                       { "type": "color",  "channelType": 107 },
    "GI":                             { "type": "color",  "channelType": 108 },
    "Caustics":                       { "type": "color",  "channelType": 109 },
    "Raw GI":                         { "type": "color",  "channelType": 110 },
    "Raw Lighting":                   { "type": "color",  "channelType": 111 },
    "Raw Shadow":                     { "type": "color",  "channelType": 112 },
    # MTLID (115) is RenderChannelColor on the V-Ray side -> 3 channels RGB.
    "Material ID":                    { "type": "color",  "channelType": 115 },
    # MULTIMATTE (6004) is RenderChannelMultiMatte -> 3 channels RGB matte.
    "Multi Matte":                    { "type": "color",  "channelType": 6004 },
    # MULTIMATTE_ID (6005) is RenderChannelMtlID -> single int per pixel.
    "Multi Matte ID":                 { "type": "value",  "channelType": 6005 },
    "Reflection Filter":              { "type": "color",  "channelType": 118 },
    "Raw Reflection":                 { "type": "color",  "channelType": 119 },
    "Refraction Filter":              { "type": "color",  "channelType": 120 },
    "Raw Refraction":                 { "type": "color",  "channelType": 121 },
    "Background":                     { "type": "color",  "channelType": 124 },
    "Matte Shadow":                   { "type": "color",  "channelType": 128 },
    "Total Light":                    { "type": "color",  "channelType": 129 },
    "Raw Total Light":                { "type": "color",  "channelType": 130 },
    "Sample Rate":                    { "type": "color",  "channelType": 132 },
    "SSS":                            { "type": "color",  "channelType": 133 },

    # Glossiness (single value)
    "Reflection Glossiness":          { "type": "value",  "channelType": 135 },
    "Reflection Highlight Glossiness": { "type": "value", "channelType": 136 },
    "Refraction Glossiness":          { "type": "value",  "channelType": 137 },
    "Reflection IOR":                 { "type": "color",  "channelType": 140 },
    "Metalness":                      { "type": "value",  "channelType": 165 },

    # Toon
    "Toon":                           { "type": "color",  "channelType": 154 },
    "Toon Lighting":                  { "type": "color",  "channelType": 180 },
    "Toon Specular":                  { "type": "color",  "channelType": 181 },

    # Sheen
    "Sheen":                          { "type": "color",  "channelType": 6011 },  # SHEEN = USER + 5011
    "Sheen Reflection":               { "type": "color",  "channelType": 194 },
    "Raw Sheen Filter":               { "type": "color",  "channelType": 183 },
    "Raw Sheen Reflection":           { "type": "color",  "channelType": 184 },
    "Sheen Filter":                   { "type": "color",  "channelType": 185 },
    "Sheen Glossiness":               { "type": "value",  "channelType": 186 },

    # Always-on passes
    "Effects Result":                 { "type": "color",  "channelType": 153 },
    "Denoised":                       { "type": "color",  "channelType": 144 },

    # Coat
    "Coat":                           { "type": "color",  "channelType": 6010 },  # COAT = USER + 5010
    "Coat Reflection":                { "type": "color",  "channelType": 195 },
    "Raw Coat Filter":                { "type": "color",  "channelType": 189 },
    "Raw Coat Reflection":            { "type": "color",  "channelType": 190 },
    "Coat Filter":                    { "type": "color",  "channelType": 191 },
    "Coat Glossiness":                { "type": "value",  "channelType": 192 },

    # Utility
    "Coverage":                       { "type": "value",  "channelType": 6001 },  # COVERAGE = USER + 5001 (1ch BW)
    "DR Bucket":                      { "type": "color",  "channelType": 134 },
    "Extra Tex":                      { "type": "color",  "channelType": 6007 },  # EXTRA_TEX = USER + 5007
}
