
######################################################################################
# This package procides code for compiling and evaliating the conditional expressions
# used in plugin description JSON files.
######################################################################################

import types
import bpy

from vray_blender.lib.sys_utils import importFunction
from vray_blender.external.pyparsing import Forward, Word, alphanums, Suppress, Group, infixNotation, oneOf, opAssoc

from vray_blender import debug


def isCondition(cond):
    """ Return true if cond is a plugin description condition definition """
    return (type(cond) is dict) and ('cond' in cond)


def evaluateCondition(propGroup, node: bpy.types.Node, cond):
    """ Evaluates whether a condition set for an attribute is fulfilled.

        Conditions can be applied to any attribute of a widget. This function only
        evaluates the condition to True or False. The semantics of the condition
        itself (i.e. what will happen if it is true) are implemented by the caller.
    """

    if not 'evaluate' in cond:
        # NOTE: 'cond' may contain quote chars so don't use an f-string to construct the message  
        errMsg = "Condition has not been compiled [" + str(cond) + f"] in {propGroup.bl_rna.name}."
        raise Exception(errMsg)

    try:
        rule = cond['cond']
        evaluator = cond['evaluate']
        
        if type(rule) is str:
            return evaluator(propGroup, node)
        
        assert type(rule) is dict

        # Evaluate the rules from top to botton. Return the first match.
        for value, subEvaluator in cond['evaluate'].items():
            if subEvaluator(propGroup, node):
                return value
    
        errMsg = "Multi-rule condition did not evaluate to any rule: [" + str(cond) + f"] in {propGroup.bl_rna.name}."
    
    except Exception as ex:
        errMsg = "Failed to evaluate condition: [" + str(cond) + f"] in {propGroup.bl_rna.name}.\nError: {ex}"
        
    raise Exception(errMsg)


