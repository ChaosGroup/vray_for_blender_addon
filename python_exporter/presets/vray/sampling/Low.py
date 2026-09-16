import bpy
vs = bpy.context.scene.vray

vs.SettingsImageSampler.samples_limit = 324
vs.SettingsImageSampler.dmc_minSubdivs = 1
vs.SettingsImageSampler.dmc_maxSubdivs = 8
vs.SettingsImageSampler.dmc_threshold = 0.1
vs.SettingsImageSampler.progressive_minSubdivs = 1
vs.SettingsImageSampler.progressive_maxSubdivs = 8
vs.SettingsImageSampler.progressive_threshold = 0.1
vs.SettingsRTEngine.noise_threshold = 0.1
