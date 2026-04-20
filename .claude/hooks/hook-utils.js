#!/usr/bin/env node
/**
 * hook-utils.js
 * hooks/*.js スクリプト間で共有するユーティリティ関数群
 */

'use strict';
const fs = require('fs');

/**
 * stdin から Claude Code hook の入力 JSON を読み込んで返す。
 * パースに失敗した場合は空オブジェクトを返す。
 * @returns {object}
 */
function readHookInput() {
  try {
    return JSON.parse(fs.readFileSync(0, 'utf8'));
  } catch (_) {
    return {};
  }
}

/**
 * セッションファイルの雛形テキストを生成する。
 * @param {string} dateStr - YYYYMMDD 形式の日付文字列
 * @returns {string}
 */
function createSessionTemplate(dateStr) {
  return [
    `SESSION: ${dateStr}`,
    'AGENT: (未設定)',
    '',
    '## うまくいったアプローチ（証拠付き）',
    '（/end-session コマンドで記入してください）',
    '',
    '## 試みたが失敗したアプローチ',
    '（未記入）',
    '',
    '## まだ試していないアプローチ',
    '（未記入）',
    '',
    '## 残タスク',
    '（未記入）',
  ].join('\n');
}

/**
 * 事実オブジェクトから Markdown セクション文字列を生成する。
 * @param {{ recordedAt: string, bashCount: number, errCount: number, recentErrors: string[] }} factsObj
 * @returns {string}
 */
function buildFactsSection(factsObj) {
  const { recordedAt, bashCount, errCount, recentErrors } = factsObj;
  const lines = [
    '## 事実ログ（自動生成 / stop.js）',
    `- 記録時刻: ${recordedAt}`,
    `- Bash実行数: ${bashCount}`,
    `- エラー数: ${errCount}`,
    '- 直近のエラーコマンド:',
  ];
  if (recentErrors.length === 0) {
    lines.push('  - （なし）');
  } else {
    for (const cmd of recentErrors) {
      lines.push(`  - ${cmd}`);
    }
  }
  return lines.join('\n');
}

/**
 * tmpContent 内の「## 事実ログ（自動生成 / stop.js）」セクションを置換する。
 * 存在しない場合は末尾に追記する（冪等性を保証）。
 * @param {string} tmpContent - セッションファイルの既存テキスト
 * @param {string} factsSection - buildFactsSection で生成したセクション文字列
 * @returns {string}
 */
function upsertFactsSection(tmpContent, factsSection) {
  const HEADER = '## 事実ログ（自動生成 / stop.js）';
  const headerIndex = tmpContent.indexOf(HEADER);
  if (headerIndex === -1) {
    // 末尾に追記（JSON ブロックがあればその前に挿入）
    const jsonIdx = tmpContent.indexOf('\n' + SESSION_JSON_START);
    if (jsonIdx !== -1) {
      return tmpContent.slice(0, jsonIdx + 1) + factsSection + '\n\n' + tmpContent.slice(jsonIdx + 1);
    }
    const separator = tmpContent.endsWith('\n') ? '\n' : '\n\n';
    return tmpContent + separator + factsSection + '\n';
  }
  // 既存セクションの終端を検出: 次の `## ` ヘッダー、JSON ブロック、またはファイル末尾
  const nextSection = tmpContent.indexOf('\n## ', headerIndex + 1);
  const jsonBlock  = tmpContent.indexOf('\n' + SESSION_JSON_START, headerIndex + 1);

  let sectionEnd;
  if (nextSection === -1 && jsonBlock === -1) {
    // このセクションがファイル末尾まで続く
    return tmpContent.slice(0, headerIndex) + factsSection + '\n';
  } else if (nextSection === -1) {
    sectionEnd = jsonBlock;
  } else if (jsonBlock === -1) {
    sectionEnd = nextSection;
  } else {
    sectionEnd = Math.min(nextSection, jsonBlock);
  }
  return tmpContent.slice(0, headerIndex) + factsSection + '\n' + tmpContent.slice(sectionEnd + 1);
}

