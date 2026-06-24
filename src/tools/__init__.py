"""Investment assistant tool registry."""

from src.tools.definitions import TOOL_DEFINITIONS
from src.tools.dispatcher import dispatch_tool

__all__ = ["TOOL_DEFINITIONS", "dispatch_tool"]
