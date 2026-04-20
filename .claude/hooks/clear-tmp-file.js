#!/usr/bin/env node
/**
 * clear-tmp-file.js
 * .claude/tmp/ 配下の指定ファイルを削除する共通スクリプト。
 *
 * レポート出力フロー（Step 1: Write → Step 2: write-report.js --file）において、
 * Step 1 の前に既存 tmp ファイルを削除しておくことで、
 * Claude Code の Write ツールによる「既存ファイル上書き確認プロンプト」の発動を防ぐ。
 *
 * CLI 使用例:
 *
 *   node .claude/hooks/clear-tmp-file.js --path .claude/tmp/requirements-report.md
 *
 * セキュリティガード:
 *   - --path で指定できるのは .claude/tmp/ 配下のみ
 *   - 絶対パス・上位ディレクトリ参照（..）は拒否する
 *   - それ以外のパスが指定された場合は exit code 1 で終了
 *
 * 出力:
 *   存在した場合: [clear-tmp-file] .claude/tmp/<file> (removed)
 *   存在しなかった場合: [clear-tmp-file] .claude/tmp/<file> (not exist)
 */

'use strict';
const fs   = require('fs');
const path = require('path');

const TMP_PREFIX = '.claude/tmp/';

const args = process.argv.slice(2);
const pathIdx = args.indexOf('--path');

if (pathIdx === -1 || !args[pathIdx + 1]) {
  console.error('[clear-tmp-file] 使い方:');
  console.error('  node clear-tmp-file.js --path .claude/tmp/<filename>');
  process.exit(1);
}

const rawPath = args[pathIdx + 1];

// パスの正規化（Windows のバックスラッシュを / に統一）
const normalizedPath = rawPath.replace(/\\/g, '/');

// セキュリティガード: .claude/tmp/ 配下限定
if (!normalizedPath.startsWith(TMP_PREFIX)) {
  console.error(`[clear-tmp-file] エラー: .claude/tmp/ 配下のファイルのみ削除できます（指定: ${rawPath}）`);
  process.exit(1);
}

// セキュリティガード: 上位ディレクトリ参照を拒否
if (normalizedPath.includes('..')) {
  console.error(`[clear-tmp-file] エラー: パスに .. を含めることはできません（指定: ${rawPath}）`);
  process.exit(1);
}

// セキュリティガード: 絶対パス拒否（Windows 形式 C:/... / Unix 形式 /... 両方）
if (path.isAbsolute(normalizedPath) || /^[A-Za-z]:[\\/]/.test(normalizedPath)) {
  console.error(`[clear-tmp-file] エラー: 絶対パスは使用できません（指定: ${rawPath}）`);
  process.exit(1);
}

const targetPath = path.resolve(process.cwd(), normalizedPath);
const relativePath = path.relative(process.cwd(), targetPath).replace(/\\/g, '/');

if (fs.existsSync(targetPath)) {
  fs.unlinkSync(targetPath);
  console.log(`[clear-tmp-file] ${relativePath} (removed)`);
} else {
  console.log(`[clear-tmp-file] ${relativePath} (not exist)`);
}
