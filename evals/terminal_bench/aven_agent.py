"""aven, as something Terminal-Bench can run.

Terminal-Bench gives an agent a container and a sentence, lets it work, and
then runs a set of pytest assertions it never saw. The adapter's whole job is
to install aven in that container and hand it the sentence.

## Two things here are not boilerplate

**`aven-code`, not `aven`.** The assistant has file tools and no shell; these
tasks are shell tasks. `aven-code` is the one with run_command, grep and glob.

**`--yes`.** Nobody is in the container to approve anything, so staged work
would sit there and every task would fail with the work described but not
done. That flag turns off the thing aven is actually for, and the honest
reading of any score from this run is: *this measures whether the harness gets
in the way, not whether its safety model is any good.* Nothing here exercises
staging, approval, the sandbox or undo, and a benchmark that rewards an agent
for `rm -rf` and a confident summary would score that agent well.
"""

import os
import shlex
from pathlib import Path

from terminal_bench.agents.installed_agents.abstract_installed_agent import (
    AbstractInstalledAgent,
)
from terminal_bench.terminal.models import TerminalCommand


class AvenAgent(AbstractInstalledAgent):
    @staticmethod
    def name() -> str:
        return "aven"

    def __init__(self, model_name: str | None = None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._model_name = model_name

    @property
    def _env(self) -> dict[str, str]:
        env = {"ANTHROPIC_API_KEY": os.environ["ANTHROPIC_API_KEY"]}
        # aven reads AVEN_MODEL, and the runner passes --model as model_name.
        if self._model_name:
            env["AVEN_MODEL"] = self._model_name.removeprefix("anthropic/")
        elif "AVEN_MODEL" in os.environ:
            env["AVEN_MODEL"] = os.environ["AVEN_MODEL"]
        return env

    @property
    def _install_agent_script_path(self) -> Path:
        return Path(__file__).parent / "install-aven.sh"

    def _run_agent_commands(self, instruction: str) -> list[TerminalCommand]:
        return [
            TerminalCommand(
                # --root . because the container's working directory is the task,
                # and aven refuses every path outside the roots it was given.
                # -p so the answer goes to stdout and the process exits, rather
                # than opening the full-screen app in a terminal nobody is at.
                command=(
                    "aven-code -p "
                    f"{shlex.quote(instruction)} --root . --yes"
                ),
                min_timeout_sec=0.0,
                max_timeout_sec=float("inf"),
                block=True,
                append_enter=True,
            ),
        ]
