"""Servers worth knowing about, and the config to reach them.

A starting point, not a store. Every entry here was checked against the
registry that publishes it before being written down, and each says who
publishes it - because the interesting question about an MCP server is not what
it does but whose computer it is.

    official     published by the people who make the thing it talks to
    community    published by somebody else

That distinction is the whole reason this file is short. A search for "google
mcp" returns a dozen packages by a dozen strangers, and adding one here would
be this project telling somebody to hand a stranger their Google account. The
answer for Google is aven's own connector, which uses an OAuth client the
person registers themselves and talks to Google directly - see `google` in
`connectors.toml`.

Nothing here is installed, enabled or contacted. `aven --connectors` prints it,
and the person pastes what they want.

**The risks below are suggestions and are marked as such.** The safe default
applies to anything not named: staged, waiting for a decision. What is written
here is only a starting judgement about tools whose names are stable, and it is
the person's file once it is pasted.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Known:
    name: str
    about: str
    who: str  # "official" or "community"
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    needs: str = ""  # what the person has to supply
    read_only: list[str] = field(default_factory=list)


CATALOGUE: list[Known] = [
    Known(
        name="files",
        about="Read and write files under a folder you name.",
        who="official",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/path/to/a/folder"],
        needs="the folder to allow, in place of /path/to/a/folder",
        read_only=["read_text_file", "read_media_file", "list_directory",
                   "directory_tree", "search_files", "get_file_info"],
    ),
    Known(
        name="fetch",
        about="Fetch a web page and turn it into text the model can read.",
        who="official",
        command="uvx",
        args=["mcp-server-fetch"],
        needs="uv installed (brew install uv)",
        read_only=["fetch"],
    ),
    Known(
        name="git",
        about="Read a git repository: log, diff, show, status.",
        who="official",
        command="uvx",
        args=["mcp-server-git", "--repository", "/path/to/a/repo"],
        needs="uv installed, and the repository path",
        read_only=["git_status", "git_diff", "git_diff_unstaged", "git_diff_staged",
                   "git_log", "git_show"],
    ),
    Known(
        name="time",
        about="The current time anywhere, and conversions between zones.",
        who="official",
        command="uvx",
        args=["mcp-server-time"],
        needs="uv installed",
        read_only=["get_current_time", "convert_time"],
    ),
    Known(
        name="notes",
        about="A knowledge graph the model can write to and read back later.",
        who="official",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-memory"],
        read_only=["read_graph", "search_nodes", "open_nodes"],
    ),
    Known(
        name="thinking",
        about="A scratchpad for working a hard problem through in steps.",
        who="official",
        command="npx",
        args=["-y", "@modelcontextprotocol/server-sequential-thinking"],
        read_only=["sequentialthinking"],
    ),
    Known(
        name="browser",
        about="Drive a real browser: open pages, click, fill forms, screenshot.",
        who="official",
        command="npx",
        args=["-y", "@playwright/mcp@latest"],
        needs="a browser it can drive; it will offer to install one",
    ),
    Known(
        name="notion",
        about="Read and write Notion pages and databases.",
        who="official",
        command="npx",
        args=["-y", "@notionhq/notion-mcp-server"],
        needs="a Notion integration token, as NOTION_TOKEN in [mcp.notion.env]",
    ),
    Known(
        name="github",
        about="Issues, pull requests and code search on GitHub.",
        who="official",
        url="https://api.githubcopilot.com/mcp/",
        needs=(
            "a GitHub token with the scopes you want, as "
            'Authorization = "Bearer ghp_..." in [mcp.github.headers]'
        ),
    ),
]


def as_toml(known: Known) -> str:
    """One entry, as the lines to paste into connectors.toml."""
    lines = [f"[mcp.{known.name}]"]
    if known.url:
        lines.append(f'url = "{known.url}"')
    else:
        lines.append(f'command = "{known.command}"')
        listed = ", ".join(f'"{a}"' for a in known.args)
        lines.append(f"args = [{listed}]")
    lines.append(f'about = "{known.about}"')

    if known.read_only:
        lines.append("")
        lines.append(f"# Suggested, and yours to change. Anything not named here")
        lines.append(f"# waits for your approval, which is the safe default.")
        lines.append(f"[mcp.{known.name}.risk]")
        for tool in known.read_only:
            lines.append(f'{tool} = "read"')
    return "\n".join(lines)


def printable(width: int = 78) -> str:
    """The whole catalogue, for `aven --connectors`."""
    out: list[str] = [
        "Servers you can connect. Nothing here is installed or contacted until",
        "you paste it into ~/.aven/connectors.toml and connect it.",
        "",
        "'official' means published by whoever makes the thing it talks to.",
        "A community server is somebody else's code, holding your credentials.",
        "",
    ]
    for known in CATALOGUE:
        out.append("-" * width)
        out.append(f"{known.name}   [{known.who}]   {known.about}")
        if known.needs:
            out.append(f"needs: {known.needs}")
        out.append("")
        out.append(as_toml(known))
        out.append("")
    out.append("-" * width)
    out.append(
        "For Google, aven has its own connector rather than a third-party "
        "server:\nit uses an OAuth client you register yourself and talks to "
        "Google directly.\nSee the Connecting a service section of the README."
    )
    return "\n".join(out)
