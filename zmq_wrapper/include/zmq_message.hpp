// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include <string>
#include <vector>

#include <zmq.hpp>
#include "base_types.h"
#include "msg_serializer.hpp"


/// Protocol serialization
/// How to define a new message:
///
///	All helper macros and functions are defined in msg_serializer.hpp.
///
/// 1. Add a new message type to the appropriate range in MsgType enum. Messages sent
///    from the client are called 'message', messages sent from the server are called 'event'.
///
///     enum MsgType {
///        ...
///        ControlNewMessage
///     }
///
/// 2. Define the new message type. The name of the resulting class will be the message type name
///    prefixed by 'Msg', i.e. MsgControlNewMessage.
///
///	     PROTO_MESSAGE(ControlNewMessage,
///		    int number;
///         std::string item;
///      );
///
///		NOTE: For empty messages, use the EMPTY_PROTO_MESSAGE macro.
///
/// 3. Declare the default serialization procedure
///      SERIALIZE_MESSAGE(ControlNewMessage,
///         PARAM(number)
///         PARAM(item)
///      );
///
///		NOTE: For empty messages, use the SERIALIZE_EMPTY_MESSAGE macro.
///
///   3.a. If default serialization cannot be used, define custom serialization:
///      static SerializerStream& operator&& (SerializerStream& stream, const MsgControlNewMessage& msg) {
///         stream  << msg.nubmer << msg.item
///         return stream;
///      }
///      static DeserializerStream& operator&& (DeserializerStream& stream, MsgControlNewMessage& msg) {
///       	stream  >> msg.number >> msg.item;
///       	return stream;
///      }
///
/// 4. To serialize
///      const auto& zmq::message_t = serializeMessage(MsgControlNewMessage{10, "boxes"});
///
/// 5. Te deserialize
///		 DeserializerStream& stream; // Unpacked from zmq::message_t
///      const MsgControlNewMessage& message = deserializeMessage<MsgControlNewMessage>(stream);
///	     std::cout << message.number << message.item;
///

#pragma warning (push)
#pragma warning (disable: 4505) // Unreferenced function has been removed

namespace VrayZmqWrapper {

namespace vray = VRayBaseTypes;

enum class MsgType : char {
	None,

	// Plugin messages
	FirstPluginMessage,
	PluginCreate,
	PluginRemove,
	PluginUpdate,
	PluginReplace,
	LastPluginMessage,

	// Renderer messages
	FirstRendererMessage,
	RendererFree,
	RendererStart,
	RendererStop,
	RendererPause,
	RendererResume,
	RendererResize,
	RendererReset,
	RendererAbort,
	RendererInit,
	RendererEnableDistributedRendering,
	RendererLoadScene,
	RendererAppendScene,
	RendererExportScene,
	RendererExportProxy,
	RendererSetRenderMode,
	RendererSetCurrentTime,
	RendererSetCurrentFrame,
	RendererClearFrameValues,
	RendererGetImage,
	RendererSetQuality,
	RendererSetCurrentCamera,
	RendererSetCommitAction,
	RendererSetVfbOptions,
	RendererSetVRayProfiler,
	RendererSetViewportImageFormat,
	RendererSetRenderRegion,
	RendererSetCropRegion,
	RendererRenderSequence,
	RendererContinueSequence,
	RendererSetResumableRendering,
	RendererElementDone,            ///< Client -> server: element SHM buffer consumed; safe to overwrite.
	LastRendererMessage,

	// Renderer events
	FirstRendererEvent,
	RendererOnVRayLog,
	RendererOnImage,
	RendererOnChangeState,
	RendererOnAsyncOpComplete,
	RendererOnProgress,
	RendererOnElementReady,
	RendererOnPluginPropertyValues, ///< Server -> client: values V-Ray wrote into in/out plugin parameters.
	LastRendererEvent,

	// Control messages
	FirstControlMessage,
	ControlSetLogLevel,
	ControlOpenCollaboration,

	// Cosmos
	ControlOnOpenCosmos,
	ControlOnCosmosCalculateDownloadSize,
	ControlOnCosmosDownloadSize,
	ControlOnCosmosDownloadAssets,
	ControlOnCosmosDownloadedAssets,
	ControlOnUpdateScenePath,
	ControlOnCosmosImportById,      ///< Client -> server: import a Cosmos package by asset name or package id.
	ControlOnCosmosDropImport,

	// Scanned materials
	ControlOnScannedLicenseCheck,
	ControlOnScannedEncodeParameters,
	ControlOnScannedEncodedParameters,

	ControlShowUserDialog,
	ControlSetTelemetryState,
	ControlSetVfbOnTop,
	ControlUpdateVfbSettings,
	ControlUpdateVfbLayers,
	ControlShowVfb,
	ControlResetVfbToolbar,
	ControlSetVfbRenderRegion,
	ControlGetComputeDevices,
	ControlSetComputeDevices,
	ControlSetUpdateAvailable,
	ControlLogVfbMessage,
	ControlClearVfbImage,
	ControlSetVisualDebugger,
	ControlClearBitmapCache,
	ControlUpdateLightingAnalysis,
	ControlShutdown,                ///< Client -> server: request a graceful shutdown so destructors run and the license is released.
	ControlOpenVerasViewport,
	ControlOpenVerasVfb,
	LastControlMessage,

	// Control events
	FirstControlEvent,
	ControlOnStartViewportRender,
	ControlOnStartProductionRender,
	ControlOnUpdateVfbSettings,
	ControlOnUpdateVfbLayers,
	ControlOnLogMessage,
	ControlOnImportAsset,
	ControlOnRendererStatus,
	ControlOnGetComputeDevices,
	ControlOnAutoUpdateCheckChanged,
	ControlOnAppUpdateRequested,
	ControlOnLightMixTransferToScene,
	ControlOnVFBMenu,
	ControlOnAddRenderElementToScene,
	ControlOnVFBShowMessagesWindow,
	ControlOnVFBRenderRegionChanged,
	ControlOnSwitchLicenseToCommunity,
	ControlOnRenderStage,           ///< Server -> client: current render stage and its progress.
	LastControlEvent,

	// Scene import messages (dedicated SCENE_IMPORT connection), see zmq_import_message.hpp
	FirstImportMessage,
	ImportVrsceneRequest,
	ImportVrsceneCancel,
	ImportVrsceneAck,
	LastImportMessage,

	// Scene import events
	FirstImportEvent,
	ImportOnPluginData,
	ImportOnProgress,
	ImportOnResult,
	LastImportEvent,

	// Scatter preview messages (dedicated SCATTER_PREVIEW connection; scene data travels over
	// the generic plugin messages), see zmq_scatter_message.hpp
	FirstScatterMessage,
	ScatterPreviewRequest,
	ScatterPreviewCancel,
	ScatterPresetRequest,
	LastScatterMessage,

