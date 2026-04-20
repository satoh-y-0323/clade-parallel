#!/usr/bin/env node
/**
 * write-report.js
 * タイムスタンプ付きレポートファイルを Windows ネイティブ環境で書き出す共通スクリプト。
 * tester / code-reviewer / security-reviewer から呼び出される。
 *
 * 【推奨】--file オプション: Write ツールで一時ファイルに保存してから渡す（特殊文字問題ゼロ）
 *
 *   # 新規出力（--file 推奨）
 *   node .claude/hooks/write-report.js <baseName> new --file /tmp/report.md
 *
 *   # 追記出力（--file 推奨）
 *   node .claude/hooks/write-report.js <baseName> append <targetFileName> --file /tmp/report.md
 *
 * ヒアドキュメント（stdin）でも渡せる（--file が使えない場合）
 *
 *   node .claude/hooks/write-report.js <baseName> new <<'CLADE_REPORT_EOF'
 *   {レポート内容の全て}
 *   CLADE_REPORT_EOF
 *
 *   node .claude/hooks/write-report.js <baseName> append <targetFileName> <<'CLADE_REPORT_EOF'
 *   {追記内容}
 *   CLADE_REPORT_EOF
 *
 * 出力:
 *   実際に書き出したファイルパスを標準出力に表示する。
 *   例: [write-report] .claude/reports/test-report-20260401-143022.md
 */

'use strict';
const fs   = require('fs');
const path = require('path');
const { resolveContent, writeToFile, appendToFile } = require('./write-file');

const [, , baseName, modeOrContent, ...rest] = process.argv;

if (!baseName || (modeOrContent !== 'new' && modeOrContent !== 'append')) {
  console.error('[write-report] 使い方:');
  console.error('  新規(--file): node write-report.js <baseName> new --file <path>');
  console.error('  追記(--file): node write-report.js <baseName> append <targetFile> --file <path>');
  console.error('  新規(stdin): node write-report.js <baseName> new <<\'CLADE_REPORT_EOF\'');
  console.error('  追記(stdin): node write-report.js <baseName> append <targetFile> <<\'CLADE_REPORT_EOF\'');
  process.exit(1);
}

const reportsDir = path.join(process.cwd(), '.claude', 'reports');
fs.mkdirSync(reportsDir, { recursive: true });

// タイムスタンプ生成（YYYYMMDD-HHmmss）
function generateTimestamp() {
  const now = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const date = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`;
  const time = `${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`;
  return `${date}-${time}`;
}

// 衝突しないファイルパスを生成
function resolveNewPath(baseNameArg, timestamp) {
  const base = path.join(reportsDir, `${baseNameArg}-${timestamp}.md`);
  if (!fs.existsSync(base)) return base;

  let branch = 2;
  while (true) {
    const candidate = path.join(reportsDir, `${baseNameArg}-${timestamp}-${branch}.md`);
    if (!fs.existsSync(candidate)) return candidate;
    branch++;
  }
}

// モード判定
const isNew    = modeOrContent === 'new';
const isAppend = modeOrContent === 'append';

if (isNew) {
  // 新規出力モード
  const content    = resolveContent(rest);
  const timestamp  = generateTimestamp();
  const outputPath = resolveNewPath(baseName, timestamp);

  writeToFile(outputPath, content);

  const relativePath = path.relative(process.cwd(), outputPath).replace(/\\/g, '/');
  console.log(`[write-report] ${relativePath}`);

} else if (isAppend) {
  // 追記出力モード: 先頭引数が追記先ファイル名、残りは --file またはインラインコンテンツ
  const targetFileName = rest[0];

  if (!targetFileName || targetFileName === '--file') {
    console.error('[write-report] append モードには追記先ファイル名が必要です。');
    console.error('  例: node write-report.js test-report append test-report-20260401-143022.md --file /tmp/report.md');
    process.exit(1);
  }

  const content    = resolveContent(rest.slice(1));
  const targetPath = path.join(reportsDir, targetFileName);

  if (!fs.existsSync(targetPath)) {
    console.error(`[write-report] 追記先ファイルが見つかりません: ${targetFileName}`);
    process.exit(1);
  }

  appendToFile(targetPath, content);

  const relativePath = path.relative(process.cwd(), targetPath).replace(/\\/g, '/');
  console.log(`[write-report] ${relativePath} (appended)`);

}
