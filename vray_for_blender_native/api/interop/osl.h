// SPDX-FileCopyrightText: Chaos Software EOOD
//
// SPDX-License-Identifier: GPL-3.0-or-later

#pragma once

#include<string>

#include <boost/python.hpp>
#include <OSL/oslquery.h>


namespace VRayForBlender::Interop
{

struct OSLManager {

	bool compile(const std::string& inputFile, const std::string& outputFile);
	std::string compileToBuffer(const std::string& code);

	// Query compiled .oso file for parameters
	bool queryFromFile(const std::string& file, OSL::OSLQuery& query);
	bool queryFromBytecode(const std::string& code, OSL::OSLQuery& query);

	static OSLManager& getInstance();

private:
	std::string stdOSLPath;
};

struct PyOSLParam {
	PyOSLParam() = default;
	bool init(const OSL::OSLQuery::Parameter* param);

	boost::python::object name; // OSLQuery::Parameter name
	boost::python::object socketType; // Socket type for this parameter
	boost::python::object socketDefaultValue; // default value
	boost::python::object isOutputSocket; // output or input

private:
	// extracting default socket value from parameter
	void setFloatListSocValFromParam(const OSL::OSLQuery::Parameter* param);
	void setFloatSockValFromParam(const OSL::OSLQuery::Parameter* param);
	void setIntSockValFromParam(const OSL::OSLQuery::Parameter* param);
	void setStringSockValFromParam(const OSL::OSLQuery::Parameter* param);
};


// getting OSL::OSLQuery from script path
bool getOslQuery(OSL::OSLQuery& query, const std::string& script);


} // end namespace VRayForBlender::Interop