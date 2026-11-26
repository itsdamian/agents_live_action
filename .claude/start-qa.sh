#!/bin/zsh
# 在獨立 worktree 啟動 ala-qa session（第一次執行會建立 worktree）
export PATH="$HOME/.local/bin:$PATH"
REPO=${0:A:h:h}
WT=$REPO/.claude/worktrees/ala-qa
[ -d $WT ] || git -C $REPO worktree add --detach $WT dev
cd $WT
# specs/ 已在版控內；最新版以主目錄為準
claude --agent ala-qa -n ala-qa --model opus "你是 ala-qa。先讀你的 agent 規則與 specs/langgraph-pipeline/tasks.md，然後回報 team lead personal-workplace-d4 你已就緒，等待指派。"
