#!/bin/bash
# Double-click this file to run the Kalshi trading bot in dry-run mode.

cd "$(dirname "$0")"

# Source shell profile so PATH includes uv, homebrew, etc.
[ -f "$HOME/.zshrc" ]   && source "$HOME/.zshrc"   2>/dev/null
[ -f "$HOME/.bashrc" ]  && source "$HOME/.bashrc"  2>/dev/null
[ -f "$HOME/.profile" ] && source "$HOME/.profile" 2>/dev/null
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

echo "========================================"
echo "  Kalshi Trading Bot  (DRY-RUN mode)  "
echo "========================================"
echo ""

# Install uv if missing
if ! command -v uv &>/dev/null; then
  echo "uv not found — installing now..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
fi

# Check that .env exists
if [ ! -f .env ]; then
  echo "WARNING: .env file not found."
  echo "Copy env_template.txt to .env and fill in your API keys."
  echo ""
fi

uv run python trading_bot.py "$@"

echo ""
echo "Bot finished. Press any key to close..."
read -n1 -r