	// Scatter preview events
	FirstScatterEvent,
	ScatterPreviewResult,
	ScatterPresetResult,
	LastScatterEvent,

	// Compute devices
};



enum DRFlags : char {
	None              = 0,
	EnableDr          = 1 << 1,
	RenderOnlyOnHosts = 1 << 2,
	_SerializationShift = 8,
};

enum class RendererType : char {
	None				= 0,
	RT,
	Animation,
	SingleFrame,
	Preview,
	_SerializationShift = 0,
};


enum class RendererState : char {
	None,
	Abort,				// Rendering aborted
	Continue,			// Rendering resumed by user
	Stopped,			// Rendering stopped by stop() or from UI
	Done,				// Rendering done
	Progress,			// [Not a state] Numerical progress message
	ProgressMessage,	// [Not a state] Textual progress message
};

/// Asyncronous operations for which a RendererOnAsyncOpComplete message will be sent to the client
enum class RendererAsyncOp: char {
	None,
	ExportVrscene,		// Export .vrscene
	ExportVrmesh		// Export .vrmesh
};


enum class ImportedAssetType : char {
	None,
	Material,
	VRMesh,
	HDRI,
	Extras,
	ParallaxInterior,
	// A Chaos Cosmos Asset Set. Carries no geometry - only a settings.json manifest
	// referencing other packages. The addon parses it and imports each member separately.
	AssetSet,
	// A Chaos Scatter preset. Carries no geometry either - only a .mbc config, which the
	// server reads with GeomUtils::readScatterPreset. The models it references are separate
	// packages, imported like set members.
	ScatterPreset
};


enum class VRayStatusType : char {
	None = 0,
	MainRendererAvailable,
	MainRendererUnavailable,
	LicenseAcquired,
	LicenseDropped

};

enum class ComputeDeviceType : int {
	CUDA = 0,
	Optix = 1,
	Metal = 2,
	HIP = 3,
	LastDevice = HIP
};

enum class VfbMessageLevel : int {
	MessageError = 0,
	MessageWarning = 1,
	MessageInfo = 2,
	MessageDebug = 3
};


// Impelments the protocol for VRay::RenderSizeParams.
struct RenderSizes {
	enum class Bitmask : int {
		None      = 0x0,
		ImgSize   = 0x1,
		CropRgn   = 0x2,
		RenderRgn = 0x4
	};

	RenderSizes() = default;

	bool operator==(const RenderSizes rhs) const {
		return
			imgWidth      == rhs.imgWidth &&
			imgHeight     == rhs.imgHeight &&
			bmpWidth      == rhs.bmpWidth &&
			bmpHeight     == rhs.bmpHeight &&
			cropRgnLeft   == rhs.cropRgnLeft &&
			cropRgnTop    == rhs.cropRgnTop &&
			cropRgnWidth  == rhs.cropRgnWidth &&
			cropRgnHeight == rhs.cropRgnHeight &&
			rgnLeft       == rhs.rgnLeft &&
			rgnTop        == rhs.rgnTop &&
			rgnWidth      == rhs.rgnWidth &&
			rgnHeight     == rhs.rgnHeight;
	}


	int bitmask = 0;	// Same as VRay::RenderSizeParams::RenderSizeBitmask

	// Output image size. This is the size of the image that we get in onImageUpdated.
	int imgWidth = 0;
	int imgHeight = 0;

	// Framebuffer size that VRay uses for the actual rendering. The image is scaled
	// to imgWidth/Height to produce the actual output. If scaling is disproportional,
	// the output image will be stretched.
	// Usually this is the same as imgWidth/Height for mono and double that in one of
	// the directions for stereo rendering.
	int bmpWidth = 0;
	int bmpHeight = 0;

	// Size of the region inside bmpWidth/Height we actually want to render.
	// This is used when only a portion of the viewport/VBF is selected for rendering.
	float cropRgnLeft = 0;
	float cropRgnTop = 0;
	float cropRgnWidth = 0;
	float cropRgnHeight = 0;

	// Size of the region inside imgWidth/Height we want to render
	int rgnLeft = 0;
	int rgnTop = 0;
	int rgnWidth = 0;
	int rgnHeight = 0;

	// Whether the exporter controls the VFB "Render Region" toolbar button for this resize.
	// False leaves the button (and region) untouched.
	bool manageRenderRegion = true;
};


SERIALIZE_STRUCT(RenderSizes,
	PARAM(bitmask)
	PARAM(imgWidth)
	PARAM(imgHeight)
	PARAM(bmpWidth)
	PARAM(bmpHeight)
	PARAM(cropRgnLeft)
	PARAM(cropRgnTop)
	PARAM(cropRgnWidth)
	PARAM(cropRgnHeight)
	PARAM(rgnLeft)
	PARAM(rgnTop)
	PARAM(rgnWidth)
	PARAM(rgnHeight)
	PARAM(manageRenderRegion)
);


struct AssetMetaData {
	ImportedAssetType type;
	std::string materialFile;
	std::string objectFile;
	VRayBaseTypes::AttrListString assetNames;
	VRayBaseTypes::AttrListString assetLocations;
};


/// Options to pass in VRayExportSettings when exporting a. vrscene
struct ExportSettings {
	struct SubFileInfo {
		std::string fileNameSuffix;
		std::string pluginType;
	};

