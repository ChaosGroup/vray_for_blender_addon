// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <catch_amalgamated.hpp>

#include "zmq_import_message.hpp"

using namespace VrayZmqWrapper;
namespace vray = VRayBaseTypes;

namespace {

/// Serialize a message the same way the wire does, then read it back.
template <typename TMsg>
TMsg roundTrip(const TMsg& msg) {
	// What serializeMessage() reserves has to match what it writes, or the payload is
	// reallocated and copied - see message_sizing.cpp.
	SerializerStream sizer(SerializerStream::Mode::Count);
	sizer && msg.getType() && msg;

	zmq::message_t wire = serializeMessage(msg);
	REQUIRE(sizer.getSize() == wire.size());

	DeserializerStream stream(reinterpret_cast<const char*>(wire.data()), wire.size());

	MsgType type = MsgType::None;
	stream && type;
	REQUIRE(type == msg.getType());

	TMsg result = deserializeMessage<TMsg>(stream);
	REQUIRE_FALSE(stream.hasMore());
	return result;
}

} // namespace


TEST_CASE("Import request/control messages round-trip") {

	SECTION("ImportVrsceneRequest") {
		MsgImportVrsceneRequest msg;
		msg.filePath = "C:/scenes/test.vrscene";
		msg.flags = ImportRequestSkipDefaults;
		msg.frameStart = 1.0;
		msg.frameEnd = 25.0;
		msg.frameStep = 0.5;
		msg.batchSizeHint = 128;
		msg.typeFilter = "Mtl,BRDF";

		const auto out = roundTrip(msg);
		REQUIRE(out.filePath == msg.filePath);
		REQUIRE(out.flags == msg.flags);
		REQUIRE(out.frameStart == msg.frameStart);
		REQUIRE(out.frameEnd == msg.frameEnd);
		REQUIRE(out.frameStep == msg.frameStep);
		REQUIRE(out.batchSizeHint == msg.batchSizeHint);
		REQUIRE(out.typeFilter == msg.typeFilter);
	}

	SECTION("ImportVrsceneCancel") {
		roundTrip(MsgImportVrsceneCancel{});
	}

	SECTION("ImportVrsceneAck") {
		MsgImportVrsceneAck msg;
		msg.batchIndex = 42;
		REQUIRE(roundTrip(msg).batchIndex == 42);
	}

	SECTION("ImportOnProgress") {
		MsgImportOnProgress msg;
		msg.pluginsDone = 10;
		msg.pluginsTotal = 400;
		msg.stage = "enumerating";

		const auto out = roundTrip(msg);
		REQUIRE(out.pluginsDone == msg.pluginsDone);
		REQUIRE(out.pluginsTotal == msg.pluginsTotal);
		REQUIRE(out.stage == msg.stage);
	}

	SECTION("ImportOnResult") {
		MsgImportOnResult msg;
		msg.status = static_cast<int>(ImportStatus::ParseError);
		msg.errorText = "unexpected token";
		msg.errorFile = "included.vrscene";
		msg.errorLine = 1234;
		msg.pluginCount = 17;
		msg.paramCount = 250;
		msg.sceneBaseDir = "C:/scenes";

		const auto out = roundTrip(msg);
		REQUIRE(out.status == msg.status);
		REQUIRE(out.errorText == msg.errorText);
		REQUIRE(out.errorFile == msg.errorFile);
		REQUIRE(out.errorLine == msg.errorLine);
		REQUIRE(out.pluginCount == msg.pluginCount);
		REQUIRE(out.paramCount == msg.paramCount);
		REQUIRE(out.sceneBaseDir == msg.sceneBaseDir);
	}
}


