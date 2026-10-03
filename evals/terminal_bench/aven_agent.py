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

The first run of this adapter is what found the bug in that flag. `--yes` used
to commit the tray *after* the loop finished, so during the run every shell
command still came back "staged, waiting for the user to approve" - and the
model, correctly, stopped and explained that it could not see any output. It
spent nine requests writing a plan. The flag now builds the tray unattended,
so the calls happen while their results can still be read.
"""

import os
import re
import shlex
from pathlib import Path

from terminal_bench.agents.installed_agents.abstract_installed_agent import (
    AbstractInstalledAgent,
)
from terminal_bench.terminal.models import TerminalCommand


# aven defaults to 12 turns, which is right for an errand and nowhere near a
# benchmark task. Three of the hard ones each ended at exactly twelve requests
# mid-sentence - "Found it, let me add the printk" and then nothing.
#
# Not unlimited: Terminal-Bench bounds each task by wall clock, so a runaway
# loop ends in a timeout rather than an invoice, but a number here is still
# what stops one task from spending the whole run's budget.
TURNS = 80


# The line aven ends on:
#
#   11 requests · in 65127 (cache read 53784 · wrote 11322) · out 2059
#
# Only the totals are taken. The cache split matters a great deal to what a run
# costs and there is nowhere in AgentResult to put it, so it stays in
# score.py, which reads the same line out of the log.
_USAGE = re.compile(r"requests\s+·\s+in\s+(\d+)\s+\(.*?\)\s+·\s+out\s+(\d+)")


def _usage_in(pane: str) -> tuple[int, int] | None:
    found = _USAGE.findall(pane.replace("\r", "\n"))
    return (int(found[-1][0]), int(found[-1][1])) if found else None


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
                    f"{shlex.quote(instruction)} --root . --yes "
                    f"--max-turns {TURNS}"
                ),
                min_timeout_sec=0.0,
                max_timeout_sec=float("inf"),
                block=True,
                append_enter=True,
            ),
        ]

    def perform_task(self, instruction, session, logging_dir=None):
        """Run the task, then put what it cost where everybody reads it.

        AbstractInstalledAgent returns AgentResult(0, 0) - hard-coded, for
        every installed agent there is, because it only ever sees the terminal
        and cannot know what the thing inside it spent. So results.json says
        zero tokens for aven, aider, goose and Claude Code alike, and the only
        way to compare two harnesses on cost is to write a log scraper per
        harness.

        aven prints the number itself, on its last line, so this one does not
        need scraping from outside: the pane is read here and the figure goes
        into the field every other tool already looks at.
        """
        result = super().perform_task(instruction, session, logging_dir)

        counted = _usage_in(session.capture_pane())
        if counted is None:
            return result

        went_in, came_out = counted
        return result.model_copy(
            update={"total_input_tokens": went_in, "total_output_tokens": came_out}
        )
