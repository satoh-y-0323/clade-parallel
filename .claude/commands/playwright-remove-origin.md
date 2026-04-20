# /playwright-remove-origin コマンド

Playwright MCP の追加オリジンを削除する。
削除対象は settings.local.json のみ（settings.json のベースオリジンは削除不可）。

## 引数
- `$ARGUMENTS`: 削除するオリジン（例: `https://staging.example.com`）

## 実行手順
1. 引数が指定されていない場合は「削除するオリジンを指定してください（例: /playwright-remove-origin https://staging.example.com）」と伝えて終了する。
2. Bash ツールで以下を実行する（`$ARGUMENTS` を実際のオリジンに置き換える）:
   ```
   node .claude/hooks/manage-playwright-origins.js remove $ARGUMENTS
   ```
3. 出力結果をユーザーに提示する。
