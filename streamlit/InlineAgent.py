
import boto3
import random
import json
import traceback

class InlineAgent:   
    code_interpreter_tool = {
        "actionGroupName": "UserInputAction",
        "parentActionGroupSignature": "AMAZON.CodeInterpreter"
    }
    
    # Initialise the inline agent
    def __init__(
        self,
        model_id="us.anthropic.claude-3-5-haiku-20241022-v1:0",
        agent_instruction="You are a helpful AI assistant and will be given a set of tools to help you perform your tasks.",
        action_groups=[],
        mcp_clients={},
        use_code_interpreter=False,
        session_id=None,
    ):
        self.model_id = model_id
        self.agent_instruction = agent_instruction
        self.use_code_interpreter = use_code_interpreter
        self.mcp_clients = mcp_clients
        
        self.client = boto3.client("bedrock-agent-runtime")
        
        # Use same session id for continuation of conversation (i.e. maintain history)
        if (session_id):
            self.session_id = session_id
        else:
            random_int = random.randint(1, 100000)
            self.session_id = f'custom-session-id-{random_int}'
        
        # Deduplicate action groups
        action_groups_dict = {
            action_group["actionGroupName"]: action_group for action_group in action_groups
        }
                
        # Add code interpreter to action group if enabled
        if use_code_interpreter:
            action_groups_dict = action_groups_dict | {
                self.code_interpreter_tool["actionGroupName"]: self.code_interpreter_tool
            }
        
        # Get list of tools from MCP Clients
        for name, mcp_client in mcp_clients.items():
            action_groups_dict = action_groups_dict | {
                name: mcp_client.list_tools()
            }
                                    
        self.action_groups = list(action_groups_dict.values())

        self.request_params = {
            "sessionId": self.session_id,
            "enableTrace": True,
            "instruction": agent_instruction,
            "foundationModel": model_id,
            "actionGroups": self.action_groups 
        }             
        
    # Invoke the inline agent
    def invoke(self, inputText, endSession=False):
        self.trace = []
        self.request_params["inputText"] = inputText
        self.request_params["endSession"] = endSession        
        response = self.client.invoke_inline_agent(**self.request_params)
        
        while True:
            event_stream = response["completion"]        
            for event in event_stream:
                
                try:
                    final_response_text = event.get("trace").get("trace").get("orchestrationTrace").get("observation").get("finalResponse").get("text")
                    if final_response_text:
                        self.add_trace(event)
                        return final_response_text, self.trace
                except Exception as e:
                    pass
                
                try:
                    if "returnControl" in event:
                        self.add_trace(event)
                        invocation_id = event.get("returnControl").get("invocationId")
                        function_invocation = event.get("returnControl").get("invocationInputs")[0].get("functionInvocationInput")
                                                
                        action_group = function_invocation.get("actionGroup")
                        tool_name = function_invocation.get("function")
                        parameters = {param.get("name") : param.get("value") for param in function_invocation.get("parameters") }
                        results = self.mcp_clients.get(action_group).call_tool(tool_name, parameters)
                                                
                        inline_session_state = {
                            "invocationId": invocation_id,
                            "returnControlInvocationResults": [
                                {
                                    "functionResult": {
                                        "actionGroup": action_group,
                                        "function": tool_name,                        
                                        "responseBody": {
                                            "TEXT": {
                                                "body" : results.content[0].text
                                            }
                                        }
                                    }
                                }
                            ]                  
                        }
                        
                        self.add_trace(inline_session_state)
                        
                        response = self.client.invoke_inline_agent(**self.request_params, inlineSessionState=inline_session_state)
                except Exception as e:
                    traceback.print_exc()
                    print(e)
                    pass
    
    # Add history traces to be returned 
    def add_trace(self,event):
        try:
            json.dumps(event, indent=4)
            self.trace.append(event)
        except Exception as e:
            pass