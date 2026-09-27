"""Foundation shared by every terminal app.

Rendering, the review prompt, the JSON and print sinks, the widgets and the
full-screen shell. All of it takes its content from the app, so none of it
decides what aven is for - cli_assistant and cli_code differ in their tools
and their prompt, not in how a tool result is drawn.
"""
