#!/usr/bin/env node
// pre-compact.js
// Claude Code hook: PreCompact
// コンテキスト圧縮前にセッションファイルへチェックポイントを記録

'use strict';
const fs   = require('fs');
const path = require('path');
const { createSessionTemplate, getProjectRoot, isWorktree } = require('./hook-utils');

if (isWorktree()) process.exit(0);

const cwd         = getProjectRoot();
const sessionDir  = path.join(cwd, '.claude', 'memory', 'sessions');
const now         = new Date();
const dateStr     = now.toISOString().slice(0, 10).replace(/-/g, '');
const sessionFile = path.join(sessionDir, `${dateStr}.tmp`);
const ts          = now.toISOString();

if (!fs.existsSync(sessionDir)) fs.mkdirSync(sessionDir, { recursive: true });

// セッションファイルが存在しない場合は雛形を作成
if (!fs.existsSync(sessionFile)) {
  fs.writeFileSync(sessionFile, createSessionTemplate(dateStr), 'utf8');
}

// PreCompact チェックポイントを追記
const checkpoint = [
  '',
  `## [PreCompact checkpoint: ${ts}]`,
  'コンテキストウィンドウ圧縮が発生しました。',
  'このポイント以前の詳細な文脈は失われています。',
].join('\n');

fs.appendFileSync(sessionFile, checkpoint + '\n', 'utf8');
process.stderr.write(`[PreCompact] セッション状態を ${sessionFile} に保存しました\n`);