// ---------------------------------------------------------------------------
// セッション JSON ブロック
// ---------------------------------------------------------------------------

const SESSION_JSON_START = '<!-- CLADE:SESSION:JSON';
const SESSION_JSON_END   = '-->';

/**
 * セッションデータから CLADE:SESSION:JSON ブロック文字列を生成する。
 * @param {object} data
 * @returns {string}
 */
function buildSessionJsonBlock(data) {
  return SESSION_JSON_START + '\n' + JSON.stringify(data, null, 2) + '\n' + SESSION_JSON_END;
}

/**
 * .tmp ファイルの内容から CLADE:SESSION:JSON ブロックを抽出してパースする。
 * ブロックが存在しないか、パースに失敗した場合は null を返す。
 * @param {string} content - セッションファイルの内容
 * @returns {object|null}
 */
function parseSessionJsonBlock(content) {
  const startIdx = content.indexOf(SESSION_JSON_START);
  if (startIdx === -1) return null;

  const jsonStart = startIdx + SESSION_JSON_START.length;
  const endIdx = content.indexOf(SESSION_JSON_END, jsonStart);
  if (endIdx === -1) return null;

  const jsonStr = content.slice(jsonStart, endIdx).trim();
  try {
    return JSON.parse(jsonStr);
  } catch (_) {
    return null;
  }
}

/**
 * tmpContent 内の CLADE:SESSION:JSON ブロックを置換する。
 * 存在しない場合は末尾に追記する（冪等性を保証）。
 * @param {string} tmpContent - セッションファイルの既存テキスト
 * @param {object} data - 書き込む JSON データ
 * @returns {string}
 */
function upsertSessionJsonBlock(tmpContent, data) {
  const block = buildSessionJsonBlock(data);
  const startIdx = tmpContent.indexOf(SESSION_JSON_START);
  if (startIdx === -1) {
    const separator = tmpContent.endsWith('\n') ? '\n' : '\n\n';
    return tmpContent + separator + block + '\n';
  }
  const jsonStart = startIdx + SESSION_JSON_START.length;
  const endIdx = tmpContent.indexOf(SESSION_JSON_END, jsonStart);
  if (endIdx === -1) {
    // 壊れたブロックは先頭から切り捨てて末尾に再追記
    return tmpContent.slice(0, startIdx).trimEnd() + '\n\n' + block + '\n';
  }
  const blockEnd = endIdx + SESSION_JSON_END.length;
  const after = tmpContent.slice(blockEnd);
  return tmpContent.slice(0, startIdx) + block + (after.startsWith('\n') ? after : '\n' + after);
}

/**
 * 現在のプロセスが git worktree 内で動いているかどうかを返す。
 * --git-dir と --git-common-dir が異なる場合は worktree 内と判断する。
 * @returns {boolean}
 */
function isWorktree() {
  try {
    const { execSync } = require('child_process');
    const gitDir    = execSync('git rev-parse --git-dir',        { encoding: 'utf8' }).trim();
    const commonDir = execSync('git rev-parse --git-common-dir', { encoding: 'utf8' }).trim();
    return gitDir !== commonDir;
  } catch (_) {
    return false;
  }
}

/**
 * worktree から呼ばれた場合でもメインリポジトリのルートを返す。
 * git rev-parse --git-common-dir でメインの .git ディレクトリを特定する。
 * @returns {string}
 */
function getProjectRoot() {
  try {
    const { execSync } = require('child_process');
    const gitCommonDir = execSync('git rev-parse --git-common-dir', { encoding: 'utf8' }).trim();
    const absGitDir = path.isAbsolute(gitCommonDir)
      ? gitCommonDir
      : path.join(process.cwd(), gitCommonDir);
    return path.dirname(absGitDir);
  } catch (_) {
    return process.cwd();
  }
}

module.exports = {
  readHookInput,
  isWorktree,
  getProjectRoot,
  createSessionTemplate,
  buildFactsSection,
  upsertFactsSection,
  buildSessionJsonBlock,
  parseSessionJsonBlock,
  upsertSessionJsonBlock,
};
