
# Miscellaneous enumeration definitions
 
class AnimationMode:
    SingleFrame     = 'FRAME'
    Animation       = 'ANIMATION'

class RenderMode:
    Production          = -1
    RtCpu               = 0
    RtGpuCUDA           = 4
    RtGpuOptiX          = 7
    ProductionGpuCUDA   = 104
    ProductionGpuOptiX  = 107

class ImageType:
    NoImage   = 0
    RgbaReal  = 1
    RgbReal   = 2
    BwReal    = 3
    Jpg       = 4

class DefaultMapping: 
    Object   = 0
    Cube     = 1
    Channel  = 2

class ExportFormat:
    ZIP     = 0
    HEX     = 1
    ASCII   = 2

class ActiveLayers:
    Scene   = 0
    All     = 1
    Custom  = 2

class VRayVerboseLevel:
    LevelNoInfo   = 0
    LevelErrors   = 1
    LevelWarnings = 2
    LevelProgress = 3
    LevelAll      = 4

class DeviceType:
    CPU = 0
    GPU = 1

     
class GIEngine:
    IrradianceMap      = 0
    BruteForce         = 2
    LightCache         = 3
    Sphericalharmonics = 4

class PhysicalCameraType:
    Still       = 0
    Cinematic   = 1
    Video       = 2

class VfbFlags:
    # Bitmask
    NoFlags     = 0x0
    Show        = 0x1
    AlwaysOnTop = 0x2
        
class ProjectionMapping:
    Cubic = 5

class LightSelectMode:
    Full = 4
    Environment = 10
    SelfIllumination =11


class StereoOutputLayout:
    Horizontal  = 0
    Vertical    = 1

class StereoViewMode:
    Both = 0
    Left = 1
    Right = 2

class GIEngine:
    NoEngine   = 0
    BruteForce = 2
    LightCache = 3
