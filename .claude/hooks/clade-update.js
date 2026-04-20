#!/usr/bin/env node
/**
 * clade-update.js
 * clade フレームワーク更新スクリプト
 *
 * Usage:
 *   node .claude/hooks/clade-update.js --check
 *   node .claude/hooks/clade-update.js --apply
 *   node .claude/hooks/clade-update.js --rollback
 */

'use strict';

const https = require('node:https');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { spawnSync } = require('node:child_process');

// ============================================================
// Constants
// ============================================================
const GITHUB_API_HOST = 'api.github.com';
const GITHUB_REPO = 'satoh-y-0323/clade';
const RELEASES_LATEST_PATH = `/repos/${GITHUB_REPO}/releases/latest`;

const CLADE_MARKER_START = '<!-- CLADE:START -->';
const CLADE_MARKER_END = '<!-- CLADE:END -->';

const BACKUP_COMMIT_PREFIX = 'chore: backup before clade update to';

// ============================================================
// Helpers: HTTP
// ============================================================

/**
 * GitHub API トークンを取得する（gh コマンド経由）
 * @returns {string|null}
 */
function fetchGitHubToken() {
  try {
    const result = spawnSync('gh', ['auth', 'token'], { encoding: 'utf8' });
    if (result.status === 0 && result.stdout.trim()) {
      return result.stdout.trim();
    }
  } catch (_) {
    // gh コマンドが存在しない場合は無視
  }
  return null;
}

/**
 * HTTPS GET リクエストを送信して文字列レスポンスを返す
 * @param {string} host
 * @param {string} urlPath
 * @param {Record<string, string>} [extraHeaders]
 * @returns {Promise<string>}
 */
