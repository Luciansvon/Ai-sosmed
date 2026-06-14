#!/usr/bin/env bash
cd /home/bima_lucian/BIMA_CORE/Bot_thread || exit 1

echo "=== configure git credential helper via gh ==="
gh auth setup-git 2>&1 | tail -2

git init -q
git symbolic-ref HEAD refs/heads/main 2>/dev/null
git add .

# Safety gate: jangan pernah commit .env asli
if git ls-files --cached | grep -qE '(^|/)\.env$'; then
  echo "ABORT: .env ke-stage!"; exit 1
fi

echo "=== staged files ==="
git ls-files --cached | sort
echo "=== gitkeep present? ==="
git ls-files --cached | grep -q 'outputs/.gitkeep' && echo "GITKEEP_OK" || echo "NO_GITKEEP"

git commit -q -F - <<'MSG'
feat: standalone Threads autoposter bot (Ai-sosmed)

Extracted from the BIMA_CORE "Anisa" bot into a self-contained,
Discord-controlled Threads autoposter with full feature parity:
- Manual posting (!threads): trends, custom topics, optional image
- Auto-post scheduler (randomized daily slots) + AFK safe auto-publish
- Comment scan & auto-reply (anti-spam/toxic + safe auto-reply)
- Image generation (OpenRouter) + hosting (Catbox -> Discord CDN)
- Viral pattern learning (agentmemory, optional Obsidian)
- DM approval gate (approve / reject / reply-to-revise)

CrewAI and LangGraph coupling removed; personal handle/ID made
configurable via THREADS_USERNAME and BIMA_DISCORD_USER_ID env vars.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
MSG

echo "=== commit ==="
git log --oneline -1

git remote remove origin 2>/dev/null
git remote add origin https://github.com/Luciansvon/Ai-sosmed.git
echo "=== push ==="
git push -u origin main 2>&1 | tail -15
