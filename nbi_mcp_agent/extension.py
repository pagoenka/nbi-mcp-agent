"""MCP Extension for Jupyter Notebook Intelligence."""

import asyncio
import json
import logging
import os
import uuid
from typing import Any, List, Optional
from mcp import ClientSession
from nbi_mcp_agent.mcp_server import Configuration, Server, ToolWrapper
from notebook_intelligence import (
    ChatCommand, MarkdownData, NotebookIntelligenceExtension, Host, 
    ChatParticipant, ChatRequest, ChatResponse
)
from fuzzy_json import loads as fuzzy_json_loads
from mcp.types import TextResourceContents
from mcp import ClientSession
from mcp.client.stdio import stdio_client
import nest_asyncio


logging = logging.getLogger(__name__)

class MCPClient:
    def __init__(self):
        self.session: Optional[ClientSession] = None
        self.server_config = {}
        self.server_tool_dict = {}
        self._cleanup_lock = asyncio.Lock()
        self.servers = []

    async def initialize_servers(self):
            
        if self.server_config.get("mcpServers"):
            self.servers = [
                Server(name, srv_config)
                for name, srv_config in self.server_config["mcpServers"].items()
            ]

        for server in self.servers:
            try:
                await server.initialize()
            except Exception as e:
                logging.error(f"Failed to initialize server: {e}")

    async def all_tools(self) -> list[Any]:
        """List available tools from the server."""
        all_tools : list[ToolWrapper] = []
        for server in self.servers:
            tools = await server.list_tools()
            self.server_tool_dict[server] = tools
            all_tools.extend(tools)
        return all_tools
    
    def convert_all_tools_to_schema(self, tools: List[ToolWrapper]):
        tools_schema = []
        for tool in tools:
            tool_schema = tool.convert_tool_to_schema()
            tools_schema.append(tool_schema)
        return tools_schema
    
    def get_server_by_tool(self, tool: ToolWrapper) -> Server:
        for server, tools in self.server_tool_dict.items():
            if tool in tools:
                return server
        return None


    # async def cleanup_servers(self) -> None:
    #     """Clean up all servers properly."""
    #     try:
    #         cleanup_tasks = [asyncio.create_task(server.cleanup()) for server in self.servers]

    #         if cleanup_tasks:
    #             try:
    #                 await asyncio.gather(*cleanup_tasks, return_exceptions=True)
    #             except Exception as e:
    #                 logging.warning(f"Warning during final cleanup: {e}")
    #             except asyncio.CancelledError as e:
    #                 logging.warning(f"Cleanup cancelled by: {e}")
    #     except Exception as e:
    #         logging.error(f"Error during final cleanup: {e}")
    #         logging.error(f"Stack trace:", exc_info=True)

    async def cleanup_servers(self) -> None:
        """Clean up all servers properly."""
        for server in reversed(self.servers):
            try:
                await server.cleanup()
            except Exception as e:
                logging.warning(f"Warning during final cleanup: {e}")    