function httpsGet(host, urlPath, extraHeaders = {}) {
  return new Promise((resolve, reject) => {
    const options = {
      hostname: host,
      path: urlPath,
      method: 'GET',
      headers: {
        'User-Agent': 'clade-update/1.0',
        Accept: 'application/vnd.github+json',
        ...extraHeaders,
      },
    };

    const req = https.request(options, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        // リダイレクト対応
        const redirectUrl = new URL(res.headers.location);
        httpsGet(redirectUrl.hostname, redirectUrl.pathname + redirectUrl.search, extraHeaders)
          .then(resolve)
          .catch(reject);
        return;
      }

      let data = '';
      res.on('data', (chunk) => {
        data += chunk;
      });
      res.on('end', () => {
        if (res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode}: ${data}`));
        } else {
          resolve(data);
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(30000, () => {
      req.destroy(new Error('Request timed out'));
    });
    req.end();
  });
}

/**
 * HTTPS でバイナリデータをダウンロードして Buffer を返す
 * @param {string} url
 * @param {Record<string, string>} [extraHeaders]
 * @returns {Promise<Buffer>}
 */
function httpsDownload(url, extraHeaders = {}) {
  const ALLOWED_HOSTS = ['api.github.com', 'codeload.github.com', 'objects.githubusercontent.com'];

  return new Promise((resolve, reject) => {
    const parsedUrl = new URL(url);
    const options = {
      hostname: parsedUrl.hostname,
      path: parsedUrl.pathname + parsedUrl.search,
      method: 'GET',
      headers: {
        'User-Agent': 'clade-update/1.0',
        ...extraHeaders,
      },
    };

    const req = https.request(options, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        const redirectUrl = new URL(res.headers.location);
        const safeHeaders = ALLOWED_HOSTS.includes(redirectUrl.hostname)
          ? extraHeaders
          : Object.fromEntries(
              Object.entries(extraHeaders).filter(([k]) => k.toLowerCase() !== 'authorization')
            );
        httpsDownload(res.headers.location, safeHeaders).then(resolve).catch(reject);
        return;
      }

      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        if (res.statusCode >= 400) {
          reject(new Error(`HTTP ${res.statusCode}`));
        } else {
          resolve(Buffer.concat(chunks));
        }
      });
    });

    req.on('error', reject);
    req.setTimeout(60000, () => {
      req.destroy(new Error('Download timed out'));
    });
    req.end();
  });
}

// ============================================================
// Helpers: File system
// ============================================================

/** テスト用プロジェクトルートオーバーライド */
let _projectRootOverride = null;

/**
 * プロジェクトルートディレクトリを取得する
 * @returns {string}
 */
function getProjectRoot() {
  if (_projectRootOverride) {
    return _projectRootOverride;
  }
  // .claude/hooks/ から2階層上がプロジェクトルート
  return path.resolve(__dirname, '..', '..');
}

/**
 * .claude/VERSION ファイルのパスを返す
 * @param {string} [versionFile] オーバーライド用パス（テスト用）
 * @returns {string}
 */
function getVersionFilePath(versionFile) {
  if (versionFile) {
    return versionFile;
  }
  return path.join(getProjectRoot(), '.claude', 'VERSION');
}

/**
 * ローカルのバージョン文字列を読む
 * @param {string} [versionFile]
 * @returns {string}
 */
function readLocalVersion(versionFile) {
  const filePath = getVersionFilePath(versionFile);
  if (!fs.existsSync(filePath)) {
    throw new Error(`VERSION ファイルが見つかりません: ${filePath}`);
  }
  return fs.readFileSync(filePath, 'utf8').trim();
}

/**
 * ディレクトリを再帰的に作成する
 * @param {string} dirPath
 */
function ensureDirectory(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true });
}

/**
 * ファイルをコピーする（コピー先ディレクトリがなければ作成）
 * @param {string} sourcePath
 * @param {string} destPath
 */
function copyFile(sourcePath, destPath) {
  ensureDirectory(path.dirname(destPath));
  fs.copyFileSync(sourcePath, destPath);
}

// ============================================================
// Helpers: git
// ============================================================

/**
 * git コマンドを実行する
 * @param {string[]} args
 * @param {string} [cwd]
 * @returns {{ stdout: string; stderr: string; status: number }}
 */
function runGit(args, cwd) {
  const result = spawnSync('git', args, {
    cwd: cwd || getProjectRoot(),
    encoding: 'utf8',
  });
  return {
    stdout: result.stdout || '',
    stderr: result.stderr || '',
    status: result.status ?? 1,
  };
}

/**
 * ワーキングツリーに未コミット変更があるか確認する
 * @param {string} [cwd]
 * @returns {boolean}
 */
function hasUncommittedChanges(cwd) {
  const result = runGit(['status', '--porcelain'], cwd);
  return result.stdout.trim().length > 0;
}

/**
 * バックアップコミットメッセージのパターンにマッチするか
 * @param {string} message
 * @returns {boolean}
 */
function isBackupCommitMessage(message) {
  return message.startsWith(BACKUP_COMMIT_PREFIX);
}

/**
 * 直近コミットのメッセージを返す
 * @param {string} [cwd]
 * @returns {string}
 */
function getLatestCommitMessage(cwd) {
  const result = runGit(['log', '-1', '--format=%s'], cwd);
  return result.stdout.trim();
}

// ============================================================
// Helpers: GitHub API
// ============================================================

/**
 * GitHub 最新リリース情報を取得する
 * @returns {Promise<{ tagName: string; changelog: string; zipballUrl: string }>}
 */
async function fetchLatestRelease() {
  const token = fetchGitHubToken();
  const extraHeaders = token ? { Authorization: `token ${token}` } : {};

  const responseText = await httpsGet(GITHUB_API_HOST, RELEASES_LATEST_PATH, extraHeaders);
  const release = JSON.parse(responseText);

  const tagName = (release.tag_name || '').replace(/^v/, '');

  if (!tagName || !/^[\w.\-]+$/.test(tagName)) {
    throw new Error(`不正なバージョン文字列です: ${tagName}`);
  }

  const changelog = release.body || '';
  const zipballUrl = release.zipball_url || '';

  return { tagName, changelog, zipballUrl };
}

// ============================================================
// Helpers: Archive extraction
// ============================================================

/**
 * zip バッファを一時ディレクトリに展開する
 * @param {Buffer} zipBuffer
 * @param {string} destDir
 * @returns {Promise<void>}
 */
async function extractZip(zipBuffer, destDir) {
  ensureDirectory(destDir);

  // zip ファイルを一時ファイルに書き出してから展開
  const zipPath = path.join(destDir, '_clade_release.zip');
  fs.writeFileSync(zipPath, zipBuffer);

  // PowerShell の Expand-Archive を使用（Windows 環境向け）
  // 展開先ディレクトリ内に zip を展開する
  const escapedZipPath = zipPath.replace(/'/g, "''");
  const escapedDestDir = destDir.replace(/'/g, "''");
  const result = spawnSync(
    'powershell',
    [
      '-NoProfile',
      '-Command',
      `Expand-Archive -Force -Path '${escapedZipPath}' -DestinationPath '${escapedDestDir}'`,
    ],
    { encoding: 'utf8' }
  );

  // PowerShell が使えない場合は unzip を試みる
  if (result.status !== 0) {
    const unzipResult = spawnSync('unzip', ['-o', '-q', zipPath, '-d', destDir], {
      encoding: 'utf8',
    });
    if (unzipResult.status !== 0) {
      throw new Error(`zip 展開に失敗しました: ${result.stderr || unzipResult.stderr}`);
    }
  }

  // 一時 zip ファイルを削除
  fs.unlinkSync(zipPath);
}

/**
 * 展開されたリリースディレクトリを探す（GitHub の zip は トップレベルにフォルダが1つある）
 * @param {string} extractDir
 * @returns {string}
 */
function findReleaseDir(extractDir) {
  const entries = fs.readdirSync(extractDir);
  const dirs = entries.filter((e) => {
    const stat = fs.statSync(path.join(extractDir, e));
    return stat.isDirectory();
  });

  if (dirs.length === 1) {
    return path.join(extractDir, dirs[0]);
  }
  // 複数の場合はリポジトリ名を含むものを選択
  const cladeDir = dirs.find((d) => d.includes('clade'));
  if (cladeDir) {
    return path.join(extractDir, cladeDir);
  }
  return extractDir;
}

// ============================================================
// Helpers: File copy logic
// ============================================================

/**
 * マニフェストに基づいてファイルをコピーする
 * @param {object} manifest
 * @param {string} releaseDir - 展開されたリリースのルートディレクトリ
 * @param {string} projectRoot - プロジェクトルート
 * @param {boolean} sourceIsEnglish - リリース側の source が EN 版かどうか
 * @param {boolean} targetIsEnglish - コピー先 target が EN 版（templates/en/.claude/）かどうか
 */
function copyFilesFromManifest(manifest, releaseDir, projectRoot, sourceIsEnglish, targetIsEnglish) {
  const managed = manifest.managed_files;
  const targetPrefix = targetIsEnglish ? 'templates/en/' : '';
  const releasePrefixBase = sourceIsEnglish ? 'templates/en/.claude/' : '.claude/';
  const jaOnlyList = managed.ja_only || [];

  // commands
  for (const file of managed.commands) {
    // ja_only チェック: source が EN の場合、ja_only は source に存在しないのでスキップ
    const jaOnlyKey = `commands/${file}`;
    if (sourceIsEnglish && jaOnlyList.includes(jaOnlyKey)) {
      continue;
    }

    const srcPath = path.join(releaseDir, `${releasePrefixBase}commands`, file);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude/commands`, file);
    copyFile(srcPath, destPath);
  }

  // hooks（clade-update.js 自身は除く）
  for (const file of managed.hooks) {
    if (file === 'clade-update.js') continue;

    const srcPath = path.join(releaseDir, `${releasePrefixBase}hooks`, file);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude/hooks`, file);
    copyFile(srcPath, destPath);
  }

  // rules
  for (const file of managed.rules) {
    const srcPath = path.join(releaseDir, `${releasePrefixBase}rules`, file);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude/rules`, file);
    copyFile(srcPath, destPath);
  }

  // agents
  for (const file of (managed.agents || [])) {
    const jaOnlyKey = `agents/${file}`;
    if (sourceIsEnglish && jaOnlyList.includes(jaOnlyKey)) continue;

    const srcPath = path.join(releaseDir, `${releasePrefixBase}agents`, file);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude/agents`, file);
    copyFile(srcPath, destPath);
  }

  // skills（トップレベル: .claude/skills/。file にサブパスが含まれる場合あり）
  for (const file of (managed.skills || [])) {
    const jaOnlyKey = `skills/${file}`;
    if (sourceIsEnglish && jaOnlyList.includes(jaOnlyKey)) continue;

    const srcPath = path.join(releaseDir, `${releasePrefixBase}skills`, file);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude/skills`, file);
    copyFile(srcPath, destPath);
  }

  // agent_skills（.claude/skills/agents/）
  for (const file of (managed.agent_skills || [])) {
    const jaOnlyKey = `skills/agents/${file}`;
    if (sourceIsEnglish && jaOnlyList.includes(jaOnlyKey)) continue;

    const srcPath = path.join(releaseDir, `${releasePrefixBase}skills/agents`, file);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude/skills/agents`, file);
    copyFile(srcPath, destPath);
  }

  // other（CLAUDE.md はマーカー区間のみ更新、settings.local.json.example/その他はコピー）
  for (const file of managed.other) {
    if (file === 'CLAUDE.md') {
      // CLAUDE.md はマーカー区間のみ更新（別関数で処理）
      continue;
    }
    if (file === 'clade-manifest.json' || file === 'VERSION') {
      // VERSION と clade-manifest.json は後で個別更新
      continue;
    }

    const srcPath = path.join(releaseDir, `${releasePrefixBase}${file}`);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude`, file);
    copyFile(srcPath, destPath);
  }
}

