#!/usr/bin/env node
/**
 * record-approval.js
 * レポートに対するユーザーの承認/否認を approvals.jsonl に記録する。
 * tester / code-reviewer / security-reviewer などが承認確認後に呼び出す。
 *
 * 使い方:
 *   # 推奨（シェルインジェクション対策）: コメントを tmp ファイル経由で渡す
 *   node .claude/hooks/clear-tmp-file.js --path .claude/tmp/approval-comment.md
 *   # → Write ツールで .claude/tmp/approval-comment.md にコメントを書き込み、その後:
 *   node .claude/hooks/record-approval.js <reportFile> <yes|no> <reportType> --comment-file .claude/tmp/approval-comment.md
 *
 *   # レガシー: コメントを位置引数で受け取る（短い・定型コメントのみ・シェルメタ文字注意）
 *   node .claude/hooks/record-approval.js <reportFile> <yes|no> <reportType> "<コメント>"
 *
 * 例:
 *   node .claude/hooks/record-approval.js test-report-20260401-143022.md yes test --comment-file .claude/tmp/approval-comment.md
 */

'use strict';
const fs   = require('fs');
const path = require('path');

const args = process.argv.slice(2);

// --comment-file <path> オプションを検出・抽出
const commentFileIdx = args.indexOf('--comment-file');
let commentFromFile = null;
let positionalArgs = args;

if (commentFileIdx !== -1) {
  const commentFilePath = args[commentFileIdx + 1];
  if (!commentFilePath) {
    console.error('[record-approval] --comment-file オプションにファイルパスが必要です。');
    process.exit(1);
  }
  try {
    commentFromFile = fs.readFileSync(commentFilePath, 'utf-8').replace(/\r?\n$/, '');
  } catch (err) {
    console.error(`[record-approval] コメントファイルの読み込みに失敗: ${err.message}`);
    process.exit(1);
  }
  // --comment-file <path> の 2 トークンを positionalArgs から除去
  positionalArgs = args.filter((_, i) => i !== commentFileIdx && i !== commentFileIdx + 1);
}

const [reportFile, approvedArg, reportType, ...commentParts] = positionalArgs;

if (!reportFile || !approvedArg || !reportType) {
  console.error('[record-approval] 使い方:');
  console.error('  推奨: node record-approval.js <reportFile> <yes|no> <reportType> --comment-file <commentFile>');
  console.error('  レガシー: node record-approval.js <reportFile> <yes|no> <reportType> "<コメント>"');
  process.exit(1);
}

const approved = approvedArg.toLowerCase() === 'yes';
const comment  = commentFromFile !== null ? commentFromFile : (commentParts.join(' ') || '');

const reportsDir    = path.join(process.cwd(), '.claude', 'reports');
const approvalsFile = path.join(reportsDir, 'approvals.jsonl');

const record = {
  timestamp:   new Date().toISOString(),
  reportFile,
  type:        reportType,
  approved,
  comment,
};

fs.mkdirSync(reportsDir, { recursive: true });
fs.appendFileSync(approvalsFile, JSON.stringify(record) + '\n', 'utf-8');

const status = approved ? '✓ 承認' : '✗ 否認';
console.log(`[record-approval] ${status}: ${reportFile}`);
