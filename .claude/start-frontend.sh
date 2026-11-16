#!/bin/zsh
# 在獨立 worktree 啟動 ala-frontend session（第一次執行會建立 worktree）
export PATH="$HOME/.local/bin:$PATH"
REPO=${0:A:h:h}
WT=$REPO/.claude/worktrees/ala-frontend
[ -d $WT ] || git -C $REPO worktree add -b feat/hud-frontend $WT dev
cd $WT
[ -e specs ] || ln -s $REPO/specs specs
claude --agent ala-frontend -n ala-frontend --model sonnet --effort high "你是 ala-frontend。先讀你的 agent 規則與 specs/live-action-hud/tasks.md 的「給執行者的須知」，然後回報 team lead personal-workplace-d4 你已就緒，等待指派。"
