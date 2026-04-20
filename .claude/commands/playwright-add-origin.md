# /playwright-add-origin コマンド

Playwright MCP の許可オリジンを追加する。
追加先は settings.local.json のみ（settings.json は変更しない）。

## 引数
- `$ARGUMENTS`: 追加するオリジン（例: `https://staging.example.com`）

## 実行手順
1. 引数が指定されていない場合は「追加するオリジンを指定してください（例: /playwright-add-origin https://staging.example.com）」と伝えて終了する。
2. Bash ツールで以下を実行する（`$ARGUMENTS` を実際のオリジンに置き換える）:
   ```
   node .claude/hooks/manage-playwright-origins.js add $ARGUMENTS
   ```
3. 出力結果をユーザーに提示する。
