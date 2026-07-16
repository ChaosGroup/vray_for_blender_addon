// MurmurHash3 was written by Austin Appleby, and is placed in the
// public domain. The author hereby disclaims copyright to this source
// code.
//
// Taken from https://github.com/PeterScott/murmur3
//

#pragma once

#include <cstddef>
#include <cstdint>

using u_int8_t  = uint8_t;
using u_int16_t = uint16_t;
using u_int32_t = uint32_t;
using u_int64_t = uint64_t;

using MHash = u_int32_t;

MHash HashCode(const char* s);

void MurmurHash3_x86_32 (const void *key, int len, u_int32_t seed, void *out);
void MurmurHash3_x86_128(const void *key, int len, u_int32_t seed, void *out);
void MurmurHash3_x64_128(const void *key, const int len, const u_int32_t seed, void *out);

