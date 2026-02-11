// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#include "osl.h"

#include <filesystem>

#include <OSL/oslconfig.h>
#include <OSL/oslcomp.h>
#include <OSL/oslexec.h>


namespace VRayForBlender::Interop
{


////////////////////////// PyOSLManager /////////////////////////////////

bool OSLManager::compile(const std::string & inputFile, const std::string & outputFile)
{
	OIIO_NAMESPACE_USING
		std::vector<std::string> options;
	std::string stdosl_path = stdOSLPath;

	options.push_back("-o");
	options.push_back(outputFile);

	OSL::OSLCompiler compiler(&OSL::ErrorHandler::default_handler());
	return compiler.compile(string_view(inputFile), options, string_view(stdosl_path));
}


std::string OSLManager::compileToBuffer(const std::string& code)
{
	OIIO_NAMESPACE_USING
		std::vector<std::string> options;
	std::string stdosl_path = stdOSLPath;

	OSL::OSLCompiler compiler(&OSL::ErrorHandler::default_handler());
	std::string buffer;
	compiler.compile_buffer(code, buffer, {}, stdOSLPath);
	return buffer;
}


bool OSLManager::queryFromFile(const std::string& file, OSL::OSLQuery& query)
{
	return query.open(file, "");
}


bool OSLManager::queryFromBytecode(const std::string& code, OSL::OSLQuery& query)
{
	return query.open_bytecode(code);
}


OSLManager& OSLManager::getInstance()
{
	static OSLManager mgr;
	return mgr;
}


////////////////////////// PyOSLParam ///////////////////////////////////

void PyOSLParam::setFloatListSocValFromParam(const OSL::OSLQuery::Parameter* param)
{
	boost::python::list list;
	std::vector<float> defaultFloat3 = { 0.0f, 0.0f, 0.0f };

	if (param->validdefault) {
		defaultFloat3[0] = param->fdefault[0];
		defaultFloat3[1] = param->fdefault[1];
		defaultFloat3[2] = param->fdefault[2];
	}

	for (auto& val : defaultFloat3) {
		list.append(val);
	}

	// the data is not sliced here only members as append and len
	socketDefaultValue = list;
}

void PyOSLParam::setFloatSockValFromParam(const OSL::OSLQuery::Parameter* param)
{
	float defaultFloat = 0.0f;
	socketType = boost::python::object("VRaySocketFloat");

	if (param->validdefault)
		defaultFloat = param->fdefault[0];

	socketDefaultValue = boost::python::object(defaultFloat);
}

void PyOSLParam::setIntSockValFromParam(const OSL::OSLQuery::Parameter* param)
{
	int defaultInt = 0;
	socketType = boost::python::object("VRaySocketInt");

	if (param->validdefault)
		defaultInt = param->idefault[0];

	socketDefaultValue = boost::python::object(defaultInt);
}

void PyOSLParam::setStringSockValFromParam(const OSL::OSLQuery::Parameter* param)
{
	// TODO: strings are only for plugin inputs
	std::string defaultString = "";
	socketType = boost::python::object("VRaySocketPlugin");
	if (param->validdefault) {
		defaultString = param->sdefault[0].string();
	}
	socketDefaultValue = boost::python::object(defaultString);
}


bool PyOSLParam::init(const OSL::OSLQuery::Parameter* param)
{
	OIIO_NAMESPACE_USING
		if (!param && (param->varlenarray || param->isstruct || param->type.arraylen > 1)) {
			/* skip unsupported types or null parameters */
			return false;
		}
	name = boost::python::object(param->name.string());
	isOutputSocket = boost::python::object(param->isoutput);

	if (param->type.vecsemantics == TypeDesc::POINT ||
		param->type.vecsemantics == TypeDesc::VECTOR ||
		param->type.vecsemantics == TypeDesc::COLOR ||
		param->type.vecsemantics == TypeDesc::NORMAL) {

		if (param->type.vecsemantics == TypeDesc::COLOR) {
			socketType = boost::python::object("VRaySocketColor");
		}
		else {
			socketType = boost::python::object("VRaySocketVector");
		}

		setFloatListSocValFromParam(param);
	}
	else if (param->type.aggregate == TypeDesc::SCALAR) {
		if (param->type.basetype == TypeDesc::INT) {

			setIntSockValFromParam(param);
		}
		else if (param->type.basetype == TypeDesc::FLOAT) {

			setFloatSockValFromParam(param);
		}
		else if (param->type.basetype == TypeDesc::STRING) {

			setStringSockValFromParam(param);
		}
	}
	return true;
}

bool getOslQuery(OSL::OSLQuery& query, const std::string& script)
{
	auto& oslManager = OSLManager::getInstance();

	std::filesystem::path osoPath = script;
	osoPath.replace_extension(".oso");
	std::string strOsoPath = osoPath.string();

	return oslManager.compile(script, strOsoPath) &&
		oslManager.queryFromFile(strOsoPath, query);
}


} // end namespace VRayForBlender::Interop