"""aven, as something Harbor can run.

Harbor is the toolchain the Terminal-Bench leaderboard is measured with, and
the leaderboard's dataset is terminal-bench@4.0.0. The older adapter in
`evals/terminal_bench/` runs `terminal-bench-core==0.1.1`, the task set from
the benchmark's launch - useful for comparing aven against itself and against
another harness on identical tasks, and not comparable to any published number.

This one exists so there can be a number that is.

## What Harbor fixes for free

Three of the faults the 0.1.1 sweep found were artefacts of how that harness
installs an agent, and none of them can happen here.

**The install cannot leave the shell somewhere else.** `install` and `run` are
separate `exec` calls, so the working directory cannot leak from one to the
other. Under terminal-bench both ran in one tmux session and a `cd` in the
install script silently became the agent's root.

**`set -e` cannot leak either**, for the same reason.

**Cost is a first-class field.** `AgentContext` has n_input_tokens,
n_cache_tokens and n_output_tokens, and `populate_context_post_run` is where an
agent fills them in. Under terminal-bench every installed agent reports zero,
because AbstractInstalledAgent hard-codes it, so comparing two harnesses on
cost means scraping each one's logs.

## What is still true, and still the point

`--yes` turns off the thing aven is for. Staging, approval, the sandbox and
undo are all switched off for the duration, because nobody is in the container
to approve anything. A score from this measures whether the harness gets in the
way of a model trying to work. It measures nothing about whether the safety
model is any good, and an agent that ran `rm -rf` and wrote a confident summary
would score well.
"""

from __future__ import annotations

import re
import shlex
from typing import Any, override

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# aven defaults to 12 turns, which is right for an errand and nowhere near a
# benchmark task: three of the hard ones ended at exactly twelve requests
# mid-sentence. Bounded rather than unlimited because a number here is what
# stops one task spending the whole run's budget.
TURNS = 80

# Where the agent's own output is kept. /logs/agent is Harbor's directory for
# this and it comes back with the trial, which is the difference between a
# timed-out task you can diagnose and one that left only a banner.
TRANSCRIPT = "/logs/agent/aven.txt"

# The line aven ends on:
#
#   11 requests · in 65127 (cache read 53784 · wrote 11322) · out 2059
#
# `in` is every input token however it was billed, so the cache read is a
# subset of it rather than something to add on.
USAGE = re.compile(
    r"(\d+)\s+requests\s+·\s+in\s+(\d+)\s+"
    r"\(cache read\s+(\d+)\s+·\s+wrote\s+(\d+)\)\s+·\s+out\s+(\d+)"
)


class Aven(BaseInstalledAgent):
    """Installs aven from its repository and runs one prompt against the task."""

    MODEL_CONNECTION = ModelConnectionSpec(passthrough=True)

    @staticmethod
    @override
    def name() -> str:
        return "aven"

    @override
    def get_version_command(self) -> str | None:
        return "aven-code --help > /dev/null && echo installed"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        """Put aven in the container, from the repository rather than from PyPI.

        What is being measured is the code in front of you; a published version
        would be whatever was released last.

        Three choices here are scars from the 0.1.1 sweep:

        uv goes in /usr/local/bin, not $HOME/.local/bin. Some task images mount
        /root as a tmpfs and Docker's tmpfs defaults include noexec, so the
        binary installs and then will not execute - "Permission denied" on a
        file that is right there. Everything uv unpacks goes under /opt for the
        same reason.

        A venv, not --system: the Ubuntu images mark the system interpreter
        externally-managed and `uv pip install --system` refuses outright.

        --python 3.12 rather than whatever the image has, because aven needs
        3.11 or newer and the images range wider than that. uv fetches one when
        the image has none.
        """
        await self.ensure_system_dependencies(environment, ("curl",))

        await self.exec_as_root(
            environment,
            command=(
                "set -eu; "
                "export UV_INSTALL_DIR=/usr/local/bin "
                "UV_CACHE_DIR=/opt/uv/cache UV_PYTHON_INSTALL_DIR=/opt/uv/python; "
                "curl -LsSf https://astral.sh/uv/install.sh | sh; "
                "export PATH=/usr/local/bin:$PATH; "
                "(command -v git >/dev/null || "
                "  (apt-get update -qq && apt-get install -y -qq git)); "
                "git clone --depth 1 https://github.com/evanl666/aven /opt/aven; "
                "uv venv --python 3.12 /opt/aven-venv; "
                "uv pip install --python /opt/aven-venv/bin/python /opt/aven; "
                "ln -sf /opt/aven-venv/bin/aven /opt/aven-venv/bin/aven-code "
                "  /usr/local/bin/"
            ),
        )

    @override
    @with_prompt_template
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        access = self.model_connection
        if not access.api_key:
            raise ValueError("no API key for the model provider")

        # Harbor names models provider-first; aven wants the bare id.
        model = (self.model_name or "").split("/", 1)[-1]

        result = await self.exec_as_agent(
            environment,
            command=(
                "mkdir -p /logs/agent; "
                # --root . and no `cd`: Harbor runs this in the task's own
                # directory, and unlike the terminal-bench adapter there is no
                # install script in the same shell to have moved it.
                f"aven-code -p {shlex.quote(instruction)} "
                f"--root . --yes --max-turns {TURNS} "
                f"2>&1 | stdbuf -oL tee {TRANSCRIPT}"
            ),
            env={**access.env, "ANTHROPIC_API_KEY": access.api_key, "AVEN_MODEL": model},
        )
        self._said = _text_of(result)

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        """What it cost, from the line aven prints itself.

        Harbor asks the agent rather than guessing, which is the right way
        round: only aven knows how its input split between a cache read and a
        cache write, and that split is most of what a run costs.
        """
        counted = USAGE.search(getattr(self, "_said", "") or "")
        if counted is None:
            return

        _, went_in, read, _written, came_out = (int(n) for n in counted.groups())
        context.n_input_tokens = went_in
        context.n_cache_tokens = read
        context.n_output_tokens = came_out


def _text_of(result: Any) -> str:
    """Whatever the executor calls the output it captured.

    Asked of the object rather than assumed: `exec_as_agent` is typed `-> Any`
    and the shape differs between environment backends, so a missing attribute
    has to mean "no usage reported" and not a crash after the work was done.
    """
    for name in ("stdout", "output", "text"):
        found = getattr(result, name, None)
        if isinstance(found, str) and found:
            return found
    return result if isinstance(result, str) else ""
