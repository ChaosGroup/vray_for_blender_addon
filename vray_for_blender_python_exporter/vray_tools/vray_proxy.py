
import struct
import os
import sys
import zlib

from math import fmod


# Debug stuff
#
USE_DEBUG = False

# VRayProxy constants
#
MVF_GEOMETRY_VOXEL = 1
MVF_PREVIEW_VOXEL  = 2

VoxelFlags = {
    MVF_GEOMETRY_VOXEL : 'MVF_GEOMETRY_VOXEL',
    MVF_PREVIEW_VOXEL  : 'MVF_PREVIEW_VOXEL',
}

#
# CHANNELS
#
VERT_GEOM_CHANNEL        = 0
FACE_TOPO_CHANNEL        = 1
VOXEL_INFO_CHANNEL       = 3
VERT_NORMAL_CHANNEL      = 4
VERT_NORMAL_TOPO_CHANNEL = 5
FACE_INFO_CHANNEL        = 6
VERT_VELOCITY_CHANNEL    = 7
MAYA_INFO_CHANNEL        = 8
POINTCLOUD_INFO_CHANNEL  = 10
POINTCLOUD_GEOM_CHANNEL  = 100
VERT_TEX_CHANNEL0        = 1000
VERT_TEX_TOPO_CHANNEL0   = 2000

ChannelID = {
    VERT_GEOM_CHANNEL        : 'VERT_GEOM_CHANNEL',
    FACE_TOPO_CHANNEL        : 'FACE_TOPO_CHANNEL',
    VOXEL_INFO_CHANNEL       : 'VOXEL_INFO_CHANNEL',
    VERT_NORMAL_CHANNEL      : 'VERT_NORMAL_CHANNEL',
    VERT_NORMAL_TOPO_CHANNEL : 'VERT_NORMAL_TOPO_CHANNEL',
    FACE_INFO_CHANNEL        : 'FACE_INFO_CHANNEL',
    VERT_VELOCITY_CHANNEL    : 'VERT_VELOCITY_CHANNEL',
    MAYA_INFO_CHANNEL        : 'MAYA_INFO_CHANNEL',
    POINTCLOUD_INFO_CHANNEL  : 'POINTCLOUD_INFO_CHANNEL',
    POINTCLOUD_GEOM_CHANNEL  : 'POINTCLOUD_GEOM_CHANNEL',
    VERT_TEX_CHANNEL0        : 'VERT_TEX_CHANNEL0',
    VERT_TEX_TOPO_CHANNEL0   : 'VERT_TEX_TOPO_CHANNEL0',
}


MF_VERT_CHANNEL            =  1
MF_TOPO_CHANNEL            =  2
MF_INFO_CHANNEL            =  4
MF_FACE_CHANNEL            =  8
MF_COMPRESSED              = 16
MF_MAYA_INFO_CHANNEL       = 32
MF_POINTCLOUD_CHANNEL      = 64
MF_POINTCLOUD_INFO_CHANNEL = 28

ChannelFlags = {
    MF_VERT_CHANNEL            : 'MF_VERT_CHANNEL',
    MF_TOPO_CHANNEL            : 'MF_TOPO_CHANNEL',
    MF_INFO_CHANNEL            : 'MF_INFO_CHANNEL',
    MF_FACE_CHANNEL            : 'MF_FACE_CHANNEL',
    MF_COMPRESSED              : 'MF_COMPRESSED',
    MF_MAYA_INFO_CHANNEL       : 'MF_MAYA_INFO_CHANNEL',
    MF_POINTCLOUD_CHANNEL      : 'MF_POINTCLOUD_CHANNEL',
    MF_POINTCLOUD_INFO_CHANNEL : 'MF_POINTCLOUD_INFO_CHANNEL',
}



class MeshFileReader(object):
    meshFile = None

    def report(self, *args):
        if USE_DEBUG:
            print(*args)

    def binRead(self, format, length):
        rawData = self.meshFile.read(length)
        data    = struct.unpack(format, rawData)
        return data



