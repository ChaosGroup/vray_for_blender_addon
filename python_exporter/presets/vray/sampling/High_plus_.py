import bpy
vs = bpy.context.scene.vray

vs.SettingsImageSampler.samples_limit = 40804
vs.SettingsImageSampler.dmc_minSubdivs = 2
vs.SettingsImageSampler.dmc_maxSubdivs = 100
vs.SettingsImageSampler.dmc_threshold = 0.005
vs.SettingsImageSampler.progressive_minSubdivs = 2
vs.SettingsImageSampler.progressive_maxSubdivs = 100
vs.SettingsImageSampler.progressive_threshold = 0.005
vs.SettingsRTEngine.noise_threshold = 0.005
