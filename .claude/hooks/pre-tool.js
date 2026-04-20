#!/usr/bin/env node
// pre-tool.js
// Claude Code hook: PreToolUse
// 危険コマンドのガード

'use strict';
const { readHookInput } = require('./hook-utils');

const hookInput = readHookInput();
const tool = hookInput.tool_name || '';

// Bash 以外はガード不要
if (tool !== 'Bash') process.exit(0);

const cmd = (hookInput.tool_input || {}).command || '';

// git force push: 警告（ブロックしない）
if (/git\s+push\s+(--force|-f)\b/.test(cmd)) {
  process.stderr.write('[PreToolUse WARNING] git force push を検出しました。実行前にユーザーに確認を取ってください。\n');
}

// rm -rf / または ~: ブロック
if (/rm\s+-rf\s+[/~]/.test(cmd)) {
  process.stderr.write(`[PreToolUse BLOCK] 危険なコマンドをブロックしました: ${cmd}\n`);
  process.exit(2);
}

// 本番DB破壊操作: 警告（ブロックしない）
if (/DROP\s+TABLE|DROP\s+DATABASE|TRUNCATE/i.test(cmd)) {
  process.stderr.write('[PreToolUse WARNING] 破壊的なDB操作を検出しました。本番環境での実行でないことを確認してください。\n');
}
