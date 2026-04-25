#!/usr/bin/env bash
# parallel-demo.sh
#
# Demonstrates manual parallel execution: opens two Windows Terminal windows
# simultaneously, each running claude -p to implement a JS file.
#
#   Window 1: implements addition.js
#   Window 2: implements multiplication.js
#
# Usage:
#   bash examples/scripts/parallel-demo.sh
#
# Output: examples/scripts/demo-output/
# Cleanup: rm -rf examples/scripts/demo-output

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
OUT="$SCRIPT_DIR/demo-output"
mkdir -p "$OUT"

# Seed empty stubs so Claude has a concrete target to write
printf '// TODO: implement\n' > "$OUT/addition.js"
printf '// TODO: implement\n' > "$OUT/multiplication.js"

# Write per-window task scripts (cd to git root so settings.local.json is found)
cat > "$OUT/_pane1.sh" << PANE
#!/usr/bin/env bash
echo "=== [Window 1] addition.js ==="
cd "$REPO_ROOT"
claude -p "Create examples/scripts/demo-output/addition.js. Accept two numeric command-line args a b. Validate both are numbers (print error to stderr and exit 1 if not). Print: a + b = result. Node.js, use strict."
echo ""
echo "=== Window 1 done. Press Enter to close ==="
read -r
PANE

cat > "$OUT/_pane2.sh" << PANE
#!/usr/bin/env bash
echo "=== [Window 2] multiplication.js ==="
cd "$REPO_ROOT"
claude -p "Create examples/scripts/demo-output/multiplication.js. Accept two numeric command-line args a b. Validate both are numbers (print error to stderr and exit 1 if not). Print: a * b = result. Node.js, use strict."
echo ""
echo "=== Window 2 done. Press Enter to close ==="
read -r
PANE

chmod +x "$OUT/_pane1.sh" "$OUT/_pane2.sh"

# Windows paths for native executables
PANE1_WIN="$(cygpath -w "$OUT/_pane1.sh")"
PANE2_WIN="$(cygpath -w "$OUT/_pane2.sh")"
# Full path to bash: wt new-window opens a fresh env without Git Bash in PATH
BASH_WIN="$(cygpath -w "$(which bash)")"

# PowerShell launcher: open each task in its own console window.
# Start-Process on a console app opens a new visible window by default.
# Avoid wt entirely — PowerShell 5.1 wraps wt's subcommand args into one
# quoted string, which wt then misinterprets as a profile name.
cat > "$OUT/_launch.ps1" << 'PS'
param($Pane1, $Pane2, $Bash)
Start-Process $Bash -ArgumentList $Pane1
Start-Process $Bash -ArgumentList $Pane2
PS

LAUNCHER_WIN="$(cygpath -w "$OUT/_launch.ps1")"

echo "Output directory : $OUT"
echo "bash path        : $BASH_WIN"
echo "Launching two parallel Windows Terminal windows..."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$LAUNCHER_WIN" \
  -Pane1 "$PANE1_WIN" -Pane2 "$PANE2_WIN" -Bash "$BASH_WIN"
