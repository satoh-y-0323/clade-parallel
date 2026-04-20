#!/usr/bin/env node
/**
 * write-file.js
 * 任意パスへのファイル書き込みを行う共通スクリプト。
 * doc-writer など Write ツールが利用できないサブエージェントから Bash 経由で呼び出す。
 * write-report.js の書き込み処理もこのモジュールに委譲している。
 *
 * CLI 使用例:
 *
 *   # 新規書き込み（stdin）
 *   node .claude/hooks/write-file.js --path <targetPath> <<'CLADE_DOC_EOF'
 *   {内容}
 *   CLADE_DOC_EOF
 *
 *   # 新規書き込み（--file）
 *   node .claude/hooks/write-file.js --path <targetPath> --file <contentFile>
 *
 *   # 追記（stdin）
 *   node .claude/hooks/write-file.js --path <targetPath> --append <<'CLADE_DOC_EOF'
 *   {内容}
 *   CLADE_DOC_EOF
 *
 * 出力:
 *   実際に書き出したファイルパスを標準出力に表示する。
 *   例: [write-file] .claude/reports/doc-README-add.md
 */

'use strict';
const fs   = require('fs');
const path = require('path');

/**
 * stdin からコンテンツを読み込む
 * @returns {string}
 */
function readStdin() {
  return fs.readFileSync(0, 'utf-8');
}

/**
 * コンテンツを解決する（優先順位: --file > stdin）
 * @param {string[]} args - argv から解析済みの引数列
 * @returns {string}
 */
function resolveContent(args) {
  const fileIdx = args.indexOf('--file');
  if (fileIdx !== -1) {
    const filePath = args[fileIdx + 1];
    if (!filePath) {
      console.error('[write-file] --file オプションにファイルパスが必要です。');
      process.exit(1);
    }
    return fs.readFileSync(filePath, 'utf-8');
  }
  return readStdin();
}

/**
 * ファイルに書き込む（親ディレクトリを自動作成）
 * @param {string} filePath - 書き込み先の絶対または相対パス
 * @param {string} content  - 書き込む内容
 */
function writeToFile(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.writeFileSync(filePath, content, 'utf-8');
}

/**
 * ファイルに追記する（親ディレクトリを自動作成）
 * @param {string} filePath - 追記先の絶対または相対パス
 * @param {string} content  - 追記する内容
 */
function appendToFile(filePath, content) {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
  fs.appendFileSync(filePath, content, 'utf-8');
}

module.exports = { resolveContent, writeToFile, appendToFile };

// CLI として直接実行された場合
if (require.main === module) {
  const args = process.argv.slice(2);

  const pathIdx = args.indexOf('--path');
  if (pathIdx === -1 || !args[pathIdx + 1]) {
    console.error('[write-file] 使い方:');
    console.error('  新規: node write-file.js --path <targetPath> [--file <contentFile>]');
    console.error('  追記: node write-file.js --path <targetPath> --append [--file <contentFile>]');
    process.exit(1);
  }

  const targetPath = args[pathIdx + 1];
  const isAppend   = args.includes('--append');
  const content    = resolveContent(args);

  if (isAppend) {
    appendToFile(targetPath, content);
  } else {
    writeToFile(targetPath, content);
  }

  const relativePath = path.relative(process.cwd(), targetPath).replace(/\\/g, '/');
  console.log(`[write-file] ${relativePath}${isAppend ? ' (appended)' : ''}`);
}