###################################################################
## UI condition compiler
###################################################################
class UIConditionCompiler:

    # Dictionary of hash => compiled function.
    # Functions having the same rule string can be reused between plugin modules.
    _compiledFunctions = {}

    def __init__(self, pluginDesc: dict):
        self.pluginDesc = pluginDesc
        self.translator = UIConditionConverter(pluginDesc)

    def generateEvaluators(self):
        """ Generates evaluation functions for the conditions in all widgets of the plugin. """

        for widget in self.pluginDesc['Widget']['widgets']:
            self._generateWidgetConditionEvaluators(widget)
        
        for sockDesc in self.pluginDesc.get('Node', {}).get('input_sockets', []):
            self._generateSocketConditionEvaluators(sockDesc)


    def _compileConditionForProperty(self, rule: str, condLocator: str, allowCustomFunctions= True):
        """ Return the compiled evaluation function for the condition 

        Args:
            rule (str):        A string representation of the condition logic
            condLocator (str): Arbitrary string idendifying the condition, only used in error messages  

        Returns:
            The compiled function object or None on error.
        """
        ruleHash = hash(rule)

        if (evalFn := __class__._compiledFunctions.get(ruleHash)) is not None:
            return evalFn
        
        if not rule.startswith('::'):
            # This is a fully qualified function name
            if not allowCustomFunctions:
                debug.printError(f"Custom functions not supported as conditions in socket descriptions: {condLocator}") 
            else:
                evalFn = importFunction(rule)
        else:
            try:
                funcCode = self.translator.toPython(rule)
                compiledMethod = compile(funcCode, '<string>', 'exec')
                evalFn = types.FunctionType(compiledMethod.co_consts[1], globals(), "evaluate")
            except Exception as ex:
                debug.printError(f"Failed to compile condition for {condLocator} => {rule}, exception: {ex}")
                
                
        if evalFn is not None:
            __class__._compiledFunctions[ruleHash] = evalFn
        
        return evalFn


    def _compileCondition(self, cond: dict, condLocator: str, allowCustomFunctions = True):
        """ Compile a condition and save the compiled evaluator(s) in its 'evaluate' property
           
            Arguments:
            condLocator(str) -  An arbitrary string identifying the condition which will be printed 
                                in error messages
        """
        assert isCondition(cond), "Invalid condition definition, should contain a 'cond' field"
        
        rule = cond['cond']

        if type(rule) is str:
            if compiledFn := self._compileConditionForProperty(rule, condLocator, allowCustomFunctions):
                cond['evaluate'] = compiledFn
        else:
            assert type(rule) is dict
            cond['evaluate'] = {}
            
            for value, subRule in rule.items():
                if compiledFn := self._compileConditionForProperty(subRule, condLocator, allowCustomFunctions):
                    cond['evaluate'][value] = compiledFn

        assert 'evaluate' in cond, f"Condition compilation failed: {condLocator}"


    def _generateWidgetConditionEvaluators(self, widget: dict, locator: str = ''):
        """ Compiles an evaluation function for each condition set on a widget's attribute.
            The compiled function is stored in attribute's 'evaluate' field and can be 
            invoked later with a plugin data object as its parameter. 
        """
        
        if not locator:
            locator = self.pluginDesc['ID']

        attrsWithConditions = [attrName for attrName in widget if isCondition(widget[attrName])]
        
        for attrName in attrsWithConditions:
            cond = widget[attrName]
            self._compileCondition(cond, f"{locator}::{attrName}")

        # Compile conditions for widget's attributes
        for attr in widget.get('attrs', []):
            # Some widgets do not have a 'name' property, replace with '_'
            condLocator = f"{locator}::{attr.get('name', '_')}"
            self._generateWidgetConditionEvaluators(attr, condLocator)
            

    def _generateSocketConditionEvaluators(self, sockDesc):
        """ Compiles an evaluation function for each condition set on a socket property.
            The compiled function is stored in condition's 'evaluate' field and can be 
            invoked later with a plugin data object as its parameter. 
        """

        label = sockDesc.get('label')
        visible = sockDesc.get('visible')

        hasLabelConditions = (label is not None) and isCondition(label)
        hasVisibleCondition = (visible is not None) and isCondition(visible)

        locator = f"{self.pluginDesc['ID']}::{sockDesc['name']}"
        
        if hasLabelConditions:
            self._compileCondition(label, f"{locator}::label")

        if hasVisibleCondition:
            # Visibility conditions require an update function to be registered for each
            # property that is part of the condition. We cannot easily parse that from 
            # custoom functions, so we disallow them in this case.
            self._compileCondition(visible, f"{locator}::visible", allowCustomFunctions=False)



###################################################################
## UI condition parser
###################################################################

class UIConditionGrammar:     
    """ Grammar for parsing UI condition expressions """

    identifier = Word("::" + alphanums + "_")
    equalityOperator = oneOf("!= = < > <= >=")
    value = Word(alphanums)
    comparisonExpr = identifier + equalityOperator + value

    LPAR,RPAR = map(Suppress, '()')     # Don't generate tokens for the parentheses
    expr = Forward()                    # Enable recursive patterns
    operand = comparisonExpr
    factor = operand | Group(LPAR + expr + RPAR)

    parser = infixNotation(operand,
            [
                (oneOf(','), 2, opAssoc.LEFT),
                (oneOf(';'), 2, opAssoc.LEFT),
            ])
    
    @staticmethod
    def parse(s: str):
        return UIConditionGrammar.parser.parseString(s)
    

