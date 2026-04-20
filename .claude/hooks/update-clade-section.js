'use strict';

/**
 * update-clade-section.js
 *
 * CLAUDE.md の "## User Rules" セクション（<!-- /cluster-promote によって自動追記される -->
 * マーカー以降、<!-- CLADE:START --> の直前）に @rules/NAME.md を冪等追記する。
 *
 * Usage:
 *   node .claude/hooks/update-clade-section.js add-rule NAME [--dry-run]
 *   node .claude/hooks/update-clade-section.js remove-rule NAME [--dry-run]
 *
 * Exit codes:
 *   0 - 正常終了 (追記済み or CLADEマーカーが見つからない場合も0)
 *   2 - no-op (既に同一エントリが存在するため追記不要 / 削除対象が存在しない)
 *   1 - エラー (ファイル読み書き失敗など)
 */

const fs = require('fs');
const path = require('path');

// ---------------------------------------------------------------------------
// 定数
// ---------------------------------------------------------------------------
const CLADE_START_MARKER = '<!-- CLADE:START -->';
const CLADE_END_MARKER = '<!-- CLADE:END -->';
const GLOBAL_RULES_HEADING = '## Global Rules (Clade 管理)';
const USER_RULES_MARKER = '<!-- /cluster-promote によって自動追記される -->';

// ---------------------------------------------------------------------------
// ユーティリティ
// ---------------------------------------------------------------------------

/**
 * stderr にログを出力する
 * @param {string} message
 */
function log(message) {
  process.stderr.write('[update-clade-section] ' + message + '\n');
}

/**
 * プロジェクトルートを返す（このスクリプトが .claude/hooks/ に置かれている前提）
 * @returns {string}
 */
function getProjectRoot() {
  return path.resolve(__dirname, '..', '..');
}

/**
 * CLAUDE.md のパスを返す
 * @returns {string}
 */
function getClaudeMdPath() {
  return path.join(getProjectRoot(), '.claude', 'CLAUDE.md');
}

// ---------------------------------------------------------------------------
// コア処理
// ---------------------------------------------------------------------------

/**
 * CLADE:START ~ CLADE:END 区間の開始・終了インデックスを検出する。
 * CRLF / LF 両対応。
 *
 * @param {string} content - ファイル全文
 * @returns {{ startIdx: number, endIdx: number } | null}
 *   startIdx: CLADE:START マーカー行の先頭インデックス
 *   endIdx:   CLADE:END マーカー行の末尾インデックス（\n含む）
 *   null:     マーカーが見つからない場合
 */
function findCladeSection(content) {
  const startIdx = content.indexOf(CLADE_START_MARKER);
  if (startIdx === -1) return null;

  const endIdx = content.indexOf(CLADE_END_MARKER, startIdx);
  if (endIdx === -1) return null;

  // CLADE:END マーカー行の末尾まで含める（改行文字も含む）
  const afterEnd = endIdx + CLADE_END_MARKER.length;
  const nextNewline = content.indexOf('\n', afterEnd);
  const sectionEnd = nextNewline === -1 ? content.length : nextNewline + 1;

  return { startIdx, sectionEnd, innerStart: startIdx + CLADE_START_MARKER.length, innerEnd: endIdx };
}

/**
 * User Rules セクション（<!-- /cluster-promote によって自動追記される --> 以降、
 * <!-- CLADE:START --> の直前）に @rules/NAME.md を冪等追記する。
 *
 * @param {string} content    - CLAUDE.md 全文
 * @param {string} ruleName   - 追記するルール名（拡張子なし）
 * @returns {{ newContent: string, alreadyExists: boolean, markerNotFound: boolean }}
 */
