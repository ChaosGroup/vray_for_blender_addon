// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

// This repo vendors Catch2 v2.13.4 (xsdk/catch), not the v3 "amalgamated" distribution
// the tests were written against. Shim the v3 header name onto the vendored v2 one so
// the test sources don't need to change.
#include <catch.hpp>
