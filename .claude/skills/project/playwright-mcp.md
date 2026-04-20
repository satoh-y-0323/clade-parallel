# Playwright MCP スキル

## 概要

Microsoft 製の Playwright MCP サーバ。ブラウザ操作（クリック・入力・スクリーンショット・ページナビゲーション等）を Claude から直接実行できる。
主にローカル開発中のWebアプリのE2Eテスト・動作確認・デバッグ支援に使用する。

## セキュリティ設定

このプロジェクトでは `--allowed-origins` オプションにより、ブラウザがアクセスできるオリジンを **ローカルホスト限定** に制限している。

```
--allowed-origins "http://localhost:*;https://localhost:*;http://127.0.0.1:*;https://127.0.0.1:*"
```

### 制限の意図
- 開発・テスト目的のツールであるため、外部サイトへの意図しないアクセスを防ぐ
- ローカルで動くWebアプリの確認に用途を絞ることで、誤操作によるデータ送信リスクを排除する
- 外部サイトへのアクセスが必要な場合は、`settings.json` の `--allowed-origins` を一時的に変更すること（変更後は必ず元に戻す）

### 注意事項
- `--allowed-origins` はリダイレクト先には適用されない（公式仕様）
- セキュリティ境界としての完全な保証はないため、機密操作には使用しない

## 提供されるツール

| ツール名 | 説明 | 使用場面 |
|---|---|---|
| `browser_navigate` | 指定URLへ移動 | ローカルアプリのページを開く |
| `browser_click` | 要素をクリック | ボタン・リンク操作 |
| `browser_type` | テキスト入力 | フォーム入力 |
| `browser_snapshot` | アクセシビリティスナップショット取得 | ページ構造の確認（軽量） |
| `browser_take_screenshot` | スクリーンショット取得 | 画面の目視確認 |
| `browser_wait_for` | 要素・テキストが表示されるまで待機 | 非同期UIの確認 |
| `browser_select_option` | セレクトボックスの選択 | ドロップダウン操作 |
| `browser_check` / `browser_uncheck` | チェックボックス操作 | フォーム操作 |
| `browser_hover` | 要素にホバー | ツールチップ・ドロップダウンメニュー確認 |
| `browser_press_key` | キーボード操作 | Enter・Tab・Escなど |
| `browser_go_back` / `browser_go_forward` | ブラウザ履歴操作 | 前後ページの確認 |
| `browser_tab_new` | 新しいタブを開く | 複数ページの並行操作 |
| `browser_close` | ブラウザを閉じる | 操作終了後のクリーンアップ |

## このプロジェクトでの使い方

### 典型的なユースケース

1. **ローカル開発サーバの動作確認**
   ```
   「http://localhost:3000 を開いて、ログインフォームにテスト用IDとパスワードを入力してログインボタンをクリックし、遷移先のURLとページタイトルを教えてください」
   ```

2. **UI変更後のスクリーンショット確認**
   ```
   「http://localhost:3000/dashboard のスクリーンショットを撮ってください」
   ```

3. **フォームのバリデーション確認**
   ```
   「http://localhost:3000/register を開き、メールアドレスフィールドに不正な値（abc）を入力してサブミットし、エラーメッセージが表示されるか確認してください」
   ```

4. **E2Eテスト仕様の動作検証**
   ```
   「http://localhost:3000 で以下の操作をして結果を報告してください: 1) トップページを開く 2) ナビゲーションの「商品一覧」をクリック 3) 最初の商品をクリック 4) カートに追加ボタンをクリック 5) カートアイコンのバッジ数が1になっているか確認」
   ```

## 必要な環境変数

なし（APIキー不要）

## 起動設定（参考）

`.claude/settings.json` に以下の設定が追加済み:

```json
"playwright": {
  "command": "npx",
  "args": [
    "-y",
    "@playwright/mcp@latest",
    "--allowed-origins",
    "http://localhost:*;https://localhost:*;http://127.0.0.1:*;https://127.0.0.1:*"
  ]
}
```

## 外部アクセスが必要な場合

外部サイト（例: staging環境・本番確認）にアクセスしたい場合は、`settings.json` の `--allowed-origins` を変更する。

```json
"args": [
  "-y",
  "@playwright/mcp@latest"
]
```

変更後は Claude Code を再起動して設定を反映させること。作業完了後は必ずローカルホスト限定設定に戻すこと。
