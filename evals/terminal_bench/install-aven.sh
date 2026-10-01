#!/bin/bash
# Install aven into the task container.
#
# From the repository rather than from PyPI: what is being measured is the code
# in front of you, and a published version would be whatever was released last.
set -e

apt-get update
apt-get install -y git curl

curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

git clone --depth 1 https://github.com/evanl666/aven /opt/aven
cd /opt/aven
uv pip install --system -e .

aven-code --help > /dev/null && echo "aven installed"