	bool compressed;
	bool hexArrays;
	bool hexTransforms;
	bool cloudExport;
	std::string hostAppString;
	std::string filePath;
	std::vector<SubFileInfo> subFileInfo;
};



static SerializerStream& operator&& (SerializerStream& stream, const ExportSettings& settings) {
	stream  << settings.compressed
			<< settings.hexArrays << settings.hexTransforms
			<< settings.cloudExport
			<< settings.hostAppString
			<< settings.filePath
			<< settings.subFileInfo.size();

	for ( const auto item : settings.subFileInfo) {
		stream << item.fileNameSuffix << item.pluginType;
	}

	return stream;
}

static DeserializerStream& operator&& (DeserializerStream& stream, ExportSettings& settings) {
	size_t numSubFiles = 0;

	stream  >> settings.compressed
			>> settings.hexArrays >> settings.hexTransforms
			>> settings.cloudExport
			>> settings.hostAppString
			>> settings.filePath
			>> numSubFiles;

	settings.subFileInfo.resize(numSubFiles);

	for (size_t i = 0; i < numSubFiles; ++i) {
		stream  >> settings.subFileInfo[i].fileNameSuffix
			>> settings.subFileInfo[i].pluginType;
	}

	return stream;
}


struct HostInfo {
	std::string vrayVersion;
	std::string buildVersion;
	std::string blenderVersion;
};


SERIALIZE_STRUCT(HostInfo,
	PARAM(vrayVersion)
	PARAM(buildVersion)
	PARAM(blenderVersion)
);


PROTO_MESSAGE( PluginCreate,
	std::string pluginName;
	std::string pluginType;
	bool allowTypeChanges = false;
);

SERIALIZE_MESSAGE(PluginCreate,
	PARAM(pluginName)
	PARAM(pluginType)
	PARAM(allowTypeChanges)
);


PROTO_MESSAGE(PluginRemove,
	std::string pluginName;
);

SERIALIZE_MESSAGE(PluginRemove,
	PARAM(pluginName)
);


/// MsgPluginUpdate
PROTO_MESSAGE(PluginUpdate,
	std::string pluginName;
	std::string propertyName;
	vray::AttrValue propertyValue;

	uint32_t flags = 0;
	inline void setAnimatable(bool animatable) {
		if (animatable) {
			flags |= vray::PluginUpdateFlags::PluginValueAnimatable;
		}
	}
	inline void setForceUpdate(bool forceUpdate) {
		if (forceUpdate) {
			flags |= vray::PluginUpdateFlags::PluginValueForceUpdate;
		}
	}

	inline void setReCreateAttribute(bool recreateAttribute) {
		if (recreateAttribute) {
			flags |= vray::PluginUpdateFlags::PluginReCreateAttr;
		}
    }
	inline bool getForceUpdate() const { return (flags & vray::PluginUpdateFlags::PluginValueForceUpdate) != 0; }
	inline bool getAsString() const { return (flags & vray::PluginUpdateFlags::PluginValueAsString) != 0; }
	inline bool getAnimatable() const { return (flags & vray::PluginUpdateFlags::PluginValueAnimatable) != 0; }
	inline bool getReCreateAttribute() const { return (flags & vray::PluginUpdateFlags::PluginReCreateAttr) != 0; }

);


SERIALIZE_MESSAGE(PluginUpdate,
	PARAM(pluginName)
	PARAM(propertyName)
	PARAM(propertyValue)
	PARAM(flags)
);


/// MsgPluginReplace
PROTO_MESSAGE(PluginReplace,
	std::string oldPluginName;
	std::string newPluginName;
);

SERIALIZE_MESSAGE(PluginReplace,
	PARAM(oldPluginName)
	PARAM(newPluginName)
);

/// MsgImage
PROTO_MESSAGE(RendererOnImage,
	vray::AttrImageSet imageSet;
	int imgId = -1;
	int bufferIndex = 0;  ///< Which of the two double-buffers was written (0 or 1)
);

SERIALIZE_MESSAGE(RendererOnImage,
	PARAM(imageSet)
	PARAM(imgId)
	PARAM(bufferIndex)
);


/// MsgRendererElementReady - per-element shared-memory notification. The pixel data is
/// already in the element SHM region; this message tells the client which slot to copy
/// from and where it goes.
PROTO_MESSAGE(RendererOnElementReady,
	std::string pluginInstanceName;  ///< V-Ray plugin instance name (the `name` attribute).
	int subIndex = 0;                ///< Cryptomatte layer, ObjectSelect 0/1/2, or 0 for generic.
	int width    = 0;
	int height   = 0;
	int channels = 0;                ///< 1, 3, or 4.
	int imageType = 0;               ///< VRayBaseTypes::AttrImage::ImageType cast to int.
	std::string metadataKey;         ///< Optional metadata key (e.g. "cryptomatte.<instance>"). Empty if absent.
	std::string metadataValue;
);

SERIALIZE_MESSAGE(RendererOnElementReady,
	PARAM(pluginInstanceName)
	PARAM(subIndex)
	PARAM(width)
	PARAM(height)
	PARAM(channels)
	PARAM(imageType)
	PARAM(metadataKey)
	PARAM(metadataValue)
);


/// One property value read back from a V-Ray plugin instance. Used for in/out parameters
/// which the core fills in while rendering and the client can only read, e.g.
/// RenderChannelVelocity::max_velocity_last_frame.
struct PluginPropertyValueData {
	std::string     pluginName;    ///< V-Ray plugin instance name, as exported by the client.
	std::string     propertyName;
	vray::AttrValue value;
};

/// MsgRendererOnPluginPropertyValues - sent after each rendered frame with the values the
/// core wrote into the watched plugin parameters. Carries an arbitrary collection so that
/// other read-back parameters can be added without a new message.
PROTO_MESSAGE(RendererOnPluginPropertyValues,
	std::vector<PluginPropertyValueData> values;
);

static SerializerStream& operator&& (SerializerStream& s, const MsgRendererOnPluginPropertyValues& msg) {
	s << static_cast<int>(msg.values.size());
	for (const auto& v : msg.values) {
		s << v.pluginName << v.propertyName << v.value;
	}
	return s;
}

static DeserializerStream& operator&& (DeserializerStream& s, MsgRendererOnPluginPropertyValues& msg) {
	int count = 0;
	s >> count;
	msg.values.resize(count);
	for (auto& v : msg.values) {
		s >> v.pluginName >> v.propertyName >> v.value;
	}
	return s;
}


/// Client -> server: per-element SHM consumed; the server may overwrite the slot
/// for the next layer. Required because multiple layers reuse one SHM region.
PROTO_MESSAGE(RendererElementDone,
	int subIndex = 0;
);

SERIALIZE_MESSAGE(RendererElementDone,
	PARAM(subIndex)
);


/// MsgVfbLayers
PROTO_MESSAGE(ControlOnUpdateVfbLayers,
	std::string vfbLayersJson;
);

SERIALIZE_MESSAGE(ControlOnUpdateVfbLayers,
	PARAM(vfbLayersJson)
);

/// MsgVRayLog
PROTO_MESSAGE(RendererOnVRayLog,
	int logLevel;
	std::string log;
);

SERIALIZE_MESSAGE(RendererOnVRayLog,
	PARAM(logLevel)
	PARAM(log)
);


/// MsgRendererFree
EMPTY_PROTO_MESSAGE(RendererFree);
SERIALIZE_EMPTY_MESSAGE(RendererFree);



/// MsgRendererStart
/// imageToBlender = false suppresses both the Combined image and per-element emits
/// for the duration of this render - Blender allocates no pass buffers and V-Ray's
/// VFB is the only consumer. The client sets it from scene.vray.Exporter.image_to_blender
/// before calling vray.renderStart().
PROTO_MESSAGE(RendererStart,
	bool imageToBlender = true;
);

SERIALIZE_MESSAGE(RendererStart,
	PARAM(imageToBlender)
);


/// MsgRendererStop
EMPTY_PROTO_MESSAGE(RendererStop);
SERIALIZE_EMPTY_MESSAGE(RendererStop);


/// MsgRendererPause
EMPTY_PROTO_MESSAGE(RendererPause);
SERIALIZE_EMPTY_MESSAGE(RendererPause);

/// MsgRendererResume
EMPTY_PROTO_MESSAGE(RendererResume);
SERIALIZE_EMPTY_MESSAGE(RendererResume);

/// MsgRendererReset
EMPTY_PROTO_MESSAGE(RendererReset);
SERIALIZE_EMPTY_MESSAGE(RendererReset);

/// MsgRendererAbort
EMPTY_PROTO_MESSAGE(RendererAbort);
SERIALIZE_EMPTY_MESSAGE(RendererAbort);


/// MsgRendererInit
PROTO_MESSAGE(RendererInit,
	RendererType rendererType;
	int renderThreads;
	int exporterType;
);

SERIALIZE_MESSAGE(RendererInit,
	PARAM(rendererType)
	PARAM(renderThreads)
	PARAM(exporterType)
);


/// MsgRendererResize
PROTO_MESSAGE(RendererResize,
	RenderSizes renderSizes;
);

SERIALIZE_MESSAGE(RendererResize,
	PARAM(renderSizes)
);


/// MsgRendererEnableDistributedRendering
PROTO_MESSAGE(RendererEnableDistributedRendering,
	std::string hosts;
	DRFlags drFlags;
	std::string remoteDispatcher;
);

SERIALIZE_MESSAGE(RendererEnableDistributedRendering,
	PARAM(hosts)
	PARAM(drFlags)
	PARAM(remoteDispatcher)
);


/// MsgRendererLoadScene
PROTO_MESSAGE(RendererLoadScene,
	std::string fileName;
);

SERIALIZE_MESSAGE(RendererLoadScene,
	PARAM(fileName)
);


/// MsgRendererAppendScene
PROTO_MESSAGE(RendererAppendScene,
	std::string fileName;
);

SERIALIZE_MESSAGE(RendererAppendScene,
	PARAM(fileName)
);


/// MsgRendererExportScene
PROTO_MESSAGE(RendererExportScene,
	ExportSettings exportSettings;
);

SERIALIZE_MESSAGE(RendererExportScene,
	PARAM(exportSettings)
);

/// MsgRendererExportProxy
PROTO_MESSAGE(RendererExportProxy,
	std::string filePath;
	int elementsPerVoxel = 64;
	int previewFaces = 10000;
	int previewType = 3; //!< @see VRay::ProxyCreateParams::PreviewTypes (0-3)
	int animOn = 0;
	int startFrame = 0;
	int endFrame = 0;
);

SERIALIZE_MESSAGE(RendererExportProxy,
	PARAM(filePath)
	PARAM(elementsPerVoxel)
	PARAM(previewFaces)
	PARAM(previewType)
	PARAM(animOn)
	PARAM(startFrame)
	PARAM(endFrame)
);

/// MsgRendererSetRenderMode
PROTO_MESSAGE(RendererSetRenderMode,
	int renderMode;	// VRay::VRayRenderer::RenderMode
);

SERIALIZE_MESSAGE(RendererSetRenderMode,
	PARAM(renderMode)
);


/// MsgRendererSetVRayProfiler
PROTO_MESSAGE(RendererSetVRayProfiler,
	int mode;					// VRay::VRayProfilerSettings::Mode (0 = Off)
	int maxDepth;				// Max ray depth to profile, range [1, 8]
	std::string outputDirectory;	// Directory for the profiler reports ("" => temp dir)
	std::string sceneName;			// Name of the profiled scene (filename suffix)
);

SERIALIZE_MESSAGE(RendererSetVRayProfiler,
	PARAM(mode)
	PARAM(maxDepth)
	PARAM(outputDirectory)
	PARAM(sceneName)
);


/// MsgRendererSetCurrentTime
PROTO_MESSAGE(RendererSetCurrentTime,
	float frame;
);

SERIALIZE_MESSAGE(RendererSetCurrentTime,
	PARAM(frame)
);


/// MsgRendererSetCurrentFrame
PROTO_MESSAGE(RendererSetCurrentFrame,
	float frame;
);

SERIALIZE_MESSAGE(RendererSetCurrentFrame,
	PARAM(frame)
);


/// MsgRendererClearFrameValues
PROTO_MESSAGE(RendererClearFrameValues,
	float upToTime;
);

SERIALIZE_MESSAGE(RendererClearFrameValues,
	PARAM(upToTime)
);


/// MsgRendererGetImage
PROTO_MESSAGE(RendererGetImage,
	int         renderElementType;   // VRay::RenderElement::Type
	std::string pluginInstanceName;  // V-Ray plugin instance name; empty selects the first match.
	int         subIndex      = 0;   // Cryptomatte layer index, or ObjectSelect 0=matte/1=filter/2=alpha.
);

SERIALIZE_MESSAGE(RendererGetImage,
	PARAM(renderElementType)
	PARAM(pluginInstanceName)
	PARAM(subIndex)
);


/// MsgRendererSetQuality
PROTO_MESSAGE(RendererSetQuality,
	int jpegQuality;
);

SERIALIZE_MESSAGE(RendererSetQuality,
	PARAM(jpegQuality)
);


/// MsgRendererSetCurrentCamera
PROTO_MESSAGE(RendererSetCurrentCamera,
	std::string cameraName;
);

SERIALIZE_MESSAGE(RendererSetCurrentCamera,
	PARAM(cameraName)
);


/// MsgRendererSetResumableRendering
PROTO_MESSAGE(RendererSetResumableRendering,
	bool        enabled;
	std::string outputFileName;
	int         autosaveSeconds;
	bool        deleteOnSuccess;
);

SERIALIZE_MESSAGE(RendererSetResumableRendering,
	PARAM(enabled) PARAM(outputFileName) PARAM(autosaveSeconds) PARAM(deleteOnSuccess)
);


/// MsgRendererSetCommitAction
PROTO_MESSAGE(RendererSetCommitAction,
	vray::CommitAction commitAction;
);

SERIALIZE_MESSAGE(RendererSetCommitAction,
	PARAM(commitAction)
);


/// MsgRendererSetVfbOptions
PROTO_MESSAGE(RendererSetVfbOptions,
	int vfbFlags;
);

SERIALIZE_MESSAGE(RendererSetVfbOptions,
	PARAM(vfbFlags)
);


/// MsgRendererSetViewportImageFormat
PROTO_MESSAGE(RendererSetViewportImageFormat,
	vray::AttrImage::ImageType format;
);

SERIALIZE_MESSAGE(RendererSetViewportImageFormat,
	PARAM(format)
);


/// MsgRendererSetRenderRegion
PROTO_MESSAGE(RendererSetRenderRegion,
	vray::AttrListInt coords;
);

SERIALIZE_MESSAGE(RendererSetRenderRegion,
	PARAM(coords)
);


/// MsgRendererSetCropRegion
PROTO_MESSAGE(RendererSetCropRegion,
	vray::AttrListInt coords;
);

SERIALIZE_MESSAGE(RendererSetCropRegion,
	PARAM(coords)
);


/// MsgRendererRenderSequence
PROTO_MESSAGE(RendererRenderSequence,
	vray::AttrListInt sequence;
	bool imageToBlender = true;
);

SERIALIZE_MESSAGE(RendererRenderSequence,
	PARAM(sequence)
	PARAM(imageToBlender)
);


/// MsgRendererContinueSequence
EMPTY_PROTO_MESSAGE(RendererContinueSequence);
SERIALIZE_EMPTY_MESSAGE(RendererContinueSequence);


/// MsgRendererOnChangeState
PROTO_MESSAGE(RendererOnChangeState,
	RendererState state;
	float renderProgress;
	std::string progressMessage;
	int lastRenderedFrame;
);

static SerializerStream& operator<< (SerializerStream& s, const MsgRendererOnChangeState&  msg) {
	s << msg.state;

	switch(msg.state){
		case RendererState::Progress:			s << msg.renderProgress; break;
		case RendererState::ProgressMessage:	s << msg.progressMessage; break;
		case RendererState::Continue:			s << msg.lastRenderedFrame; break;
		default:
			;
	}

	return s;
}

static DeserializerStream& operator>> (DeserializerStream& s, MsgRendererOnChangeState&  msg) {
	s >> msg.state;

	switch(msg.state){
	case RendererState::Progress:			s >> msg.renderProgress; break;
	case RendererState::ProgressMessage:	s >> msg.progressMessage; break;
	case RendererState::Continue:			s >> msg.lastRenderedFrame; break;
	default:
		;
	}

	return s;
}


/// MsgRendererOnAsyncOpComplete
PROTO_MESSAGE(RendererOnAsyncOpComplete,
	RendererAsyncOp operation;
	bool success;
	std::string message;
);

SERIALIZE_MESSAGE(RendererOnAsyncOpComplete,
	PARAM(operation)
	PARAM(success)
	PARAM(message)
);


/// MsgRendererOnProgress
PROTO_MESSAGE(RendererOnProgress,
	int elements;		// Render job completed elements
	int totalElements;	// Render job total elements
);

SERIALIZE_MESSAGE(RendererOnProgress,
	PARAM(elements)
	PARAM(totalElements)
);

/// MsgControlSetLogLevel
PROTO_MESSAGE(ControlSetLogLevel,
	int logLevel;
	bool enableQtLogs;
);

SERIALIZE_MESSAGE(ControlSetLogLevel,
	PARAM(logLevel)
	PARAM(enableQtLogs)
);


/// MsgControlOpenCollaboration
PROTO_MESSAGE(ControlOpenCollaboration,
	HostInfo hostInfo;
);

SERIALIZE_MESSAGE(ControlOpenCollaboration,
	PARAM(hostInfo)
);

/////////////////////// /////////////////////// /////////////////////// ///////////////////////
/////////////////////// /////////////////////// /////////////////////// ///////////////////////

/// MsgControlOnOpenCosmos
PROTO_MESSAGE(ControlOnOpenCosmos,
	int browserPage;
);

SERIALIZE_MESSAGE(ControlOnOpenCosmos,
	PARAM(browserPage)
);

/// MsgControlOnCosmosCalculateDownloadSize
PROTO_MESSAGE(ControlOnCosmosCalculateDownloadSize,
	vray::AttrListString packgeIds;
	vray::AttrListInt revisionIds;
	vray::AttrListString missingTextures;
);

SERIALIZE_MESSAGE(ControlOnCosmosCalculateDownloadSize,
	PARAM(packgeIds)
	PARAM(revisionIds)
	PARAM(missingTextures)
);

enum class CosmosRelinkStatus {
	AllAssetsValid,
	NotLoggedIn,
	RelinkOnly,
	DownloadAndRelink
};

/// MsgControlOnCosmosDownloadSize
PROTO_MESSAGE(ControlOnCosmosDownloadSize,
	int32_t downloadSizeMb;
	CosmosRelinkStatus relinkStatus;
);

SERIALIZE_MESSAGE(ControlOnCosmosDownloadSize,
	PARAM(downloadSizeMb)
	PARAM(relinkStatus)
);

/// MsgControlOnCosmosDownloadAssets
EMPTY_PROTO_MESSAGE(ControlOnCosmosDownloadAssets);
SERIALIZE_EMPTY_MESSAGE(ControlOnCosmosDownloadAssets);


enum class CosmosDownloadStatus {
	Cancelled = 0,
    Timeout = 1,
    Done = 2,
};

/// MsgControlOnCosmosDownloadedAssets
PROTO_MESSAGE(ControlOnCosmosDownloadedAssets,
	vray::AttrListString relinkedPaths;
	CosmosDownloadStatus downloadStatus;
);

SERIALIZE_MESSAGE(ControlOnCosmosDownloadedAssets,
	PARAM(relinkedPaths)
	PARAM(downloadStatus)
);

/// MsgControlOnUpdateScenePath
PROTO_MESSAGE(ControlOnUpdateScenePath,
	std::string scenePath;
);

SERIALIZE_MESSAGE(ControlOnUpdateScenePath,
	PARAM(scenePath)
);

/// MsgControlOnCosmosImportById
/// Client -> server request to import one or more Cosmos packages identified by
/// asset name or package id. The server resolves each entry through the Cosmos
/// client (name -> package id, falling back to treating it as a raw id) and drives
/// the regular import path, so the result arrives back as MsgControlOnImportAsset(s).
PROTO_MESSAGE(ControlOnCosmosImportById,
	vray::AttrListString assetIdsOrNames;
	bool applyTriplanarMapping;
	bool applyRealWorldScale;
	// Optional per-entry opaque tokens, parallel to assetIdsOrNames. Used when the addon
	// imports the members of a Cosmos Asset Set: the token is echoed back in the resulting
	// MsgControlOnImportAsset so the addon can match each imported asset to the manifest
	// instance that requested it. An empty list means "no tokens"; entries are then resolved
	// as asset names first, whereas a tokened entry is always treated as a raw package id.
	vray::AttrListString instanceTokens;
);

/// MsgControlOnCosmosDropImport
/// Sent from the Blender addon to the server when the user drops a Cosmos
/// asset stub file onto a Blender viewport. The server invokes
/// GalaxyClient::importPackage(packageId, ctx) which downloads the asset
/// and calls back into BlenderCosmosImporter::importMaterial/importCompositeAsset,
/// which then produce the usual MsgControlOnImportAsset (carrying drop coords
/// through via ImportRequestContext) back to the addon.
PROTO_MESSAGE(ControlOnCosmosDropImport,
	std::string packageId;
	uint32_t revisionId;
	// World-space position of the drop in Blender units. Computed in the
	// drop operator by raycasting from the viewport mouse position; on a
	// miss, falls back to a point in front of the camera. Passed through
	// unchanged by the server and used on the Python side to set the
	// placement location of newly imported objects.
	double worldX;
	double worldY;
	double worldZ;
	// Name of the Blender scene object the drop ray hit (empty on miss).
	// Used on the Python side to assign dropped Material assets to the
	// hovered object's material slots.
	std::string dropTargetObject;
	// Specific material-slot index to assign the dropped material to, or
	// -1 ("not specified") to let the Python side use its default policy
	// (clear all slots, append to slot 0). Set from the hit face's
	// material_index for viewport drops, or from the target object's
	// active_material_index for Outliner drops. Ignored for non-Material
	// asset types.
	int32_t dropTargetSlot;
	// Surface normal at the hit point (populated only on a geometry hit,
	// i.e. hasHitNormal=true). Used on the Python side to orient Decals
	// so they project INTO the hit surface.
	bool hasHitNormal;
	double normalX;
	double normalY;
	double normalZ;
	// Flags taken from the 'options' block of the Cosmos drag JSON payload.
	bool applyTriplanarMapping;
	bool applyRealWorldScale;
	// Set when Ctrl was held during the drop. Asks the addon to align the
	// imported asset's local +Z to the hit surface normal even for assets
	// that wouldn't otherwise be re-oriented (anything not tagged "wall"
	// and not a Decal). Ignored when hasHitNormal is false.
	bool forceNormalAlign;
);

SERIALIZE_MESSAGE(ControlOnCosmosImportById,
	PARAM(assetIdsOrNames)
	PARAM(applyTriplanarMapping)
	PARAM(applyRealWorldScale)
	PARAM(instanceTokens)
);

SERIALIZE_MESSAGE(ControlOnCosmosDropImport,
	PARAM(packageId)
	PARAM(revisionId)
	PARAM(worldX)
	PARAM(worldY)
	PARAM(worldZ)
	PARAM(dropTargetObject)
	PARAM(dropTargetSlot)
	PARAM(hasHitNormal)
	PARAM(normalX)
	PARAM(normalY)
	PARAM(normalZ)
	PARAM(applyTriplanarMapping)
	PARAM(applyRealWorldScale)
	PARAM(forceNormalAlign)
);

/// MsgControlOnImportAsset
PROTO_MESSAGE(ControlOnImportAsset,
	ImportedAssetType assetType;
	vray::AttrListString assetNames;
	vray::AttrListString assetLocations;
	std::string materialFile;
	std::string objectFile;
	std::string lightFile;
	// The .vrmat holding a Cosmos light asset's LightLuminaire, from the package's "backward
	// incompatible plugins" map. Kept out of lightFile so V-Ray 6 ignores it. Empty if none.
	std::string luminaireFile;
	// The settings.json manifest of a Cosmos Asset Set. Set only for assetType AssetSet,
	// which carries no other file - the addon parses it and imports the members separately.
	std::string settingsFile;
	// Echo of MsgControlOnCosmosImportById::instanceTokens for this import. Non-empty only
	// for a member of an Asset Set; the addon uses it to look up the manifest instance this
	// asset was imported for, and to place it accordingly.
	std::string setInstanceToken;
	std::string packageId;
	uint32_t revisionId;
	// The asset's name in the Cosmos browser. Unrelated to 'assetNames' above, which holds location-map keys.
	std::string packageName;
	bool isAnimated;
	// Plane dimensions in centimeters for ParallaxInterior assets; zero otherwise.
	double planeWidth;
	double planeHeight;
	// Whether the Cosmos browser requested wrapping material textures in TexTriPlanar.
	bool applyTriplanarMapping;
	// Real-world texture dimensions in centimeters supplied by Cosmos; zero when unknown.
	// Used to derive the triplanar size when applyTriplanarMapping is true.
	float texRealWorldWidth;
	float texRealWorldHeight;
	// Drop-origin fields, populated when this import was triggered by a
	// drag-and-drop operation. hasDropCoords=false means "imported via the
	// Cosmos browser's Import button" and worldX/worldY/worldZ should be
	// ignored (use default placement like 3D cursor).
	bool hasDropCoords;
	double worldX;
	double worldY;
	double worldZ;
	// Name of the scene object the drop ray hit (empty on miss / non-drop).
	// Used by the addon to assign dropped Material assets to that object's
	// material slots instead of creating a free-floating material datablock.
	std::string dropTargetObject;
	// Specific material-slot index for Material drops, or -1 to use the
	// addon's default policy. See MsgControlOnCosmosDropImport.
	int32_t dropTargetSlot;
	// Surface normal at the hit point. Valid only when hasHitNormal is true
	// (a geometry raycast hit occurred); for drops over empty space no
	// normal is available. Consumed on the Python side to orient Decals.
	bool hasHitNormal;
	double normalX;
	double normalY;
	double normalZ;
	// Cosmos surface-attachment tag for the dropped package. Empty string
	// for normal floor-standing assets; "wall" and "ceiling" for assets
	// that should be pre-rotated so their attachment side faces the drop
	// surface (matching the 3dsmax behavior - see VMAX-12393). Consumed on
	// the Python side together with the hit normal to align the asset to
	// the surface it was dropped on.
	std::string surfaceAttachment;
	// Set when Ctrl was held during the drop - the addon's "force align
	// to hit normal" override for assets that wouldn't otherwise be
	// re-oriented. See MsgControlOnCosmosDropImport.
	bool forceNormalAlign;
);

SERIALIZE_MESSAGE(ControlOnImportAsset,
	PARAM(assetType)
	PARAM(assetNames)
	PARAM(assetLocations)
	PARAM(materialFile)
	PARAM(objectFile)
	PARAM(lightFile)
	PARAM(luminaireFile)
	PARAM(settingsFile)
	PARAM(setInstanceToken)
	PARAM(packageId)
	PARAM(revisionId)
	PARAM(packageName)
	PARAM(isAnimated)
	PARAM(planeWidth)
	PARAM(planeHeight)
	PARAM(applyTriplanarMapping)
	PARAM(texRealWorldWidth)
	PARAM(texRealWorldHeight)
	PARAM(hasDropCoords)
	PARAM(worldX)
	PARAM(worldY)
	PARAM(worldZ)
	PARAM(dropTargetObject)
	PARAM(dropTargetSlot)
	PARAM(hasHitNormal)
	PARAM(normalX)
	PARAM(normalY)
	PARAM(normalZ)
	PARAM(surfaceAttachment)
	PARAM(forceNormalAlign)
);

/// MsgControlOnScannedLicenseCheck
PROTO_MESSAGE(ControlOnScannedLicenseCheck,
	bool license;
);

SERIALIZE_MESSAGE(ControlOnScannedLicenseCheck,
	PARAM(license)
);

/// MsgControlOnScannedEncodeParameters
PROTO_MESSAGE(ControlOnScannedEncodeParameters,
	int materialId;
	std::string nodeName;
	std::string paramsJson;
);

SERIALIZE_MESSAGE(ControlOnScannedEncodeParameters,
	PARAM(materialId)
	PARAM(nodeName)
	PARAM(paramsJson)
);

/// MsgControlOnScannedEncodedParameters
PROTO_MESSAGE(ControlOnScannedEncodedParameters,
	int materialId;
	std::string nodeName;
	bool licensed;
	vray::AttrListInt encodedParams;
);

SERIALIZE_MESSAGE(ControlOnScannedEncodedParameters,
	PARAM(materialId)
	PARAM(nodeName)
	PARAM(licensed)
	PARAM(encodedParams)
);

/// MsgControlShowProductInfo
PROTO_MESSAGE(ControlShowUserDialog,
	std::string json;
);

SERIALIZE_MESSAGE(ControlShowUserDialog,
	PARAM(json)
);


/// MsgControlSetTelemetryState
PROTO_MESSAGE(ControlSetTelemetryState,
	bool anonymous;
	bool personalized;
);

SERIALIZE_MESSAGE(ControlSetTelemetryState,
	PARAM(anonymous)
	PARAM(personalized)
);


/// MsgControlSetVfbOnTop
PROTO_MESSAGE(ControlSetVfbOnTop,
	int onTopFlags;
);

SERIALIZE_MESSAGE(ControlSetVfbOnTop,
	PARAM(onTopFlags)
);



/// MsgControlUpdateVfbSettings
PROTO_MESSAGE(ControlUpdateVfbSettings,
	std::string vfbSettings;
);

SERIALIZE_MESSAGE(ControlUpdateVfbSettings,
	PARAM(vfbSettings)
);



/// MsgControlUpdateVfbLayers
PROTO_MESSAGE(ControlUpdateVfbLayers,
	std::string vfbLayersInfo;
);

SERIALIZE_MESSAGE(ControlUpdateVfbLayers,
	PARAM(vfbLayersInfo)
);


/// MsgControlOnRenderStatus
PROTO_MESSAGE(ControlOnRendererStatus,
	VRayStatusType status;
);

SERIALIZE_MESSAGE(ControlOnRendererStatus,
	PARAM(status)
);



/// MsgControlShowVfb
PROTO_MESSAGE(ControlShowVfb,
	bool show;
);

SERIALIZE_MESSAGE(ControlShowVfb,
	PARAM(show)
);


/// MsgControlResetVfbToolbar
EMPTY_PROTO_MESSAGE(ControlResetVfbToolbar);
SERIALIZE_EMPTY_MESSAGE(ControlResetVfbToolbar);

/// MsgControlClearVfbImage - clears the VFB image. Sent by the client on a new
/// scene load and at the start of IPR VFB / PROD renders without a render region
/// so the previous frame is wiped before fresh pixels arrive.
EMPTY_PROTO_MESSAGE(ControlClearVfbImage);
SERIALIZE_EMPTY_MESSAGE(ControlClearVfbImage);

/// MsgControlClearBitmapCache - frees all cached bitmaps. Sent by the client on scene reload.
EMPTY_PROTO_MESSAGE(ControlClearBitmapCache);
SERIALIZE_EMPTY_MESSAGE(ControlClearBitmapCache);
/// MsgControlUpdateLightingAnalysis - re-applies the Lighting Analysis render element's
/// display settings (quantity, value range, scale, display mode) to the current render in
/// the VFB without re-rendering. Triggered by the Lighting Analysis channel's Update button.
EMPTY_PROTO_MESSAGE(ControlUpdateLightingAnalysis);
SERIALIZE_EMPTY_MESSAGE(ControlUpdateLightingAnalysis);

/// MsgControlShutdown - request a graceful server shutdown. Sent by the client when Blender is
/// closing or the addon is disabled, so the server runs its normal teardown (renderer destructors
/// unlink the shared memory objects and the GUI license is released) instead of being SIGKILL'd.
EMPTY_PROTO_MESSAGE(ControlShutdown);
SERIALIZE_EMPTY_MESSAGE(ControlShutdown);

/// MsgControlOpenVerasViewport - open Chaos Veras seeded with a captured Blender 3D
/// viewport image. imagePath is an absolute path to a temp image (JPEG) written by the
/// addon. Used for the "Viewport Image to Veras" menu command. Pressing the VFB toolbar's
/// own Veras button is driven directly by the AppSDK via the setOnVerasUpload callback and
/// needs no message.
PROTO_MESSAGE(ControlOpenVerasViewport,
	std::string imagePath;
);
SERIALIZE_MESSAGE(ControlOpenVerasViewport,
	PARAM(imagePath)
);

/// MsgControlOpenVerasVfb - open Chaos Veras seeded with the current V-Ray Frame Buffer
/// image. Used for the "VFB Image to Veras" menu command (as opposed to pressing the VFB
/// toolbar's own Veras button, which the AppSDK drives directly with no message needed).
EMPTY_PROTO_MESSAGE(ControlOpenVerasVfb);
SERIALIZE_EMPTY_MESSAGE(ControlOpenVerasVfb);

/// MsgControlSetVfbRenderRegion - sets the VFB Render Region rectangle and toolbar
/// button state. When 'enabled' is false (or width/height are invalid), the render
/// region is cleared (renders the whole image) and the toolbar button is turned off.
/// imgWidth/imgHeight set the VFB image size at the same time so the region coords
/// are interpreted against a known canvas; pass <= 0 to leave the image size as-is.
PROTO_MESSAGE(ControlSetVfbRenderRegion,
	int  x;
	int  y;
	int  width;
	int  height;
	int  imgWidth;
	int  imgHeight;
	bool enabled;
);

SERIALIZE_MESSAGE(ControlSetVfbRenderRegion,
	PARAM(x)
	PARAM(y)
	PARAM(width)
	PARAM(height)
	PARAM(imgWidth)
	PARAM(imgHeight)
	PARAM(enabled)
);

/// MsgControlOnStartViewportRender
EMPTY_PROTO_MESSAGE(ControlOnStartViewportRender);
SERIALIZE_EMPTY_MESSAGE(ControlOnStartViewportRender);

/// MsgControlOnStartProductionRender
EMPTY_PROTO_MESSAGE(ControlOnStartProductionRender);
SERIALIZE_EMPTY_MESSAGE(ControlOnStartProductionRender);

/// MsgControlOnUpdateVfbSettings
PROTO_MESSAGE(ControlOnUpdateVfbSettings,
	std::string vfbSettings;
);

SERIALIZE_MESSAGE(ControlOnUpdateVfbSettings,
	PARAM(vfbSettings)
);


/// MsgControlOnLogMessage
PROTO_MESSAGE(ControlOnLogMessage,
	int logLevel;
	std::string logMessage;
);

SERIALIZE_MESSAGE(ControlOnLogMessage,
	PARAM(logLevel)
	PARAM(logMessage)
);


/// MsgControlOnRenderStage
/// Goes on the control connection, not the renderer one: a blocking renderer call (startSync()
/// during GPU kernel compilation) holds the renderer connection's thread, which also flushes its sends.
PROTO_MESSAGE(ControlOnRenderStage,
	std::string stage;   // e.g. "Compiling kernels"
	int elements;        // Work units done
	int totalElements;   // Total work units, 0 if the stage reports none
);

SERIALIZE_MESSAGE(ControlOnRenderStage,
	PARAM(stage)
	PARAM(elements)
	PARAM(totalElements)
);


//ControlRequestComputeDevices
EMPTY_PROTO_MESSAGE(ControlGetComputeDevices);

SERIALIZE_EMPTY_MESSAGE(ControlGetComputeDevices);

PROTO_MESSAGE(ControlOnGetComputeDevices,
	ComputeDeviceType deviceType;
	vray::AttrListString computeDevices;
	vray::AttrListInt defaultDeviceStates;
);

SERIALIZE_MESSAGE(ControlOnGetComputeDevices,
	PARAM(deviceType)
	PARAM(computeDevices)
	PARAM(defaultDeviceStates)
);


PROTO_MESSAGE(ControlSetComputeDevices,
	vray::AttrListInt computeDeviceIndices;
	ComputeDeviceType deviceType;
);

SERIALIZE_MESSAGE(ControlSetComputeDevices,
	PARAM(computeDeviceIndices)
	PARAM(deviceType)
);

PROTO_MESSAGE(ControlSetUpdateAvailable,
	bool hasUpdate;
);

SERIALIZE_MESSAGE(ControlSetUpdateAvailable,
	PARAM(hasUpdate)
);

PROTO_MESSAGE(ControlLogVfbMessage,
	VfbMessageLevel level;
	std::string message;
);

SERIALIZE_MESSAGE(ControlLogVfbMessage,
	PARAM(level)
	PARAM(message)
);

PROTO_MESSAGE(ControlOnAutoUpdateCheckChanged,
	bool autoCheck;
);

SERIALIZE_MESSAGE(ControlOnAutoUpdateCheckChanged,
	PARAM(autoCheck)
);


EMPTY_PROTO_MESSAGE(ControlOnAppUpdateRequested);

SERIALIZE_EMPTY_MESSAGE(ControlOnAppUpdateRequested);


/// MsgControlOnSwitchLicenseToCommunity - fired when the user clicks the
/// "Switch to Community Edition" button in the no-license dialog. Tells
/// Blender to flip the community_edition preference and restart the server.
EMPTY_PROTO_MESSAGE(ControlOnSwitchLicenseToCommunity);

SERIALIZE_EMPTY_MESSAGE(ControlOnSwitchLicenseToCommunity);


/// Data for a single light mix change, mirrors VRay::LightMixChange
/// with plugin name instead of VRay::Plugin reference.
struct LightMixChangeData {
	std::string pluginName;
	float colorR = 1.f;
	float colorG = 1.f;
	float colorB = 1.f;
	float intensityMult = 1.f;
	bool enabled = true;
};

/// MsgControlOnLightMixTransferToScene
PROTO_MESSAGE(ControlOnLightMixTransferToScene,
	std::vector<LightMixChangeData> changes;
);

static SerializerStream& operator&& (SerializerStream& s, const MsgControlOnLightMixTransferToScene& msg) {
	s << static_cast<int>(msg.changes.size());
	for (const auto& c : msg.changes) {
		s << c.pluginName << c.colorR << c.colorG << c.colorB << c.intensityMult << c.enabled;
	}
	return s;
}

static DeserializerStream& operator&& (DeserializerStream& s, MsgControlOnLightMixTransferToScene& msg) {
	int count;
	s >> count;
	msg.changes.resize(count);
	for (auto& c : msg.changes) {
		s >> c.pluginName >> c.colorR >> c.colorG >> c.colorB >> c.intensityMult >> c.enabled;
	}
	return s;
}


/// VFB context menu action modes
enum class VFBMenuMode : int {
	SelectObject = 1,
	SelectMaterial = 2,
	SetFocusPoint = 3
};

/// MsgControlOnVFBMenu - sent when user selects a VFB context menu action
PROTO_MESSAGE(ControlOnVFBMenu,
	int mode;
	std::string targetName;      // Node plugin name (for object selection) or material plugin name
	std::string objectName;      // Node plugin name of the object owning the material (material selection only)
	double distance;
);

SERIALIZE_MESSAGE(ControlOnVFBMenu,
	PARAM(mode)
	PARAM(targetName)
	PARAM(objectName)
	PARAM(distance)
);


/// MsgControlOnAddRenderElementToScene
PROTO_MESSAGE(ControlOnAddRenderElementToScene,
	int renderElementType;
);

SERIALIZE_MESSAGE(ControlOnAddRenderElementToScene,
	PARAM(renderElementType)
);


/// MsgControlOnVFBShowMessagesWindow
EMPTY_PROTO_MESSAGE(ControlOnVFBShowMessagesWindow);
SERIALIZE_EMPTY_MESSAGE(ControlOnVFBShowMessagesWindow);


/// MsgControlOnVFBRenderRegionChanged - sent when the VFB render region changes
PROTO_MESSAGE(ControlOnVFBRenderRegionChanged,
	int  x;
	int  y;
	int  width;
	int  height;
	bool enabled;
);

SERIALIZE_MESSAGE(ControlOnVFBRenderRegionChanged,
	PARAM(x)
	PARAM(y)
	PARAM(width)
	PARAM(height)
	PARAM(enabled)
);


/// MsgControlSetVisualDebugger
PROTO_MESSAGE(ControlSetVisualDebugger,
	bool enable;
);

SERIALIZE_MESSAGE(ControlSetVisualDebugger,
	PARAM(enable)
);

};  // end VrayZmqWrapper namespace

#pragma warning (pop)