/**
 * 対話対象ファイル（settings.json / settings.local.json）の差分を検出し、
 * 差分があれば <target>.new として横置きする
 * @param {object} manifest
 * @param {string} releaseDir
 * @param {string} projectRoot
 * @param {boolean} sourceIsEnglish
 * @param {boolean} targetIsEnglish
 * @returns {Array<{target: string, new: string, isNew: boolean}>} 差分のあったファイル情報
 */
function processInteractiveFiles(manifest, releaseDir, projectRoot, sourceIsEnglish, targetIsEnglish) {
  const managed = manifest.managed_files;
  const interactiveFiles = managed.interactive_files || [];
  const targetPrefix = targetIsEnglish ? 'templates/en/' : '';
  const releasePrefixBase = sourceIsEnglish ? 'templates/en/.claude/' : '.claude/';
  const diffs = [];

  for (const entry of interactiveFiles) {
    const sourceName = typeof entry === 'string' ? entry : entry.source;
    const targetName = typeof entry === 'string' ? entry : entry.target;

    const srcPath = path.join(releaseDir, `${releasePrefixBase}${sourceName}`);
    if (!fs.existsSync(srcPath)) continue;

    const targetPath = path.join(projectRoot, `${targetPrefix}.claude`, targetName);
    const newPath = targetPath + '.new';

    const newContent = fs.readFileSync(srcPath, 'utf8');

    if (!fs.existsSync(targetPath)) {
      // target が存在しない → 新規配置
      copyFile(srcPath, targetPath);
      // 念のため既存の .new は掃除
      if (fs.existsSync(newPath)) {
        try { fs.unlinkSync(newPath); } catch (_) {}
      }
      diffs.push({ target: targetPath, new: null, isNew: true });
      continue;
    }

    const existingContent = fs.readFileSync(targetPath, 'utf8');

    if (existingContent === newContent) {
      // 差分なし → 既存の .new があれば削除
      if (fs.existsSync(newPath)) {
        try { fs.unlinkSync(newPath); } catch (_) {}
      }
      continue;
    }

    // 差分あり → .new として横置き
    ensureDirectory(path.dirname(newPath));
    fs.writeFileSync(newPath, newContent, 'utf8');
    diffs.push({ target: targetPath, new: newPath, isNew: false });
  }

  return diffs;
}

