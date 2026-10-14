# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

def _getDocsMapping():
    chaosDocsUrl = 'https://documentation.chaos.com/space/VBLD/'

    chaosDocsMapping = []
    # Note that blender lower cases only the rna name of our node, so we must
    # use lower case letter when registering the mappings here as well.
    def _registerNode(nodeName: str, id: str):
        chaosDocsMapping.append((f'bpy.types.vraynode{nodeName.lower()}', id),)

    def _registerSettings(pluginName: str, id: str):
        chaosDocsMapping.append((f'bpy.types.vraysettings{pluginName.lower()}', id),)

    def _registerExporter(paramName: str, id: str):
        chaosDocsMapping.append((f'bpy.types.vrayexporter.{paramName.lower()}', id),)

    def _registerType(paramName: str, id: str):
        chaosDocsMapping.append((f'bpy.types.{paramName.lower()}', id),)

    def _registerOperator(paramName: str, id: str):
        chaosDocsMapping.append((f'bpy.ops.vray.{paramName.lower()}', id),)

    # Materials
    _registerNode('BRDFVRayMtl', '117638527')
    _registerNode('BRDFAlSurface', '117637493')
    _registerNode('BRDFSSS2Complex', '117638568')
    _registerNode('BRDFLayered', '117638563')
    _registerNode('BRDFBump', '117638580')
    _registerNode('BRDFLight', '117638097')
    _registerNode('BRDFHair4', '117638589')
    _registerNode('BRDFCarPaint2', '117638516')
    _registerNode('BRDFFlakes2', '117638599')
    _registerNode('BRDFScanned', '117640066')
    _registerNode('BRDFStochasticFlakes', '117638607')
    _registerNode('BRDFToonMtl', '540672159')
    _registerNode('MtlMulti', '117638595')
    _registerNode('Mtl2Sided', '117638620')
    _registerNode('MtlOverride', '117638093')
    _registerNode('MtlDisplacement', '540246033')
    _registerNode('MtlVRmat', '117638652')

    # Math
    _registerNode('Transform', '117638209')
    _registerNode('Matrix', '117638212')
    _registerNode('Vector', '117638215')

    # Object
    _registerNode('ObjectMatteProps', '117638241')
    _registerNode('ObjectSurfaceProps', '117638244')
    _registerNode('ObjectVisibilityProps', '117638247')

    # Mapping
    _registerNode('UVWMapping', '117638859')
    _registerNode('UVWGenRandomizer', '117638851')

    _registerNode('ListHolder', '117638258')

    _registerNode('Displacement', '117638408')
    _registerNode('GeomStaticSmoothedMesh', '117638239')

    # Environment
    _registerNode('Environment', '117638235')

    # Effects
    _registerNode('VolumeVRayToon', '117637912')
    _registerNode('VolumeFog', '117638086')
    _registerNode('EnvironmentFog', '117639260')
    _registerNode('VolumeAerialPerspective', '117638865')
    _registerNode('EffectsHolder', '117638276')

    # Selectors
    _registerNode('SelectObject', '117638253')
    _registerNode('MultiSelect', '117638256')

    # Outputs
    _registerNode('OutputMaterial', '117638221')
    _registerNode('ObjectOutput', '117638228')
    _registerNode('WorldOutput', '117638231')
    _registerNode('DecalOutput', '540672075')

    # Textures
    _registerNode('MetaImageTexture', '117638712')
    _registerNode('TexBulge', '117638825')
    _registerNode('TexCellular', '117638741')
    _registerNode('TexChecker', '117638767')
    _registerNode('TexCloth', '117638773')
    _registerNode('TexCurvature', '117638754')
    _registerNode('TexDirt', '117638722')
    _registerNode('TexEdges', '117638776')
    _registerNode('TexGradRamp', '117638758')
    _registerNode('TexGranite', '117638744')
    _registerNode('TexGrid', '117638748')
    _registerNode('TexLayeredMax', '117640076')
    _registerNode('TexLeather', '117638751')
    _registerNode('TexMarble', '117638730')
    _registerNode('TexNoiseMax', '117638736')
    _registerNode('TexNormalBump', '117638779')
    _registerNode('TexOCIO', '117638784')
    _registerNode('TexRock', '117638820')
    _registerNode('TexSky', '117638794')
    _registerNode('TexSmoke', '117638692')
    _registerNode('TexSoftbox', '117638805')
    _registerNode('TexSpeckle', '117638700')
    _registerNode('TexSplat', '117638696')
    _registerNode('TexStucco', '117638828')
    _registerNode('TexSwirl', '117638832')
    _registerNode('TexTemperature', '117638838')
    _registerNode('TexTiles', '117638706')
    _registerNode('TexWood', '117638842')

     # Texture Utilities
    _registerNode('TexClamp', '117638449')
    _registerNode('TexAColorOp', '117638270')
    _registerNode('ColorCorrection', '117638477')
    _registerNode('TexColorToFloat', '117638265')
    _registerNode('TexDistance', '117638471')
    _registerNode('TexFalloff', '117638480')
    _registerNode('TexFloat', '117638482')
    _registerNode('TexFloatOp', '117638460')
    _registerNode('TexFloatToColor', '117638263')
    _registerNode('TexHairSampler', '117638485')
    _registerNode('TexHSVToRGB', '117638457')
    _registerNode('TexInvert', '117638473')
    _registerNode('TexInvertFloat', '117638488')
    _registerNode('TexLuminance', '117637479')
    _registerNode('TexMix', '117638451')
    _registerNode('TexMulti', '117638268')
    _registerNode('TexOutput', '117638467')
    _registerNode('TexRemap', '117638490')
    _registerNode('TexRemapFloat', '117638492')
    _registerNode('TexRGBToHSV', '117638453')
    _registerNode('TexSampler', '117638455')
    _registerNode('TexSetRange', '117638494')
    _registerNode('TexTriplanar', '117638498')
    _registerNode('TexUserColor', '117638465')
    _registerNode('TexUserScalar', '117638475')

     # Render Channels
    _registerNode('RenderChannels', '117637617')
    _registerNode('RenderChannelAtmosphericEffects', '117637560')
    _registerNode('RenderChannelBackground', '117637524')
    _registerNode('RenderChannelBumpNormals', '117637596')
    _registerNode('RenderChannelCaustics', '117637537')
    _registerNode('RenderChannelCoat', '117637602')
    _registerNode('RenderChannelCoatFilter', '117637628')
    _registerNode('RenderChannelCoatGlossiness', '117637608')
    _registerNode('RenderChannelCoatReflection', '117637566')
    _registerNode('RenderChannelCoverage', '117637660')
    _registerNode('RenderChannelCryptomatte', '117637514')
    _registerNode('RenderChannelDenoiser', '117637666')
    _registerNode('RenderChannelDiffuse', '117637572')
    _registerNode('RenderChannelDRBucket', '117637501')
    _registerNode('RenderChannelExtraTex', '117637634')
    _registerNode('RenderChannelGI', '117637520')
    _registerNode('RenderChannelLighting', '117637700')
    _registerNode('RenderChannelLightMix', '117637742')
    _registerNode('RenderChannelLightSelect', '117637674')
    _registerNode('RenderChannelMaterialID', '117637736')
    _registerNode('RenderChannelMaterialSelect', '117637682')
    _registerNode('RenderChannelMatteShadow', '117637642')
    _registerNode('RenderChannelMetalness', '117637752')
    _registerNode('RenderChannelMultiMatte', '117637578')
    _registerNode('RenderChannelMtlID', '117637689')
    _registerNode('RenderChannelNormals', '117637768')
    _registerNode('RenderChannelObjectID', '117637781')
    _registerNode('RenderChannelObjectSelect', '117637586')
    _registerNode('RenderChannelRawCoatFilter', '117637717')
    _registerNode('RenderChannelRawCoatReflection', '117637807')
    _registerNode('RenderChannelRawGI', '117637813')
    _registerNode('RenderChannelRawLighting', '117637695')
    _registerNode('RenderChannelRawReflection', '117637848')
    _registerNode('RenderChannelRawRefraction', '117637854')
    _registerNode('RenderChannelRawShadow', '117637648')
    _registerNode('RenderChannelSheenFilter', '117637702')
    _registerNode('RenderChannelRawSheenReflection', '117637787')
    _registerNode('RenderChannelRawTotalLight', '117637542')
    _registerNode('RenderChannelReflection', '117637860')
    _registerNode('RenderChannelReflectionFilter', '117637868')
    _registerNode('RenderChannelReflectionGlossiness', '117637793')
    _registerNode('RenderChannelReflectionHighlightGlossiness', '117637875')
    _registerNode('RenderChannelReflectionIOR', '117637548')
    _registerNode('RenderChannelRefraction', '117637887')
    _registerNode('RenderChannelRefractionFilter', '117637654')
    _registerNode('RenderChannelRefractionGlossiness', '117637842')
    _registerNode('RenderChannelRenderID', '117637893')
    _registerNode('RenderChannelSampleRate', '117637906')
    _registerNode('RenderChannelSelfIllumination', '117637774')
    _registerNode('RenderChannelShadow', '117637723')
    _registerNode('RenderChannelSheen', '117637817')
    _registerNode('RenderChannelSheenFilter', '117637823')
    _registerNode('RenderChannelGlossiness', '117637881')
    _registerNode('RenderChannelSheenReflection', '117637829')
    _registerNode('RenderChannelSpecular', '117637705')
    _registerNode('RenderChannelSSS', '117637621')
    _registerNode('RenderChannelTotalLight', '117637779')
    _registerNode('RenderChannelVelocity', '117637899')
    _registerNode('RenderChannelZDepth', '117637760')
    _registerNode('RenderChannelLightingAnalysis', '1109852182')

    _registerSettings('ImageSampler*', '117637484')

    _registerSettings('GI*', '117638973')
    _registerSettings('LightCache*', '117638981')
    _registerSettings('DMCGI*', '117638978')
    _registerSettings('Caustics*', '117638300')
    _registerSettings('DMCSampler*', '117637484#Progressive-Sampler')

    _registerSettings('CameraGlobal*', '117639033')
    _registerSettings('MotionBlur*', '117639033')
    _registerSettings('CameraDof*', '117639033')
    _registerSettings('DefaultDisplacement*', '117639033')
    _registerSettings('Hair*', '117639033')
    _registerSettings('Options*', '117639033')
    _registerSettings('TextureCache*', '117639033')
    _registerSettings('UnitsInfo*', '117638326')
    _registerSettings('RTEngine*', '117638327#GPU-Texture-Options')
    _registerSettings('Output.resumable*', '910819616')
    _registerSettings('Output.*', '733249591')
    _registerSettings('JPEG*', '733249591')
    _registerSettings('PNG*', '733249591')
    _registerSettings('TIFF*', '733249591')
    _registerSettings('TGA*', '733249591')
    _registerSettings('SGI*', '733249591')
    _registerSettings('EXR*', '733249591')
    _registerSettings('VRST*', '733249591')
    _registerSettings('RegionsGenerator.*', '117637484/Sampler+tab#Bucket-Optionss')

    _registerExporter('auto_save_render', '733249591')
    _registerExporter('use_stereo', '734626580')
    _registerExporter('device_type', '117638321')
    _registerExporter('gpu_device_type', '117638321')

    _registerExporter('use_custom_thread_count', '117639386')
    _registerExporter('custom_thread_count', '117639386')
    _registerExporter('lower_thread_priority', '117639386')

    _registerExporter('display_vfb_on_top', '117639384')
    _registerExporter('animation_mode', '908658082')
    _registerExporter('use_frame_range', '908658082')

    _registerType('VRayFilter*', '117637484')
    _registerType('VRayWindowManager*', '117638327')
    _registerType('VRayPluginListHolder', '117638258')
    _registerType('VRayScene.quick*', '909148886')
    _registerType('VRayWorld.quick*', '909148886')
    _registerType('VRayRenderchanneldenoiser.*', '909148886')
    _registerType('VRayVRayDecal.*', '540672075/V-Ray+Decal#Properties')
    _registerType('VRayVRayStereoscopicSettings.*', '734626580')
    _registerType('VRayGeomGaussians.*', '1233223793/V-Ray+Gaussians#Properties')
    _registerType('VRayGeomPlane.*', '1231880205/V-Ray+Infinite+Plane#Properties-Locations')
    _registerType('VRayDR.*', '381288513')
    _registerType('VRayPropGroupMultiBake.*', '908755503')
    _registerType('VRayBatchBakeItem.*', '908755503')
    _registerType('VRayVRayObjectProperties.*', '908722331')
    _registerType('VRayVRayClipper.*', '909050923')
    _registerType('VRayUserAttributeItem.*', '117639357')
    _registerType('VRayCamera.use_dome', '117638123')
    _registerType('VRayCameraDome.*', '117638123')
    _registerType('VRayCamera.use_physical', '117638119')
    _registerType('VRayCameraPhysical.*', '117638119')
    _registerType('VRaySettingsCamera.*', '117639394')
    _registerOperator('open_vfb', '117637715')
    _registerOperator('render_interactive', '376307957')
    _registerOperator('veras*', '1017675788')
    _registerOperator('add_object_decal*', '540672075')
    _registerOperator('add_object_splat*', '1233223793')
    _registerOperator('add_object_infinite_plane*', '1231880205')
    _registerOperator('cloud_submit*', '117639229')
    _registerOperator('open_collaboration*', '117639367')
    _registerOperator('open_cosmos_browser', '117637925')
    _registerOperator('relink_cosmos_assets', '117639361')
    _registerOperator('open_cosmos_ai_generator', '380698787')
    _registerOperator('vantage_live_link', '1129087008')
    _registerOperator('jump_to_setting', '909148886')
    _registerOperator('render', '117638327#Render-Engine')
    _registerOperator('batch_bake', '908755503')
    _registerOperator('open_preferences', '117638197/Preferences#V-Ray-for-Blender')
    _registerOperator('user_attribute_add', '117639357')
    _registerOperator('show_account_status', '117638207')
    _registerOperator('add_physical_camera', '117638119')
    _registerOperator('lister_open', '1232896285')
    _registerOperator('material_lister_open', '1232830471')
    _registerOperator('relink_assets', '1233190941')
    _registerOperator('convert_materials', '117640052')
    _registerOperator('convert_lights', '1017315330')
    _registerOperator('convert_worlds', '1233813543')
    _registerOperator('make_shadow_catcher', '1017380870')

    _registerOperator('*', '')

    return chaosDocsUrl, chaosDocsMapping


def register():
    bpy.utils.register_manual_map(_getDocsMapping)

def unregister():
    bpy.utils.unregister_manual_map(_getDocsMapping)
