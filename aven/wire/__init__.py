"""aven as something another program drives.

`terminal/` is for a person at a keyboard. This is for a client: a window, a
phone, a script, another agent. Both are interfaces over the same harness and
neither knows about the other.

It sits below `terminal/` in the layering rather than beside it, because the
dependency only points one way - the CLI is what launches `--mode rpc`, and the
terminal's JSON sink reuses these same shapes so there is one definition of what
aven looks like from outside.
"""

from aven.wire.protocol import VERSION
from aven.wire.rpc import Conversation, serve_stdio

__all__ = ["VERSION", "Conversation", "serve_stdio"]
