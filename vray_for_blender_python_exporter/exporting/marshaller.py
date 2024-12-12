import struct
from mathutils import Matrix, Vector
from vray_blender.exporting import tools

# Blob packer for Blender types
class Marshaller:
    def __init__(self):
        self.l = bytearray()

    def pack(self):
        return self.l

    def dumpString(self, v: str):
        self.l.extend(struct.pack(f"i{len(v)}s", len(v), v.encode()))

    def dumpByte(self, v):
        self.l.extend(struct.pack('b', v))

    def dumpInt16(self, v):
        self.l.extend(struct.pack('h', v))

    def dumpInt32(self, v):
        self.l.extend(struct.pack('i', v))

    def dumpInt64(self, v):
        self.l.extend(struct.pack('q', v))

    def dumpMatrix4(self, m: Matrix):
        self.l.extend(struct.pack('16f', *tools.mat4x4ToTuple(m)))

    def dumpVector3(self, v: Vector):
        self.l.extend(struct.pack('3f', *v.to_tuple()))
        
