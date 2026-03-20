#!/bin/bash
# Double-click to open the Kalshi bot dashboard in your browser.

cd "$(dirname "$0")"

# Source shell profiles so PATH includes uv / python
[ -f "$HOME/.zshrc" ]   && source "$HOME/.zshrc"   2>/dev/null
[ -f "$HOME/.bashrc" ]  && source "$HOME/.bashrc"  2>/dev/null
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "========================================"
echo "  Kalshi Bot — Dashboard               "
echo "  Opening at http://localhost:8501     "
echo "========================================"
echo ""

.venv/bin/python -m streamlit run dashboard.py --server.headless false

echo ""
read -n1 -r -p "Press any key to close..."
