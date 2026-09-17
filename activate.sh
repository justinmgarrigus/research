#!/bin/bash

# Source this file in your ".bashrc" to add the "research" command to your
# environment.
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
GIT_ROOT=$(cd "$SCRIPT_DIR" && git rev-parse --show-toplevel)
if [ ! -d "$GIT_ROOT/.git" ]; then
    echo "Error: no .git found for research project!"
fi
alias research="uv --directory "$GIT_ROOT" run research"