/**
 * 保護対象ファイル（memory/memory.json など）を、既存があればスキップ・なければ初回配置
 * @param {object} manifest
 * @param {string} releaseDir
 * @param {string} projectRoot
 * @param {boolean} sourceIsEnglish
 * @param {boolean} targetIsEnglish
 */
function processProtectedFiles(manifest, releaseDir, projectRoot, sourceIsEnglish, targetIsEnglish) {
  const managed = manifest.managed_files;
  const protectedFiles = managed.protected_files || [];
  const targetPrefix = targetIsEnglish ? 'templates/en/' : '';
  const releasePrefixBase = sourceIsEnglish ? 'templates/en/.claude/' : '.claude/';

  for (const file of protectedFiles) {
    const srcPath = path.join(releaseDir, `${releasePrefixBase}${file}`);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, `${targetPrefix}.claude`, file);
    if (fs.existsSync(destPath)) {
      // 既存あり → 上書きしない
      continue;
    }

    // 初回配置
    copyFile(srcPath, destPath);
  }
}

/**
 * 旧パスに残存するファイルを削除する（ファイル移動後の残骸クリーンアップ）
 * @param {object} manifest
 * @param {string} projectRoot
 * @param {boolean} targetIsEnglish
 */
function removeObsoleteFiles(manifest, projectRoot, targetIsEnglish) {
  const removedFiles = manifest.managed_files.removed_files || [];
  const targetPrefix = targetIsEnglish ? 'templates/en/' : '';

  for (const file of removedFiles) {
    const filePath = path.join(projectRoot, `${targetPrefix}.claude`, file);
    if (fs.existsSync(filePath)) {
      try { fs.unlinkSync(filePath); } catch (_) {}
    }
  }
}

/**
 * ja_only ファイルを .claude/commands/ にコピーする（日本語版のみ）
 * @param {object} manifest
 * @param {string} releaseDir
 * @param {string} projectRoot
 */