class UIConditionConverter:
    """ Parser for translation of V-Ray UI condition format to Python expression """

    def __init__(self, pluginDesc: dict):
        self.pluginDesc = pluginDesc

    def toPython(self, cond: str):
        syntaxTree = UIConditionGrammar.parse(cond).as_list()
        
        if len(syntaxTree) == 1:
            # If there are no nested conditions (no parentheses), the list will have just one element 
            # which itself is the list with the expression's tokens.
            syntaxTree = syntaxTree[0]

        expr = self._parseCompoundExpression(iter(syntaxTree))
        paramNames = [p[len("param_"):] for p in expr.split(" ") if p.startswith('param_')]
        
        funcCode = "def evaluate(plugin, node=None):\n"
        tab = "  "

        # Generate accessor function code for each parameter in the conditional expression. Currently, this
        # is not optimal because conditions for different parameters won't share the accessor function, but will
        # compile their own. 
        for p in paramNames:
            paramCode = ( ""
                f"{tab}result = None\n"
                f"{tab}if node:\n"
                f"{tab}{tab}sock = next((s for s in node.inputs if hasattr(s, 'vray_attr') and (s.vray_attr=='{p}')), None)\n"
                f"{tab}{tab}if sock:\n"
                f"{tab}{tab}{tab}result = sock.value\n"
                f"{tab}if not result:\n"
                f"{tab}{tab}result = plugin.{p}\n"      
                f"{tab}param_{p} = result\n"      
            )

            funcCode += paramCode

        funcCode += f"{tab}return {expr}"
        return funcCode


    @staticmethod
    def getActiveProperties(cond):
        """ Parse the names of all properties which a are part of the condition definition.
         
            Arguments:
            cond - the condition as defined in the plugin description JSON
         """
        import re
        delimiters = re.compile(r";|,|=|!|<|>|\(|\)")

        fnGetProps = lambda e: [p[2:] for p in delimiters.split(e) if p.startswith('::')]
        
        active = []
        rule = cond['cond']

        if type(rule) is dict:
            active = sum([fnGetProps(e) for e in rule.values()], [])
        else:
            active = fnGetProps(rule)

        return list(set(active))
    

    def _buildSimpleExpression(self, propNameToken: str, op: str, value: str):
        # The property name is designated in the expression by prepending '::' to it. Get the original name. 
        propName = propNameToken[2:]
        prop = next((p for p in self.pluginDesc['Parameters'] if p['attr'] == propName))

        if not prop:
            raise Exception(f"Error while parsing condition: no such property {self.pluginDesc['ID']}::{propName}")
        
        # All tokens are strings, we need to convert to the actual type for proper evaluation.
        if prop['type'] in ('INT'):
            value = int(value)
        if prop['type'] in ('FLOAT', 'FLOAT_TEXTURE'):
            value = float(value)
        elif prop['type'] == 'BOOL':
            value = bool(int(value))
        else:
            value = f'"{value}"' if value != 'NONE' else None

        if op == "=":
            op = "=="

        # 'plugin' is the name of the variable with the plugin property data.
        return f"param_{propName} {op} {value}"
            

    def _buildLogicExpression(self, lhs: bool, op, rhs: bool):
        match op:
            case ",":
                return f"{lhs} and {rhs}"
            case ";":
                return f"{lhs} or {rhs}"
            

    def _parseSimpleExpression(self, it):
        """ Parse an expression of type 'parameter op value' """
        lhs = next(it)
        if type(lhs) is list:
            # NOTE IMPORTANT: The spaces around the parentheses are essential. They will be used to 
            # split the resulting string into tokens when replacing the parameter names.
            return f"( {self._parseCompoundExpression(iter(lhs))} )"
        
        operator = next(it)
        value = next(it)
        
        return self._buildSimpleExpression(lhs, operator, value)


    def _parseCompoundExpression(self, it):
        """ Parse a compound expression of type 'condition and/or condition' with possible
            nesting using parentheses.
        """
        lhs = None

        while it:
            if lhs is None:
                lhs = self._parseSimpleExpression(it)

            logicOp = next(it, None)
            if not logicOp:
                # No more data
                return lhs

            rhs = self._parseSimpleExpression(it)
            lhs = self._buildLogicExpression(lhs, logicOp, rhs)

        return lhs