#!/usr/bin/env node
// Playwright 許可オリジン管理スクリプト
// Usage:
//   node .claude/hooks/manage-playwright-origins.js list
//   node .claude/hooks/manage-playwright-origins.js add <origin>
//   node .claude/hooks/manage-playwright-origins.js remove <origin>
//
// 設計方針:
//   - settings.json (ベース) は一切変更しない
//   - 追加オリジンは settings.local.json の mcpServers.playwright に書き込む
//   - 追加オリジンが0件になったら settings.local.json から playwright エントリを削除し
//     settings.json のベース設定 (localhost のみ) に自動的に戻る

const fs = require('fs');
const path = require('path');

const SETTINGS_LOCAL_PATH = path.join(process.cwd(), '.claude', 'settings.local.json');

// settings.json に定義されているベースオリジン（変更不可）
const BASE_ORIGINS = [
  'http://localhost:*',
  'https://localhost:*',
  'http://127.0.0.1:*',
  'https://127.0.0.1:*',
];

function readSettingsLocal() {
  if (!fs.existsSync(SETTINGS_LOCAL_PATH)) return {};
  try {
    return JSON.parse(fs.readFileSync(SETTINGS_LOCAL_PATH, 'utf8'));
  } catch {
    return {};
  }
}

function writeSettingsLocal(data) {
  fs.writeFileSync(SETTINGS_LOCAL_PATH, JSON.stringify(data, null, 2) + '\n', 'utf8');
}

function getExtraOrigins(settings) {
  const args = settings?.mcpServers?.playwright?.args || [];
  const idx = args.indexOf('--allowed-origins');
  if (idx === -1) return [];
  const originsStr = args[idx + 1] || '';
  return originsStr
    .split(';')
    .map(o => o.trim())
    .filter(o => o !== '' && !BASE_ORIGINS.includes(o));
}

function buildPlaywrightEntry(extraOrigins) {
  const allOrigins = [...BASE_ORIGINS, ...extraOrigins].join(';');
  return {
    command: 'npx',
    args: ['-y', '@playwright/mcp@latest', '--allowed-origins', allOrigins],
  };
}

const [, , command, origin] = process.argv;
const settings = readSettingsLocal();

if (command === 'list') {
  const extras = getExtraOrigins(settings);

  console.log('=== Playwright 許可オリジン ===\n');
  console.log('[ベース (settings.json / 変更不可)]');
  BASE_ORIGINS.forEach(o => console.log('  ' + o));

  if (extras.length === 0) {
    console.log('\n[追加オリジン (settings.local.json)]\n  なし');
    console.log('\n→ 現在 settings.json のベース設定が有効です。');
  } else {
    console.log('\n[追加オリジン (settings.local.json)]');
    extras.forEach(o => console.log('  ' + o));
    console.log('\n→ settings.local.json が有効です（ベース + 追加オリジン）。');
  }

} else if (command === 'add') {
  if (!origin) {
    console.error('エラー: オリジンを指定してください。');
    console.error('例: node .claude/hooks/manage-playwright-origins.js add https://staging.example.com');
    process.exit(1);
  }

  if (BASE_ORIGINS.includes(origin)) {
    console.log(`"${origin}" はベースオリジンに既に含まれています。追加不要です。`);
    process.exit(0);
  }

  const extras = getExtraOrigins(settings);
  if (extras.includes(origin)) {
    console.log(`"${origin}" は既に追加されています。`);
    process.exit(0);
  }

  extras.push(origin);
  settings.mcpServers = settings.mcpServers || {};
  settings.mcpServers.playwright = buildPlaywrightEntry(extras);
  writeSettingsLocal(settings);

  console.log(`✓ "${origin}" を追加しました。`);
  console.log('Claude Code を再起動すると反映されます。');

} else if (command === 'remove') {
  if (!origin) {
    console.error('エラー: 削除するオリジンを指定してください。');
    console.error('例: node .claude/hooks/manage-playwright-origins.js remove https://staging.example.com');
    process.exit(1);
  }

  if (BASE_ORIGINS.includes(origin)) {
    console.error(`"${origin}" はベースオリジン（settings.json）のため削除できません。`);
    process.exit(1);
  }

  const extras = getExtraOrigins(settings);
  const newExtras = extras.filter(o => o !== origin);

  if (newExtras.length === extras.length) {
    console.log(`"${origin}" は追加オリジンに見つかりませんでした。`);
    process.exit(0);
  }

  if (newExtras.length === 0) {
    // 追加オリジンがゼロになったので playwright エントリを settings.local.json から削除
    if (settings.mcpServers) {
      delete settings.mcpServers.playwright;
      if (Object.keys(settings.mcpServers).length === 0) {
        delete settings.mcpServers;
      }
    }
    console.log(`✓ "${origin}" を削除しました。`);
    console.log('追加オリジンがなくなりました。settings.json のベース設定（localhost のみ）に戻ります。');
  } else {
    settings.mcpServers = settings.mcpServers || {};
    settings.mcpServers.playwright = buildPlaywrightEntry(newExtras);
    console.log(`✓ "${origin}" を削除しました。`);
  }

  writeSettingsLocal(settings);
  console.log('Claude Code を再起動すると反映されます。');

} else {
  console.log('Usage:');
  console.log('  node .claude/hooks/manage-playwright-origins.js list');
  console.log('  node .claude/hooks/manage-playwright-origins.js add <origin>');
  console.log('  node .claude/hooks/manage-playwright-origins.js remove <origin>');
  process.exit(1);
}