function copyJaOnlyFiles(manifest, releaseDir, projectRoot) {
  const managed = manifest.managed_files;

  for (const filePath of managed.ja_only) {
    const srcPath = path.join(releaseDir, '.claude', filePath);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, '.claude', filePath);
    copyFile(srcPath, destPath);
  }
}

/**
 * en_only ファイルを templates/en/.claude/commands/ にコピーする
 * @param {object} manifest
 * @param {string} releaseDir
 * @param {string} projectRoot
 */
function copyEnOnlyFiles(manifest, releaseDir, projectRoot) {
  const managed = manifest.managed_files;

  for (const filePath of managed.en_only) {
    const srcPath = path.join(releaseDir, 'templates/en/.claude', filePath);
    if (!fs.existsSync(srcPath)) continue;

    const destPath = path.join(projectRoot, 'templates/en/.claude', filePath);
    copyFile(srcPath, destPath);
  }
}

/**
 * CLAUDE.md のマーカー区間を更新する
 * マーカーがない場合は marker_missing フラグを返す
 * @param {string} localClaudeMdPath - ローカルの CLAUDE.md パス
 * @param {string} releaseClaudeMdPath - リリース版の CLAUDE.md パス
 * @returns {{ markerMissing: boolean }}
 */
function updateClaudeMdMarkerSection(localClaudeMdPath, releaseClaudeMdPath) {
  if (!fs.existsSync(localClaudeMdPath)) {
    // ローカルに CLAUDE.md がない場合はそのままコピー
    if (fs.existsSync(releaseClaudeMdPath)) {
      copyFile(releaseClaudeMdPath, localClaudeMdPath);
    }
    return { markerMissing: false };
  }

  const localContent = fs.readFileSync(localClaudeMdPath, 'utf8');

  const startIdx = localContent.indexOf(CLADE_MARKER_START);
  const endIdx = localContent.indexOf(CLADE_MARKER_END);

  if (startIdx === -1 || endIdx === -1 || startIdx >= endIdx) {
    // マーカーが存在しない
    return { markerMissing: true };
  }

  if (!fs.existsSync(releaseClaudeMdPath)) {
    // リリース版に CLAUDE.md がない場合は更新スキップ
    return { markerMissing: false };
  }

  const releaseContent = fs.readFileSync(releaseClaudeMdPath, 'utf8');
  const releaseStartIdx = releaseContent.indexOf(CLADE_MARKER_START);
  const releaseEndIdx = releaseContent.indexOf(CLADE_MARKER_END);

  let newMarkerSection;
  if (releaseStartIdx !== -1 && releaseEndIdx !== -1 && releaseStartIdx < releaseEndIdx) {
    // リリース版からマーカー区間を取り出す（マーカータグ含む）
    newMarkerSection = releaseContent.substring(
      releaseStartIdx,
      releaseEndIdx + CLADE_MARKER_END.length
    );
  } else {
    // リリース版にマーカーがない場合は全体をマーカー区間として扱う
    newMarkerSection = `${CLADE_MARKER_START}\n${releaseContent}\n${CLADE_MARKER_END}`;
  }

  // ローカルのマーカー区間を置換
  const before = localContent.substring(0, startIdx);
  const after = localContent.substring(endIdx + CLADE_MARKER_END.length);
  const updatedContent = before + newMarkerSection + after;

  fs.writeFileSync(localClaudeMdPath, updatedContent, 'utf8');
  return { markerMissing: false };
}

// ============================================================
// Mode: --check
// ============================================================

/**
 * --check モードのメイン処理
 * @param {object} options
 * @param {string} [options.versionFile] - VERSION ファイルパスのオーバーライド（テスト用）
 */
async function runCheckMode(options = {}) {
  // 1. ローカルバージョンを取得
  const currentVersion = readLocalVersion(options.versionFile);

  // 2. GitHub API から最新リリース情報を取得
  const { tagName: latestVersion, changelog } = await fetchLatestRelease();

  // 3. バージョン比較
  const hasUpdate = currentVersion !== latestVersion;

  // 4. 結果を JSON で stdout に出力
  const result = {
    current_version: currentVersion,
    latest_version: latestVersion,
    has_update: hasUpdate,
    changelog,
    changes: {
      ja: { added: [], updated: [], removed: [] },
      en: { added: [], updated: [], removed: [] },
    },
  };

  process.stdout.write(JSON.stringify(result) + '\n');
}

// ============================================================
// Mode: --apply
// ============================================================

