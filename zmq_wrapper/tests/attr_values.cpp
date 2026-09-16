// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <catch_amalgamated.hpp>

#include "base_types.h"
#include "zmq_serializer.hpp"
#include "zmq_deserializer.hpp"

using namespace VrayZmqWrapper;
namespace vray = VRayBaseTypes;

namespace {

/// Serialize a single AttrValue and read it back, checking the stream is fully consumed.
vray::AttrValue roundTrip(const vray::AttrValue& value) {
	SerializerStream out;
	out << value;

	DeserializerStream in(out.getData(), out.getSize());
	vray::AttrValue result;
	in >> result;
	REQUIRE_FALSE(in.hasMore());
	REQUIRE(result.getType() == value.getType());
	return result;
}

} // namespace


TEST_CASE("AttrValue scalar types round-trip") {

	SECTION("int") {
		REQUIRE(roundTrip(vray::AttrValue(-42)).as<vray::AttrSimpleType<int>>().value == -42);
	}

	SECTION("bool becomes int") {
		const auto out = roundTrip(vray::AttrValue(true));
		REQUIRE(out.getType() == vray::ValueTypeInt);
		REQUIRE(out.as<vray::AttrSimpleType<int>>().value == 1);
	}

	SECTION("float") {
		REQUIRE(roundTrip(vray::AttrValue(2.5f)).as<vray::AttrSimpleType<float>>().value == 2.5f);
	}

	SECTION("string") {
		const std::string text = "Mtl name with spaces @and:symbols|";
		REQUIRE(roundTrip(vray::AttrValue(text)).as<vray::AttrSimpleType<std::string>>().value == text);
	}

	SECTION("empty string") {
		REQUIRE(roundTrip(vray::AttrValue(std::string())).as<vray::AttrSimpleType<std::string>>().value.empty());
	}
}


TEST_CASE("AttrValue struct types round-trip") {

	SECTION("color / acolor") {
		const auto color = roundTrip(vray::AttrValue(vray::AttrColor(0.25f, 0.5f, 0.75f))).as<vray::AttrColor>();
		REQUIRE(color.r == 0.25f);
		REQUIRE(color.g == 0.5f);
		REQUIRE(color.b == 0.75f);

		const auto acolor = roundTrip(vray::AttrValue(vray::AttrAColor(vray::AttrColor(1.0f), 0.5f))).as<vray::AttrAColor>();
		REQUIRE(acolor.color.r == 1.0f);
		REQUIRE(acolor.alpha == 0.5f);
	}

	SECTION("vector") {
		const auto v = roundTrip(vray::AttrValue(vray::AttrVector(1.0f, -2.0f, 3.0f))).as<vray::AttrVector>();
		REQUIRE(v.x == 1.0f);
		REQUIRE(v.y == -2.0f);
		REQUIRE(v.z == 3.0f);
	}

	SECTION("transform") {
		float tm[4][4] = {
			{0, 1, 0, 0},
			{-1, 0, 0, 0},
			{0, 0, 1, 0},
			{10, 20, 30, 1},
		};
		const auto out = roundTrip(vray::AttrValue(vray::AttrTransform(tm))).as<vray::AttrTransform>();
		REQUIRE(out.m.v0.y == 1.0f);
		REQUIRE(out.m.v1.x == -1.0f);
		REQUIRE(out.offs.x == 10.0f);
		REQUIRE(out.offs.z == 30.0f);
	}

	SECTION("plugin reference keeps name and output") {
		const auto out = roundTrip(vray::AttrValue(vray::AttrPlugin("bitmap@tex_0", "out_intensity"))).as<vray::AttrPlugin>();
		REQUIRE(out.plugin == "bitmap@tex_0");
		REQUIRE(out.output == "out_intensity");
	}
}


TEST_CASE("AttrValue list types round-trip") {

	SECTION("int / float lists") {
		const auto ints = roundTrip(vray::AttrValue(vray::AttrListInt({0, 1, 2, 2, 1, 3}))).as<vray::AttrListInt>();
		REQUIRE(ints.getCount() == 6);
		REQUIRE((*ints)[3] == 2);

		const auto floats = roundTrip(vray::AttrValue(vray::AttrListFloat({0.5f, 1.5f}))).as<vray::AttrListFloat>();
		REQUIRE(floats.getCount() == 2);
		REQUIRE((*floats)[1] == 1.5f);
	}

	SECTION("empty list") {
		REQUIRE(roundTrip(vray::AttrValue(vray::AttrListInt())).as<vray::AttrListInt>().getCount() == 0);
	}

	SECTION("string list") {
		const auto out = roundTrip(vray::AttrValue(vray::AttrListString({"a", "", "channel name"}))).as<vray::AttrListString>();
		REQUIRE(out.getCount() == 3);
		REQUIRE((*out)[1].empty());
		REQUIRE((*out)[2] == "channel name");
	}

	SECTION("nested heterogeneous list value") {
		// The [name, valueType, value] shape the import user-attribute decoding produces.
		vray::AttrListValue entry;
		entry.append(vray::AttrValue(std::string("mtl_id")));
		entry.append(vray::AttrValue(1));
		entry.append(vray::AttrValue(vray::AttrListFloat({0.0f, 0.5f, 1.0f})));

		vray::AttrListValue attrs;
		attrs.append(vray::AttrValue(entry));

		const auto out = roundTrip(vray::AttrValue(attrs)).as<vray::AttrListValue>();
		REQUIRE(out.getCount() == 1);
		const auto& outEntry = (*out)[0].as<vray::AttrListValue>();
		REQUIRE(outEntry.getCount() == 3);
		REQUIRE((*outEntry)[0].as<vray::AttrSimpleType<std::string>>().value == "mtl_id");
		REQUIRE((*outEntry)[1].as<vray::AttrSimpleType<int>>().value == 1);
		REQUIRE((*outEntry)[2].as<vray::AttrListFloat>().getCount() == 3);
	}
}


TEST_CASE("AttrValue tagged-union lifecycle") {

	SECTION("copy deep-copies string payload") {
		vray::AttrValue original(std::string("first"));
		vray::AttrValue copy(original);
		copy.as<vray::AttrSimpleType<std::string>>().value = "second";
		REQUIRE(original.as<vray::AttrSimpleType<std::string>>().value == "first");
	}

	SECTION("assignment over a different payload type") {
		vray::AttrValue value(std::string("about to be replaced"));
		value = vray::AttrValue(vray::AttrListInt({7, 8}));
		REQUIRE(value.getType() == vray::ValueTypeListInt);
		REQUIRE(value.as<vray::AttrListInt>().getCount() == 2);
	}

	SECTION("list copies share the buffer") {
		// AttrList holds a shared_ptr: copying an AttrValue must NOT detach the data.
		// Consumers relying on zero-copy (import ndarray capsules) depend on this.
		vray::AttrValue original(vray::AttrListInt({1, 2, 3}));
		vray::AttrValue copy(original);
		(*copy.as<vray::AttrListInt>())[0] = 99;
		REQUIRE((*original.as<vray::AttrListInt>())[0] == 99);
		REQUIRE(original.as<vray::AttrListInt>().getData() == copy.as<vray::AttrListInt>().getData());
	}

	SECTION("default value is unknown type") {
		REQUIRE(vray::AttrValue().getType() == vray::ValueTypeUnknown);
	}
}
