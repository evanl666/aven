#!/bin/bash
# Install aven into the task container.
#
# From the repository rather than from PyPI: what is being measured is the code
# in front of you, and a published version would be whatever was released last.
#
# Four things here are not boilerplate, and each is a real failure this script
# already hit on a real task:
#
# **No `set -e`.** The runner `source`s this file into the shell the agent then
# works in, so errexit set here outlives the install and kills that shell on the
# first command that returns non-zero - including the agent's own, which is how
# a finished task reports a failing test. The install reports itself through its
# last command instead, which is what the runner checks for.
#
# **The image's own Python first.** uv is a convenience, and reaching for it
# unconditionally meant every container downloaded it from astral.sh - slow
# thirty times over, and fatal on the one task that interferes with that host.
# What that looked like was not an error message: `curl -LsSf ... | sh` piped a
# captive-portal page into a shell and the install died with
#
#     sh: 1: DNS: not found
#     bash: uv: command not found
#
# having said nothing about why. git clone to GitHub worked fine in the same
# container, which is why "the network was broken" was the wrong diagnosis.
# So: use python3 when it is new enough, and fall back to uv only when it is
# not - and when the fallback is needed, download it to a file and check it
# before running it, so a failure says so.
#
# **uv does not go in $HOME/.local/bin**, when it is used at all. Some tasks
# mount /root as a tmpfs, and Docker's tmpfs defaults include noexec, so the
# binary installs fine and then cannot be executed: "Permission denied" on a
# file that is right there and marked executable.
#
# **This script ends where it started.** The runner sources it and then types
# the agent's command into the same shell, so a `cd` left behind here becomes
# the agent's working directory - which it was, pointing at aven's own source
# tree instead of the task.

started_in=$PWD

apt-get update -qq
apt-get install -y -qq git curl ca-certificates

git clone --depth 1 https://github.com/evanl666/aven /opt/aven

# aven needs 3.11 or newer. Ask the image before downloading anything.
usable=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' \
      > /dev/null 2>&1; then
    usable="$candidate"
    break
  fi
done

if [ -n "$usable" ]; then
  # A venv, not --system: the Ubuntu images mark the system interpreter
  # externally-managed (PEP 668) and refuse outright.
  "$usable" -m venv /opt/aven-venv \
    || (apt-get install -y -qq python3-venv && "$usable" -m venv /opt/aven-venv)
  /opt/aven-venv/bin/pip install -q --upgrade pip
  /opt/aven-venv/bin/pip install -q /opt/aven
else
  # No interpreter new enough, so fetch one. Downloaded to a file and checked,
  # never piped straight into a shell: see the header.
  export UV_INSTALL_DIR=/usr/local/bin
  export UV_CACHE_DIR=/opt/uv/cache
  export UV_PYTHON_INSTALL_DIR=/opt/uv/python
  if curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh \
      && head -1 /tmp/uv-install.sh | grep -q '^#!'; then
    sh /tmp/uv-install.sh
    export PATH="/usr/local/bin:$PATH"
    uv venv --python 3.12 /opt/aven-venv
    uv pip install --python /opt/aven-venv/bin/python /opt/aven
  else
    echo "no Python 3.11+ in this image and uv could not be fetched" >&2
  fi
fi

ln -sf /opt/aven-venv/bin/aven /opt/aven-venv/bin/aven-code /usr/local/bin/ 2>/dev/null

cd "$started_in"

# Last, so its status is the script's status and the runner's `|| echo
# INSTALL_FAIL_STATUS` fires when any of the above did not take.
aven-code --help > /dev/null && echo "aven installed in $PWD"