/**
 * --apply モードのメイン処理
 *
 * 二段階実行方式:
 *   Stage 1（本関数）: zip ダウンロード → 新 clade-update.js をディスクにコピー → 新スクリプトを別プロセスで起動
 *   Stage 2（runApplyFilesMode）: 新スクリプトで全管理ファイルをコピー → コミット
 *
 * これにより、clade-update.js 自身に新しいハンドラが追加されても
 * 同一実行内で新コードが使われる。
 */
async function runApplyMode() {
  const projectRoot = getProjectRoot();

  // 1. 最新リリース情報を取得
  const token = fetchGitHubToken();
  const extraHeaders = token ? { Authorization: `token ${token}` } : {};
  const { tagName: latestVersion, zipballUrl } = await fetchLatestRelease();

  // 2. バックアップコミット
  let backupCommitCreated = false;
  const backupMessage = `${BACKUP_COMMIT_PREFIX} ${latestVersion}`;
  const addResult = runGit(['add', '-A']);
  if (addResult.status !== 0) {
    process.stderr.write(`git add に失敗しました: ${addResult.stderr}\n`);
    process.exit(1);
  }

  const commitResult = runGit(['commit', '-m', backupMessage]);
  if (commitResult.status !== 0) {
    if (!commitResult.stdout.includes('nothing to commit') &&
        !commitResult.stderr.includes('nothing to commit')) {
      process.stderr.write(`バックアップコミットに失敗しました: ${commitResult.stderr}\n`);
      process.exit(1);
    }
  } else {
    backupCommitCreated = true;
  }

  // 3. リリースアセットをダウンロードして展開
  const temporaryDir = fs.mkdtempSync(path.join(os.tmpdir(), 'clade-update-'));

  try {
    const zipBuffer = await httpsDownload(zipballUrl, extraHeaders);
    await extractZip(zipBuffer, temporaryDir);
    const releaseDir = findReleaseDir(temporaryDir);

    // 4. 新しい clade-update.js を先にディスクへコピー
    //    スポーンする子プロセスが新コードを読み込めるようにする
    const newScriptSrc = path.join(releaseDir, '.claude', 'hooks', 'clade-update.js');
    const currentScriptPath = path.resolve(__dirname, 'clade-update.js');
    if (fs.existsSync(newScriptSrc)) {
      fs.copyFileSync(newScriptSrc, currentScriptPath);
    }

    // 5. 新スクリプトを別プロセスで起動してファイルコピーを実行（Stage 2）
    const child = spawnSync(
      'node',
      [currentScriptPath, '--apply-files', releaseDir, latestVersion],
      { stdio: 'inherit', cwd: projectRoot }
    );

    if (child.status !== 0) {
      throw new Error(`ファイルコピープロセスが失敗しました (exit code: ${child.status})`);
    }

    process.stdout.write(`clade を ${latestVersion} に更新しました\n`);
  } catch (error) {
    process.stderr.write(`更新中にエラーが発生しました: ${error.message}\n`);
    if (backupCommitCreated) {
      process.stderr.write('バックアップコミットに戻しています...\n');
      runGit(['reset', '--hard', 'HEAD~1']);
    }
    process.exit(1);
  } finally {
    try {
      fs.rmSync(temporaryDir, { recursive: true, force: true });
    } catch (cleanupError) {
      process.stderr.write(
        `警告: 一時ディレクトリの削除に失敗しました: ${temporaryDir}\n手動で削除してください。\n`
      );
    }
  }
}

// ============================================================
// Mode: --apply-files (internal, spawned by --apply)
// ============================================================

/**
 * --apply-files モードのメイン処理
 * --apply から新しい clade-update.js を経由して呼び出される内部モード。
 * リリース版のマニフェストを使って全管理ファイルをコピーし、コミットする。
 * @param {string} releaseDir - 展開されたリリースのルートディレクトリ
 * @param {string} latestVersion - リリースバージョン
 */