class VoxelChannel(MeshFileReader):
    elementSize  = None
    numElements  = None
    channelID    = None
    depChannelID = None
    flags        = None

    data         = None

    def __init__(self, meshFile):
        self.meshFile = meshFile

    def loadInfo(self):
        self.elementSize  = self.binRead("I", 4)[0]
        self.numElements  = self.binRead("I", 4)[0]
        self.channelID    = self.binRead("H", 2)[0]
        self.depChannelID = self.binRead("H", 2)[0]
        self.flags        = self.binRead("I", 4)[0]

    def printInfo(self):
        self.report("Channel")
        self.report("  elementSize  = %i" % (self.elementSize))
        self.report("  numElements  = %i" % (self.numElements))
        self.report("  channelID    = %s" % (ChannelID[self.channelID] if self.channelID in ChannelID else str(self.channelID)))
        self.report("  depChannelID = %i" % (self.depChannelID))

        flagsList = []
        for key in sorted(ChannelFlags.keys()):
            if key & self.flags:
                flagsList.append(ChannelFlags[key])
        self.report("  flags        = %s" % (", ".join(flagsList)))

    def loadData(self):
        def _readChannelData(size):
            channelRawData = self.meshFile.read(size)
            if self.flags & MF_COMPRESSED:
                data = zlib.decompressobj().decompress(channelRawData)
                # self.report("  Compressed data:", channelRawData)
                # self.report("  Uncompressed data:", self.data)
                self.report("  Expected / uncompressed size:", elementsSize, len(data))
            else:
                data = channelRawData
            return data

        self.report("Channel Data")

        elementsSize = self.elementSize * self.numElements

        dataSize = elementsSize
        if self.flags & MF_COMPRESSED:
            self.report("  Data is compressed")
            dataSize = self.binRead("I", 4)[0]

        self.report("  Data size = %i" % (dataSize))

        if self.channelID in {VERT_GEOM_CHANNEL, FACE_TOPO_CHANNEL, MAYA_INFO_CHANNEL}:
            self.data = _readChannelData(dataSize)
        else:
            self.meshFile.seek(dataSize, os.SEEK_CUR)

    def loadChechsum(self):
        self.report("Channel Checksums")

        for i in range(self.numElements):
            channelCRC  = self.binRead("I", 4)[0]

            self.report("  %i: checksum = %i" % (i, channelCRC))



class VoxelChannels(MeshFileReader):
    channels = None

    def __init__(self, meshFile):
        self.meshFile = meshFile
        self.channels = []
    
    def loadInfo(self, voxelOffset=None):
        self.channelCount = self.binRead("I", 4)[0]

        for i in range(self.channelCount):
            voxelChannel = VoxelChannel(self.meshFile)
            voxelChannel.loadInfo()

            self.channels.append(voxelChannel)

    def printInfo(self):
        self.report("Voxel")
        self.report("  Channels count = %i" % (len(self.channels)))
        
        for channel in self.channels:
            channel.printInfo()

    def loadData(self):
        for channel in self.channels:
            channel.loadData()

    def getChannelByType(self, channelType=VERT_GEOM_CHANNEL):
        for channel in self.channels:
            if channel.channelID == channelType:
                return channel
        return None

    def getFaceTopoChannel(self):
        return self.getChannelByType(FACE_TOPO_CHANNEL)
    
    def getVertGeomChannel(self):
        return self.getChannelByType(VERT_GEOM_CHANNEL)



