# /enable-sandbox コマンド

`.claude/settings.json` に `"sandbox": true` を設定するセキュリティコマンド。
Claude Code のサンドボックス機能を有効化し、Bash コマンドの実行を制限された環境に閉じ込める。

## 実行手順
1. Bash ツールで以下を実行する:
   ```
   node .claude/hooks/enable-sandbox.js
   ```
2. 出力結果をユーザーに報告する:
   - 有効化できた場合: 「sandbox を有効化しました。Claude Code を再起動すると反映されます。」と伝える
   - すでに有効だった場合: 「sandbox はすでに有効です。」と伝える
   - 失敗した場合: エラー内容を提示する
