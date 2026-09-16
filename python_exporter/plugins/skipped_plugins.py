# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Plugins which are not skipped for import/conversion purposes or plugins that were simply hidden from
# the menus but should still remain active and just not show up in node creation menus.
HIDDEN_PLUGINS = (
    "TexFresnel",
    "TexBlend",
    "TexColorCorrect",
    "TexVectorProduct",
    "TexVectorToColor",
    "TexUVW",
    "UVWGenObjectBBox",
    "TexNormalMapFlip",
    "TexBlendBumpNormal",
    "TexCondition2",
    # Registered so the .vrscene importer can create + render them, but not offered in the
    # node-add menu (import-only). Legacy V-Ray BRDFs from Max/Maya exports; V-Ray core
    # renders them, so the generic import/export path handles them once a descriptor exists.
    "TexRamp",
    "BRDFBlinn",
    "BRDFPhong",
    "BRDFDiffuse",
    "BRDFWard",
    "BRDFMirror",
    "BRDFGlass",
    "BRDFGlassGlossy",
    "BRDFHair",
    "BRDFHair3",
    "BRDFCarPaint",
    "UVWGenSelect",
    "TexNoiseMaya",
    "TexColorConstant", # import-only: constant color texture (Cosmos assets, our world export)
    "TexUserInteger",   # import-only: per-object user-integer attribute (e.g. Lego "Version")
    "TexMaxGamma",      # import-only: kept as a node when it does real gamma/color-space work
    # import-only: TexFloatOp ("Float Math") is the user-facing float math node. TexFloatComposite has no
    # enum for its `operation`, so the generated UI is a bare integer field.
    "TexFloatComposite",
    # import-only: color/float "map + amount + base" wrappers (common in Max exports). Kept as a
    # node when they do real work (non-default color/multipliers/flags); seen through when a no-op.
    "TexCombineColor",
    "TexCombineFloat",
    "TexCombineColorLightMtl",
    # Base render-channel plugins: users create the named alias variants instead
    # (customRenderChannelNodes.py); the bare base node stays import-only so any .vrscene
    # alias round-trips. The empty Subtype keeps them off the Render Channels panel.
    "RenderChannelColor",
    "RenderChannelGlossiness",
    # import-only: 3ds Max color-management maps (VRayICC / VRayLut). Both wrap a basemap and an
    # external profile/LUT file, so they are only meaningful when they come in from a .vrscene.
    "TexICC",
    "TexLut",
    # import-only: procedural textures and mapping generators that arrive from Max/Maya .vrscenes.
    # A node class is generated so the importer can build and render them, but they are kept out
    # of the add menu - Blender users author these with the addon's own nodes instead.
    # TexBerconGrad additionally needs a node creator before it could ever be user-facing: its
    # gradient is 'positions' plus a 'colors' TEXTURE_LIST, and list-typed attrs generate no socket.
    "TexSurfaceLuminance",
    "TexNoise",
    "TexSnow",
    "TexWater",
    "TexBerconNoise",
    "TexBerconTile",
    "TexBerconWood",
    "TexBerconDistortion",
    "TexBerconGrad",
    "UVWGenBercon",
    "TexAColorChannel",
    # import-only: normally remapped onto TexHairSampler, but its own node is still needed as
    # the fallback for the output modes TexHairSampler does not expose (hair opacity,
    # transparency, incandescence).
    "TexMaxHairInfo",
    # import-only: Max/Maya textures and render channels that only arrive from a .vrscene.
    "TexRaySwitch",
    "TexMarbleMax",
    "TexSimplexNoise",
    "TexStencil",
    "ColorCorrect",
    "TexVectorOp",
    "RenderChannelExtraTexInt",
    "RenderChannelExtraTexFloat",
    # import-only: Maya colour-composite node (colorComposite).
    "TexComposite",
    # import-only: remapped onto TexUserColor, but kept registered as the fallback.
    "TexVertexColorDirect",
    # import-only: random value/colour generator.
    "TexRandom",
)