class MeshVoxel(MeshFileReader):
    fileOffset = None
    bbox       = None
    flags      = None

    channels = None

    def __init__(self, meshFile):
        self.meshFile = meshFile
        self.channels = VoxelChannels(self.meshFile)

    def printInfo(self):
        self.report("Voxel")
        self.report("  fileOffset = %i" % (self.fileOffset))
        self.report("  bbox       = %s" % ("%.2f,%.2f,%.2f; %.2f,%.2f,%.2f" % (self.bbox)))
        self.report("  flags      = %s" % (VoxelFlags[self.flags]))

    def loadData(self):
        self.meshFile.seek(self.fileOffset)

        self.channels.loadInfo()
        self.channels.printInfo()
        self.channels.loadData()

    def chunk(self, input, size):
        return tuple(zip(*([iter(input)]*size)))

    def getFaces(self):
        faceTopoChannel = self.channels.getFaceTopoChannel()
        
        if faceTopoChannel is None:
            return ()
        
        intArray   = struct.unpack("%ii"%(len(faceTopoChannel.data) / 4), faceTopoChannel.data)
        facesArray = self.chunk(intArray, 3)
        
        return facesArray

    def getVertices(self):
        vertexChannel = self.channels.getVertGeomChannel()
        
        if vertexChannel is None:
            return ()

        floatArray  = struct.unpack("%if"%(len(vertexChannel.data) / 4), vertexChannel.data)
        vertexArray = self.chunk(floatArray, 3)
        
        return vertexArray

    def getUvChannels(self):
        uvChannles = []
        for chan in self.channels.channels:
            if chan.channelID >= VERT_TEX_CHANNEL0:
                uvChannles.append(VERT_TEX_CHANNEL0 - chan.channelID)
        return uvChannles

    def getUvChannelNames(self):
        mayaInfoChannel = self.channels.getChannelByType(MAYA_INFO_CHANNEL)
        if mayaInfoChannel is None:
            return ()

        uvChannels = []

        def _getBytes(data, size):
            value = data[0:size]
            data = data[size:]
            return value, data

        number_of_uv_sets, mayaInfoChannel.data = _getBytes(mayaInfoChannel.data, 4)
        number_of_uv_sets = struct.unpack("I", number_of_uv_sets)[0]

        for i in range(number_of_uv_sets):
            name_len, mayaInfoChannel.data = _getBytes(mayaInfoChannel.data, 4)
            name_len = struct.unpack("I", name_len)[0]

            name, mayaInfoChannel.data = _getBytes(mayaInfoChannel.data, name_len)
            name = name.decode(encoding='ascii')

            uvChannels.append(name)

        return uvChannels


class VoxelInfo:
    fileOffset = None
    bbox       = None
    flags      = None


class FrameInfo:
    numVoxels = None
    voxels    = None

    def __init__(self):
        self.voxels = []