class MCPChatParticipant(ChatParticipant):
    """Chat participant that implements the Model Context Protocol."""

    def __init__(self, host: Host):
        super().__init__()
        self.host = host
        self.client = None
        self.updation_in_progress = False
        self.initialize_client()

    def initialize_client(self):
        self.client = MCPClient()
        self.tools_list: list[ToolWrapper] = []
        self.tools_schema_list = []


    def cleanup_server_tools(self):
        self.client.servers = []
        self.tools_list: list[ToolWrapper] = []
        self.tools_schema_list = []
        self.client.server_tool_dict = {}


    def is_client_initialized(self) -> bool:
        return self.client is not None
    
    @property
    def id(self) -> str:
        return "mcp-agent"

    @property
    def name(self) -> str:
        return "MCP Agent"
    
    @property
    def description(self) -> str:
        return "MCP Agent for Jupyter notebooks"

    @property
    def icon_path(self) -> str:
        return ""

    @property
    def commands(self) -> List[ChatCommand]:
        return [
            ChatCommand(name='help', description='Show help'),
            ChatCommand(name='getMCPConfig', description='Get MCP Config'),
            ChatCommand(name='updateMCPConfig', description='Update MCP Config')
        ]
    
    def _get_tool_schema_by_name(self, name: str) -> dict:
        for tool in self.tools_schema_list:
            if tool["function"]["name"] == name:
                return tool
        return None
    
    def _get_tool_by_name(self, name: str) -> dict:
        for tool in self.tools_list:
            if tool.name == name:
                return tool
        return None
    

    async def handle_chat_request(self, request: ChatRequest, response: ChatResponse, options: dict = {}) -> None:
        try:
            if self.updation_in_progress:

                self.updation_in_progress = False
                new_config_path = request.chat_history[-1].get('content', '')
                new_config_path = new_config_path.replace('@mcp-agent ', '', 1)
                new_config_path = new_config_path.strip()

                response.stream(MarkdownData(f"Updating MCP config to: {new_config_path}"))

                if os.path.isfile(new_config_path) and new_config_path.endswith('.json'):
                    self.client.server_config = Configuration.load_config(new_config_path)
                    response.stream(MarkdownData(f"Updated MCP config to: {self.client.server_config}"))
                    
                else:
                    response.stream(MarkdownData("Invalid file path or not a JSON file. Please provide a valid JSON file path."))
                    response.stream(MarkdownData("""If you want to update the MCP config, call below command again then provide config file path.\n
                                                \n```text\n@mcp-agent updateMCPConfig"\n```\n
                                                """))
                response.finish()
                return

            if request.command == 'help':
                response.stream(MarkdownData("""I am an AI agent. I can help you with some tasks. Here are some example prompts you can try:\n
                \n```text\n@mcp-agent prompt"\n```\n
                \n```text\n@mcp-agent getMCPConfig"\n```\n
                \n```text\n@mcp-agent updateMCPConfig"\n```\n
                """))
                response.stream(MarkdownData(f"Available tools: {self.tools_list}"))
                response.finish()
                return
            
            if request.command == 'getMCPConfig':
                response.stream(MarkdownData("""Listing the MCP server config:\n
                """))
                response.stream(MarkdownData(f"{self.client.server_config}"))
                response.finish()
                return
            
            if request.command == 'updateMCPConfig':
                self.updation_in_progress = True
                response.stream(MarkdownData(f"Provide Absolute path to the new MCP config file:"))
                response.finish()
                return

            if self.client.server_config.get("mcpServers"):
                await self.handle_chat_request_with_mcp_tools(request, response, options)
            else:
                logging.error("MCP server config not found.")
                await self.host.default_chat_participant.handle_chat_request(request, response, options)
        
        except asyncio.CancelledError as e:
            logging.error(f"Error asyncio.CancelledError chat request: {e}")
        except GeneratorExit as e:
            logging.error(f"Error GeneratorExit chat request: {e}")
        except Exception as e:
            logging.error(f"Error chat request: {e}")    



    
    async def handle_chat_request_with_mcp_tools(self, request: ChatRequest, response: ChatResponse, options: dict = {}, tool_context: dict = {}, tool_choice = 'auto') -> None:
        try:

            await self.client.initialize_servers()
            self.tools_list = await self.client.all_tools()
            self.tools_schema_list = self.client.convert_all_tools_to_schema(self.tools_list)
            tools = self.tools_schema_list
            messages = request.chat_history.copy()

            if len(tools) == 0:
                request.host.chat_model.completions(messages, tools=None, cancel_token=request.cancel_token, response=response)
                return

            openai_tools = tools
            tool_call_rounds = []
            # TODO overrides options arg
            options = {'tool_choice': tool_choice}

            async def _tool_call_loop(tool_call_rounds: list):
                try:
                    tool_response = request.host.chat_model.completions(messages, openai_tools, cancel_token=request.cancel_token, options=options)
                    # after first call, set tool_choice to auto
                    options['tool_choice'] = 'auto'

                    if tool_response['choices'][0]['message'].get('tool_calls', None) is not None:
                        for tool_call in tool_response['choices'][0]['message']['tool_calls']:
                            tool_call_rounds.append(tool_call)
                    elif tool_response['choices'][0]['message'].get('content', None) is not None:
                        response.stream(MarkdownData(tool_response['choices'][0]['message']['content']))

                    messages.append(tool_response['choices'][0]['message'])

                    had_tool_call = len(tool_call_rounds) > 0

                    tool_name = None
                    # handle first tool calls
                    while len(tool_call_rounds) > 0:
                        tool_call = tool_call_rounds[0]
                        if "id" not in tool_call:
                            tool_call['id'] = uuid.uuid4().hex
                        tool_call_rounds = tool_call_rounds[1:]

                        tool_name = tool_call['function']['name']

                        tool_to_call = self._get_tool_schema_by_name(tool_name)

                        tool_temp = self._get_tool_by_name(tool_name) #TODO
                        

                        server = self.client.get_server_by_tool(tool_temp)

                        
                        if tool_to_call is None:
                            logging.error(f"Tool not found: {tool_name}, args: {tool_call['function']['arguments']}")
                            response.stream(MarkdownData("Oops! Failed to find requested tool. Please try again with a different prompt."))
                            response.finish()
                            return

                        if type(tool_call['function']['arguments']) is dict:
                            args = tool_call['function']['arguments']
                        elif not tool_call['function']['arguments'].startswith('{'):
                            args = tool_call['function']['arguments']
                        else:
                            args = fuzzy_json_loads(tool_call['function']['arguments'])

                        tool_properties = tool_to_call["function"]["parameters"]["properties"]

                        if type(args) is str:
                            if len(tool_properties) == 1 and tool_call['function']['arguments'] is not None:
                                tool_property = list(tool_properties.keys())[0]
                                args = {tool_property: args}
                            else:
                                args = {}

                        if len(tool_properties) != len(args):
                            response.stream(MarkdownData(f"Oops! There was a problem handling tool request. Please try again with a different prompt."))
                            response.finish()
                            return

                        response.stream(MarkdownData(f"Calling tool {tool_name} "))

                        logging.info(f"Attempting to call tool {tool_name} with args {args}")
                        
                        tool_call_response = await server.execute_tool(tool_name, args)

                        content_text = ""
                        for content_item in tool_call_response.content:
                            if content_item.type == "text":
                                content_text += content_item.text
                            elif content_item.type == "image":
                                content_text += f"[Image: {content_item.mimeType}]"
                            elif content_item.type == "resource":
                                if isinstance(content_item.resource, TextResourceContents):
                                    content_text += content_item.resource.text
                                else:
                                    content_text += f"[Binary Resource: {content_item.resource.mimeType}]"

                        function_call_result_message = {
                            "role": "tool",
                            "content": content_text,
                            "tool_call_id": tool_call['id']
                        }

                        messages.append(function_call_result_message)

                    if had_tool_call:
                        await _tool_call_loop(tool_call_rounds)
                        return

                    if len(tool_call_rounds) > 0:
                        await _tool_call_loop(tool_call_rounds)
                        return
                    
                    logging.debug("Tool call round completed")
                except Exception as e:
                    # error_msg = f"Error calling tool {tool_name}: {str(e)}"
                    error_msg = f"Error calling tool: {str(e)}"
                    logging.error(error_msg)
                    logging.error(f"Stack trace:", exc_info=True)
                    response.stream(MarkdownData(error_msg))
                    response.finish()


            await _tool_call_loop(tool_call_rounds)

        except asyncio.CancelledError as e:
            logging.error(f"Chat request cancelled by: {e}")
        except GeneratorExit as e:
            logging.error(f"Error GeneratorExit chat request: {e}")    
        except Exception as e:
            logging.error(f"Error handling chat request: {e}")
            logging.error(f"Stack trace:", exc_info=True)
            response.stream(MarkdownData(f"Error handling chat request: {e}"))
            response.finish()
        finally :
            await self.client.cleanup_servers()
            self.cleanup_server_tools()
            response.finish()
            return
        


class MCPExtension(NotebookIntelligenceExtension):
    """MCP Extension for Jupyter Notebook Intelligence."""
    
    @property
    def id(self) -> str:
        return "mcp-extension"

    @property
    def name(self) -> str:
        return "MCP Extension"

    @property
    def provider(self) -> str:
        return "Notebook Intelligence"

    @property
    def url(self) -> str:
        return "https://github.com/notebook-intelligence/nbi-mcp-agent"

    def activate(self, host: Host) -> None:
        """Activate the MCP extension."""
        self.participant = MCPChatParticipant(host)
        host.register_chat_participant(self.participant)
        logging.info("MCP extension activated")