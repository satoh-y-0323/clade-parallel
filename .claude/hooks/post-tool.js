#!/usr/bin/env node
// post-tool.js
// Claude Code hook: PostToolUse
// Bash コマンドの実行結果を bash-log.jsonl に記録する

'use strict';
const fs   = require('fs');
const path = require('path');
const { readHookInput } = require('./hook-utils');

const hookInput = readHookInput();

// Bash 以外は記録しない
if ((hookInput.tool_name || '') !== 'Bash') process.exit(0);

const input        = hookInput.tool_input || {};
const toolResponse = hookInput.tool_response || {};
const now          = new Date();
const ts           = now.toISOString();
const session      = now.toISOString().slice(0, 10).replace(/-/g, '');

const cmd = (input.command || '').slice(0, 300);

// is_error の判定
const isError = toolResponse.is_error === true ||
               (typeof toolResponse.error === 'string' && toolResponse.error.length > 0);

// 出力テキストの取得（最大 800 文字、改行を↵に変換して1行に収める）
const responseStr = typeof toolResponse === 'string'
  ? toolResponse
  : (toolResponse.output || JSON.stringify(toolResponse));
const outPreview = responseStr.slice(0, 800).replace(/\r?\n/g, '↵');

const logDir  = path.join(process.cwd(), '.claude', 'instincts', 'raw');
const logFile = path.join(logDir, 'bash-log.jsonl');

try {
  if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });
  const record = { ts, session, cmd, out: outPreview, err: isError };
  fs.appendFileSync(logFile, JSON.stringify(record) + '\n', 'utf8');
} catch (e) {
  process.stderr.write('post-tool record error: ' + e.message + '\n');
}