function addRuleToContent(content, ruleName) {
  const ruleEntry = '@rules/' + ruleName + '.md';

  // 既存エントリチェック（ファイル全体を対象）
  if (content.includes(ruleEntry)) {
    return { newContent: content, alreadyExists: true, markerNotFound: false };
  }

  // User Rules マーカーを探す
  const markerIdx = content.indexOf(USER_RULES_MARKER);
  if (markerIdx === -1) {
    return { newContent: content, alreadyExists: false, markerNotFound: true };
  }

  // CLADE:START マーカーの位置を探す（User Rules の終端）
  const cladeStartIdx = content.indexOf(CLADE_START_MARKER, markerIdx);

  // 挿入位置: CLADE:START の直前（空行を挟む）
  // CLADE:START より前の最後の @rules/ 行を見つけ、その直後に挿入
  const userRulesBlock = cladeStartIdx === -1
    ? content.slice(markerIdx)
    : content.slice(markerIdx, cladeStartIdx);

  // User Rules ブロック内の最後の @rules/ 行を探す
  const lines = userRulesBlock.split('\n');
  let lastRulesLineOffset = -1;
  let offset = 0;
  for (const line of lines) {
    if (line.startsWith('@rules/')) {
      lastRulesLineOffset = markerIdx + offset + line.length;
    }
    offset += line.length + 1; // +1 for '\n'
  }

  let insertPos;
  if (lastRulesLineOffset !== -1) {
    // 最後の @rules/ 行の末尾（改行の直後）に挿入
    insertPos = content.indexOf('\n', lastRulesLineOffset) + 1;
  } else {
    // @rules/ 行がない場合はマーカー行の直後に挿入
    insertPos = content.indexOf('\n', markerIdx) + 1;
  }

  const before = content.slice(0, insertPos);
  const after = content.slice(insertPos);
  const newContent = before + ruleEntry + '\n' + after;

  return { newContent, alreadyExists: false, markerNotFound: false };
}

/**
 * CLAUDE.md から @rules/NAME.md の行を削除する。
 *
 * @param {string} content    - CLAUDE.md 全文
 * @param {string} ruleName   - 削除するルール名（拡張子なし）
 * @returns {{ newContent: string, notFound: boolean }}
 */
function removeRuleFromContent(content, ruleName) {
  const ruleEntry = '@rules/' + ruleName + '.md';

  // エントリが存在するか確認
  if (!content.includes(ruleEntry)) {
    return { newContent: content, notFound: true };
  }

  // 該当行（改行含む）を削除する
  // CRLF と LF の両方に対応
  const newContent = content
    .replace(ruleEntry + '\r\n', '')
    .replace(ruleEntry + '\n', '');

  return { newContent, notFound: false };
}

// ---------------------------------------------------------------------------
// サブコマンド: add-rule
// ---------------------------------------------------------------------------

/**
 * add-rule サブコマンドのエントリポイント
 * @param {string[]} args - サブコマンド以降の引数
 */
function commandAddRule(args) {
  const dryRunIdx = args.indexOf('--dry-run');
  const isDryRun = dryRunIdx !== -1;

  // --dry-run フラグを除いた引数リスト
  const positional = args.filter((a, i) => i !== dryRunIdx);
  const ruleName = positional[0];

  if (!ruleName) {
    log('Error: rule name is required. Usage: add-rule NAME [--dry-run]');
    process.exit(1);
  }

  // ルール名のバリデーション（パストラバーサル防止）
  if (ruleName.includes('/') || ruleName.includes('\\') || ruleName.includes('..')) {
    log('Error: invalid rule name "' + ruleName + '"');
    process.exit(1);
  }

  const claudeMdPath = getClaudeMdPath();

  // ファイル読み込み
  let content;
  try {
    content = fs.readFileSync(claudeMdPath, 'utf8');
  } catch (err) {
    log('Error: failed to read ' + claudeMdPath + ': ' + err.message);
    process.exit(1);
  }

  // 追記処理
  const result = addRuleToContent(content, ruleName);

  if (result.markerNotFound) {
    log('CLADE markers not found in ' + claudeMdPath + '. No changes made.');
    process.exit(0);
  }

  if (result.alreadyExists) {
    log('@rules/' + ruleName + '.md already exists. No-op.');
    process.exit(2);
  }

  log('Adding @rules/' + ruleName + '.md to User Rules section in ' + claudeMdPath);

  if (isDryRun) {
    log('[dry-run] Would write the following content:');
    process.stderr.write('--- diff ---\n');
    // dry-run では変更後の内容を stderr に出力して終了
    const lines = result.newContent.split('\n');
    const originalLines = content.split('\n');
    for (let i = 0; i < Math.max(lines.length, originalLines.length); i++) {
      if (lines[i] !== originalLines[i]) {
        if (originalLines[i] !== undefined) process.stderr.write('- ' + originalLines[i] + '\n');
        if (lines[i] !== undefined) process.stderr.write('+ ' + lines[i] + '\n');
      }
    }
    process.stderr.write('--- end diff ---\n');
    process.exit(0);
  }

  // ファイル書き込み
  try {
    fs.writeFileSync(claudeMdPath, result.newContent, 'utf8');
    log('Successfully added @rules/' + ruleName + '.md to User Rules section.');
  } catch (err) {
    log('Error: failed to write ' + claudeMdPath + ': ' + err.message);
    process.exit(1);
  }

  process.exit(0);
}

