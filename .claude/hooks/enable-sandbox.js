#!/usr/bin/env node
/**
 * enable-sandbox.js
 * .claude/settings.json の "sandbox" を true にする共通ロジック。
 * カスタムコマンド /enable-sandbox と /init-session から呼び出される。
 *
 * 注意: 設定変更はClaude Code再起動後に有効になる。
 */

const fs = require('fs');
const path = require('path');

const SETTINGS_PATH = path.join(process.cwd(), '.claude', 'settings.json');

function enableSandbox() {
  // git worktree 内では実行しない（.git がファイルの場合は worktree）
  const gitPath = path.join(process.cwd(), '.git');
  if (fs.existsSync(gitPath) && fs.statSync(gitPath).isFile()) {
    console.log('[enable-sandbox] git worktree 内での実行のためスキップします。');
    return { success: true, alreadyEnabled: true };
  }

  if (!fs.existsSync(SETTINGS_PATH)) {
    console.error('[enable-sandbox] settings.json が見つかりません:', SETTINGS_PATH);
    return { success: false, alreadyEnabled: false };
  }

  const raw = fs.readFileSync(SETTINGS_PATH, 'utf-8');
  const settings = JSON.parse(raw);

  if (settings.sandbox && settings.sandbox.enabled === true) {
    console.log('[enable-sandbox] sandbox はすでに有効です。');
    return { success: true, alreadyEnabled: true };
  }

  settings.sandbox = {
    enabled: true,
    autoAllowBashIfSandboxed: true,
    allowUnsandboxedCommands: false,
    excludedCommands: [],
    network: {
      allowUnixSockets: [],
      allowAllUnixSockets: false,
      allowLocalBinding: false,
      allowedDomains: []
    },
    enableWeakerNestedSandbox: false
  };
  fs.writeFileSync(SETTINGS_PATH, JSON.stringify(settings, null, 2) + '\n', 'utf-8');
  console.log('[enable-sandbox] sandbox を有効化しました。Claude Code 再起動後に反映されます。');
  return { success: true, alreadyEnabled: false };
}

// 直接実行された場合
if (require.main === module) {
  const result = enableSandbox();
  process.exit(result.success ? 0 : 1);
}

module.exports = { enableSandbox };
