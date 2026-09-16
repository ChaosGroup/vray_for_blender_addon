import bpy
vs = bpy.context.scene.vray

vs.SettingsImageSampler.samples_limit = 676
vs.SettingsImageSampler.dmc_minSubdivs = 1
vs.SettingsImageSampler.dmc_maxSubdivs = 12
vs.SettingsImageSampler.dmc_threshold = 0.08
vs.SettingsImageSampler.progressive_minSubdivs = 1
vs.SettingsImageSampler.progressive_maxSubdivs = 12
vs.SettingsImageSampler.progressive_threshold = 0.08
vs.SettingsRTEngine.noise_threshold = 0.08
