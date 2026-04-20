#!/usr/bin/env node
/**
 * clear-file-history.js
 * .claude/file-history/ フォルダの中身を全削除する共通ロジック。
 * カスタムコマンド /clear-file-history と /init-session から呼び出される。
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

const FILE_HISTORY_DIR = path.join(os.homedir(), '.claude', 'file-history');

function clearFileHistory() {
  if (!fs.existsSync(FILE_HISTORY_DIR)) {
    console.log('[clear-file-history] file-history フォルダが存在しません。スキップします。');
    return { deleted: 0 };
  }

  const entries = fs.readdirSync(FILE_HISTORY_DIR, { withFileTypes: true });
  let deleted = 0;

  for (const entry of entries) {
    const fullPath = path.join(FILE_HISTORY_DIR, entry.name);
    if (entry.isDirectory()) {
      fs.rmSync(fullPath, { recursive: true, force: true });
    } else {
      fs.unlinkSync(fullPath);
    }
    deleted++;
  }

  console.log(`[clear-file-history] ${deleted} 件削除しました。`);
  return { deleted };
}

// 直接実行された場合
if (require.main === module) {
  const result = clearFileHistory();
  process.exit(result.deleted >= 0 ? 0 : 1);
}

module.exports = { clearFileHistory };
