#!/bin/bash
# Install aven into the task container.
#
# From the repository rather than from PyPI: what is being measured is the code
# in front of you, and a published version would be whatever was released last.
#
# Three things here are not boilerplate, and each of them is a real failure
# this script already hit on a real task:
#
# **No `set -e`.** The runner `source`s this file into the shell the agent then
# works in, so errexit set here outlives the install and kills that shell on the
# first command that returns non-zero - including the agent's own, which is how
# a finished task reports a failing test. The install reports itself through its
# last command instead, which is what the runner checks for.
#
# **uv does not go in $HOME/.local/bin.** Some tasks mount /root as a tmpfs, and
# Docker's tmpfs defaults include noexec, so the binary installs fine and then
# cannot be executed: `bash: /root/.local/bin/uv: Permission denied`, on a file
# that is right there and marked executable. Everything uv unpacks and runs goes
# under /opt for the same reason.
#
# **A venv, not --system.** The Ubuntu task images mark the system interpreter
# externally-managed (PEP 668), and `uv pip install --system` refuses with
# "hint: Virtual environments were not considered due to the `--system` flag".
#
# And the quiet one: this script ends where it started. The runner sources it
# and then types the agent's command into the same shell, so a `cd` left behind
# here becomes the agent's working directory - which it was, pointing at aven's
# own source tree instead of the task.

started_in=$PWD

apt-get update -qq
apt-get install -y -qq git curl ca-certificates

export UV_INSTALL_DIR=/usr/local/bin
export UV_CACHE_DIR=/opt/uv/cache
export UV_PYTHON_INSTALL_DIR=/opt/uv/python
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="/usr/local/bin:$PATH"

git clone --depth 1 https://github.com/evanl666/aven /opt/aven

# --python 3.12 rather than whatever the image has: aven needs 3.11 or newer and
# the images range from 3.11 to 3.13. uv fetches one if the image has none.
uv venv --python 3.12 /opt/aven-venv
uv pip install --python /opt/aven-venv/bin/python /opt/aven

ln -sf /opt/aven-venv/bin/aven /opt/aven-venv/bin/aven-code /usr/local/bin/

cd "$started_in"

# Last, so its status is the script's status and the runner's `|| echo
# INSTALL_FAIL_STATUS` fires when any of the above did not take.
aven-code --help > /dev/null && echo "aven installed in $PWD"