async function runApplyFilesMode(releaseDir, latestVersion) {
  const projectRoot = getProjectRoot();
  let markerMissing = false;
  const interactiveDiffs = [];

  try {
    // リリース版のマニフェストを読む（新しいセクション定義を使用するため）
    const releaseManifestPath = path.join(releaseDir, '.claude', 'clade-manifest.json');
    if (!fs.existsSync(releaseManifestPath)) {
      throw new Error(`リリース版の clade-manifest.json が見つかりません: ${releaseManifestPath}`);
    }
    const manifest = JSON.parse(fs.readFileSync(releaseManifestPath, 'utf8'));

    // ローカル manifest から言語判定
    const localManifestPath = path.join(projectRoot, '.claude', 'clade-manifest.json');
    let localLanguage = 'ja';
    if (fs.existsSync(localManifestPath)) {
      try {
        const localManifest = JSON.parse(fs.readFileSync(localManifestPath, 'utf8'));
        if (localManifest.language === 'en') {
          localLanguage = 'en';
        }
      } catch (_) {
        // 読み込み失敗時は ja デフォルト
      }
    }
    const isEnglish = localLanguage === 'en';

    // 配布用リポジトリ判定（templates/en/.claude の有無で）
    const enTemplateDir = path.join(projectRoot, 'templates', 'en', '.claude');
    const isDistributionRepo = fs.existsSync(enTemplateDir);

    if (isDistributionRepo) {
      // 配布用リポジトリ: JA/EN の両方を更新（source=target=JA、source=target=EN）
      copyFilesFromManifest(manifest, releaseDir, projectRoot, false, false);
      copyFilesFromManifest(manifest, releaseDir, projectRoot, true, true);
      removeObsoleteFiles(manifest, projectRoot, false);
      removeObsoleteFiles(manifest, projectRoot, true);
      interactiveDiffs.push(...processInteractiveFiles(manifest, releaseDir, projectRoot, false, false));
      interactiveDiffs.push(...processInteractiveFiles(manifest, releaseDir, projectRoot, true, true));
      processProtectedFiles(manifest, releaseDir, projectRoot, false, false);
      processProtectedFiles(manifest, releaseDir, projectRoot, true, true);
    } else {
      // ユーザー環境: language に応じて source を選択（target は常に .claude/）
      copyFilesFromManifest(manifest, releaseDir, projectRoot, isEnglish, false);
      removeObsoleteFiles(manifest, projectRoot, false);
      interactiveDiffs.push(...processInteractiveFiles(manifest, releaseDir, projectRoot, isEnglish, false));
      processProtectedFiles(manifest, releaseDir, projectRoot, isEnglish, false);
    }

    // ja_only / en_only ファイルのコピー（配布用リポジトリでのみ使用）
    if (isDistributionRepo) {
      copyJaOnlyFiles(manifest, releaseDir, projectRoot);
      copyEnOnlyFiles(manifest, releaseDir, projectRoot);
    }

    // CLAUDE.md のマーカー区間更新
    const localClaudeMdPath = path.join(projectRoot, '.claude', 'CLAUDE.md');
    const claudeSrcBase = (isEnglish && !isDistributionRepo)
      ? path.join(releaseDir, 'templates', 'en', '.claude')
      : path.join(releaseDir, '.claude');
    const releaseClaudeMdPath = path.join(claudeSrcBase, 'CLAUDE.md');
    const jaResult = updateClaudeMdMarkerSection(localClaudeMdPath, releaseClaudeMdPath);
    if (jaResult.markerMissing) markerMissing = true;

    // CLAUDE.md のマーカー区間更新（配布用リポジトリの EN 側）
    if (isDistributionRepo) {
      const localEnClaudeMdPath = path.join(projectRoot, 'templates', 'en', '.claude', 'CLAUDE.md');
      const releaseEnClaudeMdPath = path.join(releaseDir, 'templates', 'en', '.claude', 'CLAUDE.md');
      const enResult = updateClaudeMdMarkerSection(localEnClaudeMdPath, releaseEnClaudeMdPath);
      if (enResult.markerMissing) markerMissing = true;
    }

    // VERSION ファイルを更新
    const versionPath = path.join(projectRoot, '.claude', 'VERSION');
    fs.writeFileSync(versionPath, latestVersion + '\n', 'utf8');
    if (isDistributionRepo) {
      const enVersionPath = path.join(projectRoot, 'templates', 'en', '.claude', 'VERSION');
      fs.writeFileSync(enVersionPath, latestVersion + '\n', 'utf8');
    }

    // マニフェストをリリース版でコピー（ユーザー環境では language を保持）
    const manifestPath = path.join(projectRoot, '.claude', 'clade-manifest.json');
    if (isDistributionRepo) {
      // 配布用リポジトリ: JA 版 manifest をそのまま JA 側に、EN 版 manifest を EN 側に
      copyFile(releaseManifestPath, manifestPath);
      const enManifestSrc = path.join(releaseDir, 'templates', 'en', '.claude', 'clade-manifest.json');
      const enManifestDest = path.join(projectRoot, 'templates', 'en', '.claude', 'clade-manifest.json');
      if (fs.existsSync(enManifestSrc)) {
        copyFile(enManifestSrc, enManifestDest);
      }
    } else {
      // ユーザー環境: language フィールドを保持してコピー
      const releaseSrcPath = isEnglish
        ? path.join(releaseDir, 'templates', 'en', '.claude', 'clade-manifest.json')
        : releaseManifestPath;
      if (fs.existsSync(releaseSrcPath)) {
        const releaseManifestObj = JSON.parse(fs.readFileSync(releaseSrcPath, 'utf8'));
        releaseManifestObj.language = localLanguage;
        fs.writeFileSync(manifestPath, JSON.stringify(releaseManifestObj, null, 2) + '\n', 'utf8');
      } else {
        copyFile(releaseManifestPath, manifestPath);
      }
    }

    // 完了コミット
    runGit(['add', '-A']);
    runGit(['commit', '-m', `chore: update clade to ${latestVersion}`]);

    // 結果を JSON で stdout に出力（update.md が解析して対話ループに使う）
    const result = {
      success: true,
      version: latestVersion,
      marker_missing: markerMissing,
      interactive_diffs: interactiveDiffs.map((d) => ({
        target: d.target,
        new: d.new,
        isNew: d.isNew,
      })),
    };
    process.stdout.write(JSON.stringify(result) + '\n');

    if (markerMissing) {
      process.stderr.write(JSON.stringify({ marker_missing: true }) + '\n');
    }

    process.exit(0);
  } catch (error) {
    process.stderr.write(`ファイルコピー中にエラーが発生しました: ${error.message}\n`);
    process.exit(1);
  }
}

