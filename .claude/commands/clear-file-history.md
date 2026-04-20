# /clear-file-history コマンド

`.claude/file-history/` フォルダの中身を全削除するセキュリティコマンド。
Claude Code がファイル編集時に自動生成するバックアップを手動でクリアする。

## 実行手順
1. Bash ツールで以下を実行する:
   ```
   node .claude/hooks/clear-file-history.js
   ```
2. 出力結果をユーザーに報告する:
   - 削除件数を表示する
   - フォルダが存在しない場合はその旨を伝える