TEST_CASE("ImportOnPluginData round-trips every carried value type") {

	MsgImportOnPluginData msg;
	msg.batchIndex = 3;

	ImportedPluginData mtl;
	mtl.pluginName = "mtlDiffuse@material";
	mtl.pluginType = "BRDFVRayMtl";
	mtl.time = 0.0;
	mtl.params.push_back({"diffuse", 0, vray::AttrValue(vray::AttrAColor(vray::AttrColor(0.5f, 0.25f, 0.125f), 0.75f))});
	mtl.params.push_back({"opacity", ImportParamAnimated, vray::AttrValue(0.5f)});
	mtl.params.push_back({"brdf_type", 0, vray::AttrValue(4)});
	mtl.params.push_back({"channels_name", 0, vray::AttrValue(std::string("diffuse_channel"))});
	mtl.params.push_back({"brdf", 0, vray::AttrValue(vray::AttrPlugin("someBrdf", "brdf_out"))});

	ImportedPluginData mesh;
	mesh.pluginName = "geom@node";
	mesh.pluginType = "GeomStaticMesh";
	mesh.params.push_back({"faces", 0, vray::AttrValue(vray::AttrListInt({0, 1, 2, 2, 1, 3}))});
	mesh.params.push_back({"vertices", 0, vray::AttrValue(vray::AttrListVector({
		vray::AttrVector(0.f, 0.f, 0.f),
		vray::AttrVector(1.f, 0.f, 0.f),
		vray::AttrVector(0.f, 1.f, 0.f),
		vray::AttrVector(1.f, 1.f, 0.f),
	}))});
	mesh.params.push_back({"weights", 0, vray::AttrValue(vray::AttrListFloat({0.1f, 0.2f, 0.3f}))});
	mesh.params.push_back({"names", 0, vray::AttrValue(vray::AttrListString({"a", "b"}))});
	mesh.params.push_back({"refs", 0, vray::AttrValue(vray::AttrListPlugin({vray::AttrPlugin("nodeA"), vray::AttrPlugin("nodeB", "out")}))});

	// Nested heterogeneous list: the layout Max uses for map_channels.
	vray::AttrListValue channel;
	channel.append(vray::AttrValue(1));
	channel.append(vray::AttrValue(vray::AttrListVector({vray::AttrVector(0.f, 0.f, 0.f), vray::AttrVector(1.f, 1.f, 0.f)})));
	channel.append(vray::AttrValue(vray::AttrListInt({0, 1, 1})));
	vray::AttrListValue mapChannels;
	mapChannels.append(vray::AttrValue(channel));
	mesh.params.push_back({"map_channels", 0, vray::AttrValue(mapChannels)});

	ImportedPluginData node;
	node.pluginName = "node@scene";
	node.pluginType = "Node";
	node.time = 2.5;
	node.flags = ImportPluginPartialUpdate;
	float tm[4][4] = {
		{1, 0, 0, 0},
		{0, 0, -1, 0},
		{0, 1, 0, 0},
		{10, 20, 30, 1},
	};
	node.params.push_back({"transform", ImportParamAnimated, vray::AttrValue(vray::AttrTransform(tm))});

	msg.plugins = {mtl, mesh, node};

	const auto out = roundTrip(msg);

	REQUIRE(out.batchIndex == 3);
	REQUIRE(out.plugins.size() == 3);

	const auto& outMtl = out.plugins[0];
	REQUIRE(outMtl.pluginName == mtl.pluginName);
	REQUIRE(outMtl.pluginType == mtl.pluginType);
	REQUIRE(outMtl.params.size() == mtl.params.size());
	REQUIRE(outMtl.params[0].name == "diffuse");
	REQUIRE(outMtl.params[0].value.getType() == vray::ValueTypeAColor);
	REQUIRE(outMtl.params[0].value.as<vray::AttrAColor>().color.r == 0.5f);
	REQUIRE(outMtl.params[0].value.as<vray::AttrAColor>().alpha == 0.75f);
	REQUIRE(outMtl.params[1].flags == ImportParamAnimated);
	REQUIRE(outMtl.params[1].value.as<vray::AttrSimpleType<float>>().value == 0.5f);
	REQUIRE(outMtl.params[2].value.as<vray::AttrSimpleType<int>>().value == 4);
	REQUIRE(outMtl.params[3].value.as<vray::AttrSimpleType<std::string>>().value == "diffuse_channel");
	REQUIRE(outMtl.params[4].value.as<vray::AttrPlugin>().plugin == "someBrdf");
	REQUIRE(outMtl.params[4].value.as<vray::AttrPlugin>().output == "brdf_out");

	const auto& outMesh = out.plugins[1];
	REQUIRE(outMesh.params[0].value.getType() == vray::ValueTypeListInt);
	REQUIRE(*outMesh.params[0].value.as<vray::AttrListInt>().getData() == std::vector<int>({0, 1, 2, 2, 1, 3}));
	REQUIRE(outMesh.params[1].value.as<vray::AttrListVector>().getCount() == 4);
	REQUIRE(outMesh.params[1].value.as<vray::AttrListVector>().getData()->at(3) == vray::AttrVector(1.f, 1.f, 0.f));
	REQUIRE(*outMesh.params[2].value.as<vray::AttrListFloat>().getData() == std::vector<float>({0.1f, 0.2f, 0.3f}));
	REQUIRE(*outMesh.params[3].value.as<vray::AttrListString>().getData() == std::vector<std::string>({"a", "b"}));
	REQUIRE(outMesh.params[4].value.as<vray::AttrListPlugin>().getData()->at(1).output == "out");

	const auto& outMapChannels = outMesh.params[5].value.as<vray::AttrListValue>();
	REQUIRE(outMapChannels.getCount() == 1);
	const auto& outChannel = outMapChannels.getData()->at(0).as<vray::AttrListValue>();
	REQUIRE(outChannel.getCount() == 3);
	REQUIRE(outChannel.getData()->at(0).as<vray::AttrSimpleType<int>>().value == 1);
	REQUIRE(outChannel.getData()->at(1).as<vray::AttrListVector>().getCount() == 2);
	REQUIRE(*outChannel.getData()->at(2).as<vray::AttrListInt>().getData() == std::vector<int>({0, 1, 1}));

	const auto& outNode = out.plugins[2];
	REQUIRE(outNode.time == 2.5);
	REQUIRE(outNode.flags == ImportPluginPartialUpdate);
	const auto& outTm = outNode.params[0].value.as<vray::AttrTransform>();
	REQUIRE(outTm.m.v1.z == -1.f);
	REQUIRE(outTm.offs == vray::AttrVector(10.f, 20.f, 30.f));
}
