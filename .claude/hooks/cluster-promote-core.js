#!/usr/bin/env node
/**
 * cluster-promote-core.js
 * 昇格候補を抽出して JSON または人間が読める形式で出力する CLI ツール。
 *
 * 使い方:
 *   node .claude/hooks/cluster-promote-core.js scan [--since today] [--json]
 */

'use strict';

const fs = require('fs');
const path = require('path');
const { parseSessionJsonBlock } = require('./hook-utils');

// ---- 定数 ----------------------------------------------------------------

const SESSIONS_DIR = path.join(__dirname, '..', 'memory', 'sessions');
const BASH_LOG_PATH = path.join(__dirname, '..', 'instincts', 'raw', 'bash-log.jsonl');

// ---- 日付ユーティリティ --------------------------------------------------

/**
 * 今日の日付を YYYYMMDD 形式で返す。
 * @returns {string}
 */
function getTodayStr() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  return `${yyyy}${mm}${dd}`;
}

/**
 * 現在日時を "YYYY-MM-DD HH:mm:ss" 形式で返す。
 * @returns {string}
 */
function getScannedAt() {
  const now = new Date();
  const yyyy = now.getFullYear();
  const mm = String(now.getMonth() + 1).padStart(2, '0');
  const dd = String(now.getDate()).padStart(2, '0');
  const hh = String(now.getHours()).padStart(2, '0');
  const min = String(now.getMinutes()).padStart(2, '0');
  const sec = String(now.getSeconds()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd} ${hh}:${min}:${sec}`;
}

// ---- ファイル読み込みユーティリティ --------------------------------------

/**
 * sessions ディレクトリから対象の .tmp ファイルパス一覧を返す。
 * @param {boolean} sinceToday - true の場合は当日ファイルのみ
 * @returns {string[]}
 */
function resolveTmpFilePaths(sinceToday) {
  if (!fs.existsSync(SESSIONS_DIR)) {
    return [];
  }
  const allFiles = fs.readdirSync(SESSIONS_DIR)
    .filter(f => f.endsWith('.tmp'))
    .map(f => path.join(SESSIONS_DIR, f));

  if (!sinceToday) {
    return allFiles;
  }
  const todayStr = getTodayStr();
  return allFiles.filter(f => path.basename(f) === `${todayStr}.tmp`);
}

/**
 * .tmp ファイルの内容から指定セクション（## 見出し）のテキストを抽出する。
 * 見出しの照合は部分一致（括弧・補足を除いたキーフレーズを含む）で行う。
 * 例: "## 試みたが失敗したアプローチ（今回は特になし）" も抽出対象になる。
 * @param {string} content - ファイル内容
 * @param {string} sectionTitle - 例: "## 試みたが失敗したアプローチ"
 * @returns {string} セクション本文（次の ## の直前まで、または末尾まで）
 */
function extractSection(content, sectionTitle) {
  // キーフレーズ: "## " を除き、括弧類より前の部分を照合キーとして使う
  const keyword = sectionTitle.replace(/^##\s*/, '').split(/[（(【「]/)[0].trim();

  const lines = content.split(/\r?\n/);
  let inSection = false;
  const collected = [];
  for (const line of lines) {
    if (line.startsWith('## ')) {
      if (inSection) break; // 次のセクション開始 → 終了
      if (line.includes(keyword)) {
        inSection = true;
        continue;
      }
    }
    if (inSection) {
      collected.push(line);
    }
  }
  return collected.join('\n').trim();
}

// ---- 候補抽出ロジック ----------------------------------------------------

/**
 * .tmp ファイル群を解析してルール候補とスキル候補を抽出する。
 * @param {string[]} filePaths
 * @returns {{ rules: CandidateRule[], skills: CandidateSkill[] }}
 */
function extractFromTmpFiles(filePaths) {
  const rules = [];
  const skills = [];

  // スキル候補の信頼度カウント用マップ: title → { candidate, count }
  const skillMap = new Map();

  for (const filePath of filePaths) {
    let content;
    try {
      content = fs.readFileSync(filePath, 'utf8');
    } catch (err) {
      process.stderr.write(`[cluster-promote-core] skip ${filePath}: ${err.message}\n`);
      continue;
    }

    const sessionName = path.basename(filePath, '.tmp');

    // JSON ブロックを優先して読み取り（存在すればテキストパースをスキップ）
    const jsonData = parseSessionJsonBlock(content);
    if (jsonData) {
      if (Array.isArray(jsonData.failures)) {
        for (const f of jsonData.failures) {
          if (!f.title) continue;
          rules.push({
            type: 'rule',
            title: f.title,
            summary: f.lesson || f.title,
            source: 'session-tmp',
          });
        }
      }
      if (Array.isArray(jsonData.successes)) {
        for (const s of jsonData.successes) {
          if (!s.title) continue;
          const summary = [s.summary, s.evidence].filter(Boolean).join(' / ');
          if (skillMap.has(s.title)) {
            skillMap.get(s.title).count += 1;
          } else {
            skillMap.set(s.title, {
              candidate: { type: 'skill', title: s.title, summary, source: 'session-tmp' },
              count: 1,
            });
          }
        }
      }
      continue; // テキストパースはスキップ
    }

    // フォールバック: テキストパース（JSON ブロックが存在しない旧形式ファイル用）

    // ルール候補: 試みたが失敗したアプローチ
    const failedSection = extractSection(content, '## 試みたが失敗したアプローチ');
    if (failedSection && failedSection !== '（未記入）') {
      const items = parseListItems(failedSection);
      for (const item of items) {
        rules.push({
          type: 'rule',
          title: item.title || `失敗アプローチ (${sessionName})`,
          summary: item.body || failedSection,
          source: 'session-tmp',
        });
      }
    }

    // スキル候補: うまくいったアプローチ（複数セッションで言及されているものを優先）
    const successSection = extractSection(content, '## うまくいったアプローチ（証拠付き）');
    if (successSection && successSection !== '（/end-session コマンドで記入してください）') {
      const items = parseListItems(successSection);
      for (const item of items) {
        const title = item.title || `成功アプローチ (${sessionName})`;
        if (skillMap.has(title)) {
          const entry = skillMap.get(title);
          entry.count += 1;
        } else {
          skillMap.set(title, {
            candidate: {
              type: 'skill',
              title,
              summary: item.body || successSection,
              source: 'session-tmp',
            },
            count: 1,
          });
        }
      }
    }
  }

  // 複数セッションで言及されているスキル候補を優先（1件でも収録するが、複数件は先頭に）
  const skillEntries = Array.from(skillMap.values());
  skillEntries.sort((a, b) => b.count - a.count);
  for (const entry of skillEntries) {
    const candidate = { ...entry.candidate };
    if (entry.count > 1) {
      candidate.summary = `(${entry.count}セッションで言及) ${candidate.summary}`;
    }
    skills.push(candidate);
  }

  return { rules, skills };
}

/**
 * Markdown のリスト形式テキストを { title, body } オブジェクトに変換する。
 * @param {string} text
 * @returns {{ title: string, body: string }[]}
 */
function parseListItems(text) {
  const lines = text.split(/\r?\n/);
  const items = [];
  let currentTitle = null;
  const bodyLines = [];

  for (const line of lines) {
    const listMatch = line.match(/^[-*]\s+(.+)/);
    if (listMatch) {
      if (currentTitle !== null) {
        items.push({ title: currentTitle, body: bodyLines.join(' ').trim() });
        bodyLines.length = 0;
      }
      currentTitle = listMatch[1].replace(/:.*$/, '').trim();
      const afterColon = listMatch[1].includes(':') ? listMatch[1].split(':').slice(1).join(':').trim() : '';
      if (afterColon) bodyLines.push(afterColon);
    } else if (currentTitle !== null && line.trim()) {
      bodyLines.push(line.trim());
    }
  }
  if (currentTitle !== null) {
    items.push({ title: currentTitle, body: bodyLines.join(' ').trim() });
  }
  return items;
}

/**
 * bash-log.jsonl を解析してルール候補を抽出する。
 * @returns {CandidateRule[]}
 */
function extractFromBashLog() {
  if (!fs.existsSync(BASH_LOG_PATH)) {
    return [];
  }

  let rawContent;
  try {
    rawContent = fs.readFileSync(BASH_LOG_PATH, 'utf8');
  } catch (err) {
    process.stderr.write(`[cluster-promote-core] skip bash-log.jsonl: ${err.message}\n`);
    return [];
  }

  const rules = [];
  const lines = rawContent.split(/\r?\n/).filter(l => l.trim());

  for (const line of lines) {
    let record;
    try {
      record = JSON.parse(line);
    } catch (_) {
      continue;
    }

    if (record.err === true) {
      const cmd = record.cmd || record.command || '(不明なコマンド)';
      const errDetail = record.stderr || record.out || '';
      const title = `コマンドエラー: ${String(cmd).slice(0, 60)}`;
      const summary = errDetail ? String(errDetail).slice(0, 200) : 'エラー詳細なし';
      rules.push({
        type: 'rule',
        title,
        summary,
        source: 'bash-log',
      });
    }
  }

  return rules;
}

// ---- メイン処理 ----------------------------------------------------------

/**
 * scan サブコマンドのエントリポイント。
 * @param {boolean} sinceToday
 * @param {boolean} outputJson
 */
function runScan(sinceToday, outputJson) {
  const tmpFilePaths = resolveTmpFilePaths(sinceToday);
  const { rules: tmpRules, skills } = extractFromTmpFiles(tmpFilePaths);

  // bash-log は --since today でも全体を対象（当日の失敗ログは bash-log 全体に含まれる前提）
  const bashLogRules = extractFromBashLog();

  const allRules = [...tmpRules, ...bashLogRules];

  // 連番 id を付与
  let idCounter = 1;
  const rulesWithId = allRules.map(r => ({ id: idCounter++, ...r }));
  const skillsWithId = skills.map(s => ({ id: idCounter++, ...s }));

  const scannedAt = getScannedAt();
  const result = {
    scannedAt,
    candidates: {
      rules: rulesWithId,
      skills: skillsWithId,
    },
  };

  if (outputJson) {
    process.stdout.write(JSON.stringify(result, null, 2) + '\n');
    return;
  }

  // 人間が読める形式
  const totalCount = rulesWithId.length + skillsWithId.length;
  process.stdout.write(`スキャン日時: ${scannedAt}\n`);
  process.stdout.write(`候補総数: ${totalCount} 件\n\n`);

  if (rulesWithId.length === 0 && skillsWithId.length === 0) {
    process.stdout.write('昇格候補は見つかりませんでした。\n');
    return;
  }

  if (rulesWithId.length > 0) {
    process.stdout.write('=== ルール候補 ===\n');
    for (const r of rulesWithId) {
      process.stdout.write(`[${r.id}] ${r.title}\n    ${r.summary}\n    ソース: ${r.source}\n\n`);
    }
  }

  if (skillsWithId.length > 0) {
    process.stdout.write('=== スキル候補 ===\n');
    for (const s of skillsWithId) {
      process.stdout.write(`[${s.id}] ${s.title}\n    ${s.summary}\n    ソース: ${s.source}\n\n`);
    }
  }
}

// ---- CLI パース ----------------------------------------------------------

const args = process.argv.slice(2);
const subcommand = args[0];

if (subcommand !== 'scan') {
  process.stderr.write(`使い方: node cluster-promote-core.js scan [--since today] [--json]\n`);
  process.exit(1);
}

const sinceToday = args.includes('--since') && args[args.indexOf('--since') + 1] === 'today';
const outputJson = args.includes('--json');

try {
  runScan(sinceToday, outputJson);
  process.exit(0);
} catch (err) {
  process.stderr.write(`[cluster-promote-core] 予期せぬエラー: ${err.message}\n${err.stack}\n`);
  process.exit(1);
}