class MeshFile(MeshFileReader):
    vrayID       = None
    fileVersion  = None
    lookupOffset = None

    frames = None

    def __init__(self, filepath):
        self.meshFile = open(os.path.expanduser(filepath), "rb")
        self.frames = {}

    def __del__(self):
        if self.meshFile:
            self.meshFile.close()

    def readHeader(self):
        self.vrayID = self.binRead("7s", 7)[0][:-1]

        if self.vrayID == b'vrmesh':
            # New format
            self.fileVersion = self.binRead("I", 4)[0]
        else:
            # Old format
            self.meshFile.seek(0)
            self.vrayID = self.binRead("4s", 4)[0][:-1]
            self.fileVersion = 0

        self.lookupOffset = self.binRead("Q", 8)[0]

        self.report("MeshFile:", self.meshFile.name)
        self.report("  fileID       = %s" % self.vrayID)
        self.report("  fileVersion  = 0x%X" % self.fileVersion)
        self.report("  lookupOffset = %i" % self.lookupOffset)


    def readLookUpTable(self):
        def readVoxelInfo():
            vi = VoxelInfo()

            vi.fileOffset = self.binRead("Q", 8)[0]
            vi.bbox       = self.binRead("6f", 24)
            vi.flags      = self.binRead("I", 4)[0]

            return vi

        def readFrameInfo():
            numVoxels  = self.binRead("I", 4)[0]
            if numVoxels == 0:
                return None

            frameInfo = FrameInfo()
            frameInfo.numVoxels = numVoxels

            for v in range(numVoxels):
                frameInfo.voxels.append(readVoxelInfo())

            return frameInfo

        self.meshFile.seek(self.lookupOffset)

        frameCount = 0
        while True:
            fi = readFrameInfo()
            if not fi:
                break
            self.frames[frameCount] = fi
            frameCount += 1

        for frameNumber in self.frames:
            fi = self.frames[frameNumber]

            self.report("Frame %i:" % frameNumber)
            self.report("  numVoxels = %s" % fi.numVoxels)

            for v,vi in enumerate(fi.voxels):
                self.report("  Voxel %i" % v)
                self.report("    fileOffset = %i" % vi.fileOffset)
                self.report("    bbox       = %s" % ("%.2f,%.2f,%.2f; %.2f,%.2f,%.2f" % (vi.bbox)))
                self.report("    flags      = %s" % VoxelFlags[vi.flags])


    def readFile(self):
        self.readHeader()
        self.readLookUpTable()


    def getFrameByType(self, animType, animOffset, speed, frame):
        def clamp(value, value_min, value_max):
            return max(min(value, value_max), value_min)

        animLength = len(self.frames)
        animStart  = 0

        if animType in {'0', 'LOOP'}:
            frame = fmod(animOffset+(frame-animStart)*speed, animLength)
            if frame < 0:
                frame += animLength
            frame += animStart

        elif animType in {'1', 'ONCE'}:
            frame = clamp(animOffset+(frame-animStart)*speed, 0.0, animLength-1)+animStart

        elif animType in {'2', 'PINGPONG'}:
            frame = fmod(animOffset+(frame-animStart)*speed, animLength*2-2) # subtract 2 to remove the duplicate frames
            if frame < 0:
                frame += 2*animLength-2
            if frame >= animLength:
                frame = 2*animLength-2-frame
            frame += animStart*speed

        elif animType in {'3', 'STILL'}:
            frame = clamp(animOffset+animStart, 0.0, animLength-1.0)

        return int(frame)


    def getPreviewVoxel(self, frameInfo):
        for voxel in frameInfo.voxels:
            if voxel.flags == MVF_PREVIEW_VOXEL:
                return voxel
        return None


    def getGeometryVoxel(self, frameInfo):
        for voxel in frameInfo.voxels:
            if voxel.flags == MVF_GEOMETRY_VOXEL:
                return voxel
        return None


    def getPreviewMesh(self, animType=0, animOffset=0.0, speed=1.0, frame=0.0):
        frameIndex = self.getFrameByType(animType, animOffset, speed, frame)
        if frameIndex not in self.frames:
            return None

        voxelInfo = self.getPreviewVoxel(self.frames[frameIndex])
        if not voxelInfo:
            return None

        voxel = MeshVoxel(self.meshFile)
        voxel.fileOffset = voxelInfo.fileOffset
        voxel.bbox       = voxelInfo.bbox
        voxel.flags      = voxelInfo.flags

        voxel.loadData()

        faces    = voxel.getFaces()
        vertices = voxel.getVertices()
        uvChannels = voxel.getUvChannelNames()

        if not uvChannels:
            # There are no UV channels in the preview voxel, try to load them from 
            # the regular voxel
            geomVoxelInfo = self.getGeometryVoxel(self.frames[frameIndex])
            if geomVoxelInfo:
                voxel = MeshVoxel(self.meshFile)
                voxel.fileOffset = voxelInfo.fileOffset
                voxel.bbox       = voxelInfo.bbox
                voxel.flags      = voxelInfo.flags
                voxel.loadData()
                uvChannels = voxel.getUvChannelNames()
        
        if uvChannels:
            self.report("Number of UV Set: %i" % len(uvChannels))
            for i,chanName in enumerate(uvChannels):
                self.report("  UV Set %i: %s" % (i, chanName))

        return {
            'vertices' : vertices,
            'faces'    : faces,
            'uv_sets'  : uvChannels,
        }


def main():
    global USE_DEBUG

    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("file", help="*.vrmesh filepath")
    args = parser.parse_args()

    USE_DEBUG = True

    meshFile = MeshFile(args.file)
    meshFile.readFile()

    mesh = meshFile.getPreviewMesh(0)


if __name__ == '__main__':
    main()
