#!/usr/bin/env bash
# parallel-demo-complex.sh
#
# Measures claude -p execution time for a task that requires reading
# existing files before writing — closer to real clade-parallel workloads.
#
#   Window 1: reads addition.js + multiplication.js, then implements subtraction.js
#   Window 2: reads addition.js + multiplication.js, then implements division.js
#
# Usage:
#   bash examples/scripts/parallel-demo-complex.sh
#
# Output: examples/scripts/demo-output/
# Cleanup: rm -rf examples/scripts/demo-output

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
OUT="$SCRIPT_DIR/demo-output"
mkdir -p "$OUT"

cat > "$OUT/_complex1.sh" << PANE
#!/usr/bin/env bash
echo "=== [Window 1] subtraction.js ==="
echo "Start: \$(date '+%H:%M:%S')"
START=\$(date +%s)
cd "$REPO_ROOT"
claude -p "Read examples/scripts/addition.js and examples/scripts/multiplication.js to understand the exact code style. Then create examples/scripts/demo-output/subtraction.js following the same style. It should accept two numeric args a b, validate both are numbers (exit 1 on error), and print: a - b = result."
END=\$(date +%s)
echo ""
echo "End  : \$(date '+%H:%M:%S')"
echo "Elapsed: \$((END - START)) sec"
echo "=== Window 1 done. Press Enter to close ==="
read -r
PANE

cat > "$OUT/_complex2.sh" << PANE
#!/usr/bin/env bash
echo "=== [Window 2] division.js ==="
echo "Start: \$(date '+%H:%M:%S')"
START=\$(date +%s)
cd "$REPO_ROOT"
claude -p "Read examples/scripts/addition.js and examples/scripts/multiplication.js to understand the exact code style. Then create examples/scripts/demo-output/division.js following the same style. It should accept two numeric args a b, validate both are numbers (exit 1 on error), handle division by zero (exit 1 on error), and print: a / b = result."
END=\$(date +%s)
echo ""
echo "End  : \$(date '+%H:%M:%S')"
echo "Elapsed: \$((END - START)) sec"
echo "=== Window 2 done. Press Enter to close ==="
read -r
PANE

chmod +x "$OUT/_complex1.sh" "$OUT/_complex2.sh"

PANE1_WIN="$(cygpath -w "$OUT/_complex1.sh")"
PANE2_WIN="$(cygpath -w "$OUT/_complex2.sh")"
BASH_WIN="$(cygpath -w "$(which bash)")"

cat > "$OUT/_launch_complex.ps1" << 'PS'
param($Pane1, $Pane2, $Bash)
Start-Process $Bash -ArgumentList $Pane1
Start-Process $Bash -ArgumentList $Pane2
PS

LAUNCHER_WIN="$(cygpath -w "$OUT/_launch_complex.ps1")"

echo "タスク内容:"
echo "  Window 1: addition.js + multiplication.js を Read → subtraction.js を実装"
echo "  Window 2: addition.js + multiplication.js を Read → division.js を実装"
echo ""
echo "Launching..."

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$LAUNCHER_WIN" \
  -Pane1 "$PANE1_WIN" -Pane2 "$PANE2_WIN" -Bash "$BASH_WIN"