// ---------------------------------------------------------------------------
// サブコマンド: remove-rule
// ---------------------------------------------------------------------------

/**
 * remove-rule サブコマンドのエントリポイント
 * @param {string[]} args - サブコマンド以降の引数
 */
function commandRemoveRule(args) {
  const dryRunIdx = args.indexOf('--dry-run');
  const isDryRun = dryRunIdx !== -1;

  // --dry-run フラグを除いた引数リスト
  const positional = args.filter((a, i) => i !== dryRunIdx);
  const ruleName = positional[0];

  if (!ruleName) {
    log('Error: rule name is required. Usage: remove-rule NAME [--dry-run]');
    process.exit(1);
  }

  // ルール名のバリデーション（パストラバーサル防止）
  if (ruleName.includes('/') || ruleName.includes('\\') || ruleName.includes('..')) {
    log('Error: invalid rule name "' + ruleName + '"');
    process.exit(1);
  }

  const claudeMdPath = getClaudeMdPath();

  // ファイル読み込み
  let content;
  try {
    content = fs.readFileSync(claudeMdPath, 'utf8');
  } catch (err) {
    log('Error: failed to read ' + claudeMdPath + ': ' + err.message);
    process.exit(1);
  }

  // 削除処理
  const result = removeRuleFromContent(content, ruleName);

  if (result.notFound) {
    log('@rules/' + ruleName + '.md not found. No-op.');
    process.exit(2);
  }

  log('Removing @rules/' + ruleName + '.md from ' + claudeMdPath);

  if (isDryRun) {
    log('[dry-run] Would write the following content:');
    process.stderr.write('--- diff ---\n');
    const lines = result.newContent.split('\n');
    const originalLines = content.split('\n');
    for (let i = 0; i < Math.max(lines.length, originalLines.length); i++) {
      if (lines[i] !== originalLines[i]) {
        if (originalLines[i] !== undefined) process.stderr.write('- ' + originalLines[i] + '\n');
        if (lines[i] !== undefined) process.stderr.write('+ ' + lines[i] + '\n');
      }
    }
    process.stderr.write('--- end diff ---\n');
    process.exit(0);
  }

  // ファイル書き込み
  try {
    fs.writeFileSync(claudeMdPath, result.newContent, 'utf8');
    log('Successfully removed @rules/' + ruleName + '.md.');
  } catch (err) {
    log('Error: failed to write ' + claudeMdPath + ': ' + err.message);
    process.exit(1);
  }

  process.exit(0);
}

// ---------------------------------------------------------------------------
// メインエントリポイント
// ---------------------------------------------------------------------------

function main() {
  const args = process.argv.slice(2);
  const subcommand = args[0];

  if (!subcommand) {
    log('Error: subcommand required. Available: add-rule, remove-rule');
    process.exit(1);
  }

  switch (subcommand) {
    case 'add-rule':
      commandAddRule(args.slice(1));
      break;
    case 'remove-rule':
      commandRemoveRule(args.slice(1));
      break;
    default:
      log('Error: unknown subcommand "' + subcommand + '". Available: add-rule, remove-rule');
      process.exit(1);
  }
}

main();