# Plugins for which node generation and
# visualization in the UI is not required
SKIPPED_PLUGINS = (
    # 'Virtual' plugins
    'SettingsCameraGlobal',

    # not meant to be used
    'MtlSingleBRDF',
    'BRDFSkinComplex',
    'BSDFPointParticle',
    'MtlStreakFade',


    # 3ds max specific
    'GeomHair',
    'TexMaskMax',
    'TexRGBTintMax',

    # XSI specific
    'TexBillboardParticle',
    'TexColor2Scalar',
    'TexColor8Mix',
    'TexColorAverage',
    'TexColorCurve',
    'TexColorExponential',
    'TexColorMathBasic',
    'TexColorSwitch',
    'TexDisplacacementRestrict',
    'TexFloatPerVertexHairSampler',
    'TexHairRootSampler',
    'TexInterpLinear',
    'TexParticleShape',
    'TexPerVertexHairSampler',
    'texRenderHair',
    'TexRgbaCombine',
    'TexRgbaSplit',
    'TexScalarCurve',
    'TexScalarExponential',
    'TexScalarHairRootSampler',
    'TexScalarMathBasic',
    'TexSurfIncidence',
    'TexXSIBitmap',
    'TexXSICell',
    'texXSIColor2Alpha',
    'texXSIColor2Vector',
    'TexXSIColorBalance',
    'TexXSIColorCorrection',
    'TexXSIColorMix',
    'TexXSIFabric',
    'TexXSIFalloff',
    'TexXSIFlagstone',
    'TexXSIGradient',
    'TexXSIHLSAdjust',
    'TexXSIIntensity',
    'TexXSILayered',
    'TexXSIMulti',
    'TexXSINormalMap',
    'TexXSIRGBAKeyer',
    'TexXSIRipple',
    'TexXSIRock',
    'TexXSIScalar2Color',
    'TexXSIScalarInvert',
    'TexXSISnow',
    'TexXSIVein',
    'TexXSIVertexColorLookup',
    'TexXSIWeightmapColorLookup',
    'TexXSIWeightmapLookup',
    'TexXSIWood',
    'volumeXSIMulti',
    'xsiUVWGenChannel',
    'xsiUVWGenEnvironment',

    # Handled with meta node
    'TexBitmap',
    'BitmapBuffer',

    # Manually handled
    'GeomMayaHair',
    'GeomStaticMesh',
    'VRayScene',
    'EnvFogMeshGizmo',

    # Unused
    'TexBezierCurve',
    'TexBezierCurveColor',
    'MtlBump',
    'GeomImagePlane',
    'GeomInfinitePlane',
    'TexCustomBitmap',
    'TexMultiX',
    'TexIDIntegerMap',
    'TexMeshVertexColor',
    'TexMeshVertexColorWithDefault',
    'TexMultiProjection',
    'TexParticleDiffuse',
    'TexParticleShape',
    'TexParticleId',
    'RawBitmapBuffer',
    'VolumeScatterFog',

    # Houdini specific
    'TexExtMaterialID',
    'TexExtMapChannels',

    # Not yet implemented
    'TexVolumeColorSampler',
    'TexVolumeFloatSampler',

    # Not useful
    "TexAColor",
    "TexBifrostVVMix",
    "TexGradient",
    "TexInt",
    "TexMotionOcclusion",
    "TexMultiFloat",
    "TexPtex",
    "TexThickness",
    "TexVoxelData",
    "ColorTextureToMono",
    "FloatToTex",
    "TexColorAndAlpha",
    "TexColorCondition",
    "TexColorLogic",
    "TexColorMask",
    "TexCompMax",
    "TexIntToFloat",
    "TexPlusMinusAverage",
    "TexRGBMultiplyMax",
    "TexSwitch",
    "TexSwitchFloat",
    "TexSwitchInt",
    "TexSwitchMatrix",
    "TexSwitchTransform",
    "TexTemperatureToColor",
    "TexUVWGenToTexture",
    "TransformToTex",

    # Could be used in future
    "TexCondition",
    "PhxShaderOceanTex",
    "PhxShaderTex",
    "PhxShaderTexAlpha",
    "ParticleTex",
    "TexMeshVertexColorChannel",
    "TexDistanceBetween",
    "TexOSL",

    # Unused UVWGen nodes
    "UVWGenSwitch",

    # Currently unsupported effects
    "PhxShaderSimVol",
    "PhxShaderSim",
    "PhxShaderCache",
    "SphereFade",
    "SphereFadeGizmo",
    "VolumeChannels",
    "VolumeMulti",

    # Unsupported misc plugins
    "ColorMapperTest",
    "CustomGlsl",
    "NURBSCurve",
    "PhxShaderFoam",
    "PhxShaderPGroup",
    "PhxShaderPrtLoader",
    "SceneModifierTest",
    "TexMayaFluidCombined",
    "TexMayaFluidProcedural",
    "TexModoSampler",
    "TexVRayFurSampler",
    "TrimmingRegion",
    "TrimmingRegionsComplex",

    # To be re-implemented
    "TexOpenVDB",
    "MtlGLSL",
    "MtlOSL"
)


# Third-party host-integration plugins (mirrors the list in vraypluginnode's
# pb2vray_plugin_node.h). These are meaningful only inside their originating host app and
# must NEVER be imported from a .vrscene - the generic V-Ray plugin node ("power shader")
# is the intended way to create/edit them. The importer drops any link to one (see
# nodes/importing/engine.py) and they are kept out of the add menu (see nodes/nodes.py).
# TexCyclesNoise is deliberately NOT here: Blender is our host, so it is a first-class node
# with its own plugin module and UI, and it must import from a .vrscene like any other.
THIRD_PARTY_INTEGRATION_PLUGINS = (
    "TexForestColor",   # Forest Pro (c) iToo Software
    "TexHoudini",       # Houdini (c) Side FX
    "TexC4DNoise",      # Cinema 4D (c) Maxon
    "TexLayeredNuke",   # Nuke (c) Foundry
)


MANUALLY_CREATED_PLUGINS = (
    'TexLayeredMax',
    'TexOSL',
    'MtlOSL',
    'MtlMulti',
    'RenderChannelDenoiser'
)
