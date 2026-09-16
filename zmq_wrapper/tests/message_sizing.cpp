// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include <catch_amalgamated.hpp>

#include "zmq_import_message.hpp"
#include "zmq_scatter_message.hpp"

using namespace VrayZmqWrapper;
namespace vray = VRayBaseTypes;

namespace {

/// serializeMessage() reserves whatever a Count stream measures, so the payload is allocated
/// exactly once only while the two agree. Anything a message writes past the measured size
/// reallocates the whole buffer and trips the reservation assert in SerializerStream::grow().
/// The payloads below are deliberately over COPY_WARN_BYTES, which is what arms that assert.
template <typename TMsg>
void requireSizedExactly(const TMsg& msg, size_t minBytes) {
	SerializerStream sizer(SerializerStream::Mode::Count);
	sizer && msg.getType() && msg;

	zmq::message_t wire = serializeMessage(msg);
	REQUIRE(sizer.getSize() == wire.size());
	REQUIRE(wire.size() > minBytes);
}

const size_t ONE_MB = 1024 * 1024;

} // namespace


TEST_CASE("Big messages are sized for the whole payload, not just their largest field") {

	SECTION("ImportOnPluginData batch") {
		// A batch is a vector of plugins each holding a vector of params: nothing on that path
		// can size the message from a single field.
		MsgImportOnPluginData msg;
		msg.batchIndex = 7;
		for (int i = 0; i < 400; ++i) {
			ImportedPluginData plugin;
			plugin.pluginName = "importedPlugin@" + std::to_string(i);
			plugin.pluginType = "BRDFVRayMtl";
			for (int j = 0; j < 10; ++j) {
				plugin.params.push_back({
					"param_" + std::to_string(j), 0, vray::AttrValue(vray::AttrListFloat(64))
				});
			}
			msg.plugins.push_back(std::move(plugin));
		}

		requireSizedExactly(msg, ONE_MB);
	}

	SECTION("ScatterPreviewResult") {
		// transforms is the big field, but topo and two ints are written after it.
		MsgScatterPreviewResult msg;
		msg.requestId = 1;
		msg.status = static_cast<int>(ScatterPreviewStatus::Ok);
		msg.instanceCount = 25600;
		msg.transforms = vray::AttrValue(vray::AttrListTransform(msg.instanceCount));
		msg.topo = vray::AttrValue(vray::AttrListInt(msg.instanceCount));
		msg.elapsedMs = 23;

		requireSizedExactly(msg, ONE_MB);
	}

	SECTION("RendererOnImage") {
		// The image set is megabytes and two ints follow it.
		const std::vector<char> pixels(512 * 512 * 4 * sizeof(float), 0x7f);

		MsgRendererOnImage msg;
		msg.imageSet.sourceType = vray::ImageReady;
		msg.imageSet.images.insert({ vray::RenderChannelTypeFragColor,
			vray::AttrImage(pixels.data(), pixels.size(), vray::AttrImage::RGBA_REAL, 512, 512) });
		msg.imgId = 2;
		msg.bufferIndex = 1;

		requireSizedExactly(msg, ONE_MB);
	}
}
