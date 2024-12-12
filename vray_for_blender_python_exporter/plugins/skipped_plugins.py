from vray_blender.plugins import PLUGINS
from vray_blender.lib     import class_utils
import bpy

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
    'TexMaxHairInfo',
    'GeomHair',
    'TexMaskMax',
    'TexMarbleMax',
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
    'TexBezierCurve',
    'GeomMayaHair',
    'GeomStaticMesh',
    'VRayScene',
    'EnvFogMeshGizmo',

    # Unused
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
    'BRDFScanned',
    'TexRamp',

    # Not useful
    "TexAColor",
    "TexBerconDistortion",
    "TexBerconGrad",
    "TexBerconNoise",
    "TexBerconTile",
    "TexBerconWood",
    "TexBifrostVVMix",
    "TexFresnel",
    "TexGradient",
    "TexICC",
    "TexInt",
    "TexLut",
    "TexMotionOcclusion",
    "TexMultiFloat",
    "TexNoise",
    "TexNoiseMaya",
    "TexC4DNoise",
    "TexPtex",
    "TexRaySwitch",
    "TexSimplexNoise",
    "TexStencil",
    "TexThickness",
    "Outputs the UVW from a UVWGen plugin as RGB color",
    "TexVoxelData",
    "TexWater",
    "ColorCorrect",
    "ColorTextureToMono",
    "Float3ToAColor",
    "FloatToTex",
    "TexAColorChannel",
    "TexBlend",
    "TexColorAndAlpha",
    "TexColorCondition",
    "TexColorConstant",
    "TexColorCorrect",
    "TexColorLogic",
    "TexColorMask",
    "TexCombineColor",
    "TexCombineColorLightMtl",
    "TexCombineFloat",
    "TexCompMax",
    "TexComposite",
    "TexIntToFloat",
    "TexMaxGamma",
    "TexPlusMinusAverage",
    "TexRGBMultiplyMax",
    "TexSwitch",
    "TexSwitchFloat",
    "TexSwitchInt",
    "TexSwitchMatrix",
    "TexSwitchTransform",
    "TexTemperatureToColor",
    "TexUVWGenToTexture",
    "TexVectorOp",
    "TexVectorProduct",
    "TexVectorToColor",
    "TexVertexColorDirect",
    "TransformToTex",
    
    # Could be used in future
    "TexCondition",
    "TexCondition2",
    "TexUVW",
    "TexSnow",
    "PhxShaderOceanTex",
    "PhxShaderTex",
    "PhxShaderTexAlpha",
    "ParticleTex",
    "TexMeshVertexColorChannel",
    "TexDistanceBetween",
    "TexOSL",

    # Unused UVWGen nodes
    "UVWGenObjectBBox",
    "UVWGenBercon",
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
    "TexParticleSampler",
    "TexVRayFurSampler",
    "TrimmingRegion",
    "TrimmingRegionsComplex"


)


MANUALLY_CREATED_PLUGINS = (
    'BRDFLayered', 
    'TexLayeredMax', 
    'TexOSL', 
    'MtlOSL', 
    'MtlMulti',
    'RenderChannelColor'
)