# /update コマンド

clade フレームワークを最新バージョンに更新する。

## 実行手順

### Step 1: 更新チェック

Bash ツールで以下を実行する:

```bash
node .claude/hooks/clade-update.js --check
```

出力は JSON 形式。パースして以下で分岐する:

#### 更新がない場合（`has_update: false`）

「clade はすでに最新バージョン（`{current_version}`）です。」と伝えて終了する。

#### ネットワークエラーなどで失敗した場合

エラーメッセージをユーザーに伝えて終了する。

### Step 2: 差分表示

`has_update: true` の場合、以下の形式でユーザーに提示する:

```
## clade 更新情報

現在のバージョン: {current_version}
最新のバージョン: {latest_version}

### 変更内容
{changelog}

### ファイル変更一覧（日本語版）
追加: {ja.added のリスト、なければ "(なし)"}
更新: {ja.updated のリスト、なければ "(なし)"}
削除: {ja.removed のリスト、なければ "(なし)"}

### ファイル変更一覧（英語テンプレート）
追加: {en.added のリスト、なければ "(なし)"}
更新: {en.updated のリスト、なければ "(なし)"}
削除: {en.removed のリスト、なければ "(なし)"}
```

（追加・更新・削除のリストが全て空の場合は「ファイル変更一覧」セクションを省略する）

### Step 3: 更新確認

ユーザーに確認を取る:

```
更新しますか？
  [yes] 更新を適用する（現在の状態をバックアップコミット後に更新）
  [no]  更新をキャンセルする
```

- **no の場合**: 「更新をキャンセルしました。」と伝えて終了する。

### Step 4: 更新適用

**yes の場合**: Bash ツールで以下を実行する:

```bash
node .claude/hooks/clade-update.js --apply
```

- **成功した場合**:
  1. stdout の最終行は JSON 形式。パースして `interactive_diffs` 配列を取得する。
  2. `interactive_diffs` が空または存在しない場合は、「clade を `{latest_version}` に更新しました。」と伝えて終了する。
  3. `interactive_diffs` が1件以上ある場合は Step 5 へ進む。
- **成功かつ stderr に `{"marker_missing":true}` が含まれる場合**: 以下を案内する:
  「CLAUDE.md に CLADE マーカーが見つかりませんでした。CLADE 管理セクションは更新されませんでした。
  手動で `<!-- CLADE:START -->` / `<!-- CLADE:END -->` マーカーを追加してください。」
- **失敗した場合**: エラーメッセージを表示し、以下を案内する:

```
更新中にエラーが発生しました。ロールバックしますか？
  [yes] ロールバックする（更新前の状態に戻す）
  [no]  このままにする
```

### Step 5: 対話的な差分処理ループ

apply の stdout JSON の `interactive_diffs` 配列を順番に処理する。
各エントリは以下のフィールドを持つ:

- `target`: 更新対象のファイルパス
- `new`: 新バージョンが配置された `.new` ファイルのパス（差分がない場合は `null`）
- `isNew`: `true` の場合は新規配置済み（ユーザーの既存ファイルが存在しなかった）

各エントリに対して以下を行う:

1. **`new` が `null` または `isNew === true` の場合**: 「`{target}` を新規配置しました。」と伝えて次のエントリへ。
2. **`new` が非 null の場合**:
   1. Bash ツールで差分を表示する:
      ```bash
      git diff --no-index --color=never "<target>" "<new>"
      ```
      ※ 差分がある場合 git は exit code 1 を返す。これはエラーではないので stdout の内容を表示する。
   2. ユーザーに確認を取る:
      ```
      {target} に差分があります。{new} の内容で上書きしますか？
        [yes] 上書きする（.new ファイルは削除されます）
        [no]  上書きしない（.new ファイルは残ります。手動でマージしてください）
      ```
   3. **yes の場合**: Bash ツールで以下を実行する:
      ```bash
      node .claude/hooks/apply-diff.js --target "<target>" --new "<new>"
      ```
      「`{target}` を上書きしました。」と伝える。
   4. **no の場合**: 「`{new}` をそのまま残しました。内容を確認してから手動で `{target}` にマージしてください。」と伝える。

すべてのエントリを処理し終えたら、「clade を `{latest_version}` に更新しました。」と伝えて終了する。

### Step 6: ロールバック（エラー時のみ）

ロールバックを選択した場合は Bash ツールで以下を実行する:

```bash
node .claude/hooks/clade-update.js --rollback
```

- **成功した場合**: 「更新前の状態に戻しました。」と伝える。
- **失敗した場合**: エラーメッセージをユーザーに伝える。

## 注意事項

- 更新前に現在の状態が自動でバックアップコミットされる
- ユーザーが `.claude/` に追加したファイルは上書きされない（clade-manifest.json に登録されたファイルのみ更新）
- CLAUDE.md のユーザー記述部分（`<!-- CLADE:END -->` 以降）は更新されない
- `settings.json` / `settings.local.json` はユーザー独自の設定を含む可能性があるため、差分がある場合は対話で上書きを確認する（Step 5）
- `memory/memory.json` はユーザーの蓄積データを壊さないよう、既にファイルが存在する場合は一切更新されない
