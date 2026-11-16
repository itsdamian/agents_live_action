#!/bin/zsh
# 在獨立 worktree 啟動 ala-developer session（第一次執行會建立 worktree 與 venv）
export PATH="$HOME/.local/bin:$PATH"
REPO=${0:A:h:h}
WT=$REPO/.claude/worktrees/ala-developer
[ -d $WT ] || git -C $REPO worktree add -b feat/langgraph-pipeline $WT dev
cd $WT
# specs/ 已在版控內；最新版以主目錄為準
[ -d backend/.venv ] || { python3.11 -m venv backend/.venv && backend/.venv/bin/python -m pip install -q -r backend/requirements.txt; }
claude --agent ala-developer -n ala-developer --model sonnet --effort high "你是 ala-developer。先讀你的 agent 規則與 specs/langgraph-pipeline/tasks.md 的「給執行者的須知」，然後回報 team lead personal-workplace-d4 你已就緒，等待指派。"
