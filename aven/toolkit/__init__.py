"""Tools more than one app wants.

A tool belongs here once a second app would reasonably reach for it. Until
then it lives with the app that needs it - `mac.py` sits in cli_assistant
because a coding agent has no use for Mail.
"""

from aven.toolkit.files import file_tools
from aven.toolkit.memory import memory_tools
from aven.toolkit.skills import skill_tools

__all__ = ["file_tools", "memory_tools", "skill_tools"]
