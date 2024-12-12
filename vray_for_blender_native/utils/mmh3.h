// MurmurHash3 was written by Austin Appleby, and is placed in the
// public domain. The author hereby disclaims copyright to this source
// code.
//
// Taken from https://github.com/PeterScott/murmur3
//

#pragma once


#ifdef _WIN32
	using u_int8_t  = unsigned __int8;
	using u_int16_t = unsigned __int16;
	using u_int32_t = unsigned __int32;
	using u_int64_t = unsigned __int64;
#endif

using MHash = u_int32_t;

MHash HashCode(const char* s);

void MurmurHash3_x86_32 (const void *key, int len, u_int32_t seed, void *out);
void MurmurHash3_x86_128(const void *key, int len, u_int32_t seed, void *out);
void MurmurHash3_x64_128(const void *key, const int len, const u_int32_t seed, void *out);