// ============================================================
// Mode: --rollback
// ============================================================

/**
 * --rollback モードのメイン処理
 */
function runRollbackMode() {
  const projectRoot = getProjectRoot();

  // 1. 未コミット変更チェック
  if (hasUncommittedChanges()) {
    process.stderr.write(
      '未コミットの変更があります。先にコミットまたはスタッシュしてから --rollback を実行してください\n'
    );
    process.exit(1);
  }

  // 2. バックアップコミットの存在確認（直近コミットがバックアップコミットかどうかのみを判定）
  const latestMessage = getLatestCommitMessage();
  if (!isBackupCommitMessage(latestMessage)) {
    process.stderr.write(
      '直近コミットがバックアップコミットではありません。--apply を先に実行してください\n'
    );
    process.exit(1);
  }

  // 3. git reset --hard HEAD~1
  const resetResult = runGit(['reset', '--hard', 'HEAD~1']);
  if (resetResult.status !== 0) {
    process.stderr.write(`git reset に失敗しました: ${resetResult.stderr}\n`);
    process.exit(1);
  }

  process.stdout.write('ロールバックが完了しました\n');
}

// ============================================================
// Main
// ============================================================

const args = process.argv.slice(2);
const mode = args[0];

// --version-file オプション（テスト用）
const versionFileIndex = args.indexOf('--version-file');
const versionFile = versionFileIndex !== -1 ? args[versionFileIndex + 1] : undefined;

// --project-root オプション（テスト用）
const projectRootIndex = args.indexOf('--project-root');
if (projectRootIndex !== -1 && args[projectRootIndex + 1]) {
  _projectRootOverride = path.resolve(args[projectRootIndex + 1]);
}

if (mode === '--check') {
  runCheckMode({ versionFile }).catch((error) => {
    process.stderr.write(`エラー: ${error.message}\n`);
    process.exit(1);
  });
} else if (mode === '--apply') {
  runApplyMode().catch((error) => {
    process.stderr.write(`エラー: ${error.message}\n`);
    process.exit(1);
  });
} else if (mode === '--apply-files') {
  const releaseDir = args[1];
  const latestVersion = args[2];
  if (!releaseDir || !latestVersion) {
    process.stderr.write('使用方法: node clade-update.js --apply-files <releaseDir> <version>\n');
    process.exit(1);
  }
  runApplyFilesMode(releaseDir, latestVersion).catch((error) => {
    process.stderr.write(`エラー: ${error.message}\n`);
    process.exit(1);
  });
} else if (mode === '--rollback') {
  try {
    runRollbackMode();
  } catch (error) {
    process.stderr.write(`エラー: ${error.message}\n`);
    process.exit(1);
  }
} else {
  process.stderr.write(
    `使用方法:
  node .claude/hooks/clade-update.js --check
  node .claude/hooks/clade-update.js --apply
  node .claude/hooks/clade-update.js --apply-files <releaseDir> <version>
  node .claude/hooks/clade-update.js --rollback
`
  );
  process.exit(1);
}
