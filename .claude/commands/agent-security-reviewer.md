# /agent-security-reviewer コマンド

セキュリティ診断エージェント（security-reviewer）を起動する。親 Claude がコンテキストを整理し、サブエージェントを一発起動してレポートを生成する。

## 親 Claude の責務

このコマンドは親 Claude が直接実行する。サブエージェントはコンテキスト整理完了後に一発起動する。

## 実行フロー

### Step 1: 上流レポートの読み込み

Glob で以下を検索し、存在すれば最新を Read する:
- `.claude/reports/requirements-report-*.md`（要件・扱うデータ種別・ユーザー種別の確認）
- `.claude/reports/architecture-report-*.md`（通信経路・認証方式・データフローの確認）
- `.claude/reports/plan-report-*.md`（担当タスクの確認）

### Step 2: 診断対象の確認

ユーザーの依頼内容から診断対象（ファイル・PR・コミット範囲等）を特定する。
不明な場合は git status / git log で最新の変更を確認する。

### Step 3: コンテキストの整理

以下を整理する:
- 診断対象ファイル・変更範囲
- 上流レポートのパス（存在する場合）
- ユーザーからの特記事項（重点確認箇所など）

### Step 4: サブエージェントの一発起動

Agent ツールで `subagent_type: security-reviewer` を指定して起動する。プロンプトに以下を含める:

```
## 作業依頼
セキュリティ診断レポートの作成

## 上流レポートのパス（存在する場合）
- requirements-report: {パス または「なし」}
- architecture-report: {パス または「なし」}
- plan-report: {パス または「なし」}

## 診断対象
{対象ファイル・PR・コミット範囲}

## 特記事項
{ユーザーからの重点確認箇所など、または「なし」}

## 出力指示
- 出力先: `.claude/reports/security-review-report-*.md`（write-report.js 経由）
- 最終メッセージにレポートファイルパスを必ず含めること（形式: `ファイル: .claude/reports/security-review-report-YYYYMMDD-HHmmss.md`）
- AskUserQuestion / SendMessage は使わないこと
- レポート生成後は終了すること（承認確認は親 Claude が担当）
```

否認後の再生成時はプロンプトに以下を追加する:
```
## 再生成モード
- 前回レポート: {前回レポートパス}
- ユーザーからの修正指示: {指示内容}
```

### Step 5: レポートパスの受け取り

サブエージェントの最終出力から正規表現 `.claude/reports/security-review-report-\d{8}-\d{6}\.md` でレポートファイルパスを抽出する。

### Step 6: 承認確認

ユーザーに以下をテキストで提示する:

```
セキュリティ診断レポートを `{ファイルパス}` に保存しました。内容を確認して、このレポートを承認しますか？（yes / no）
修正が必要な場合はその内容もお知らせください。
```

### Step 7: 承認記録

シェルインジェクション対策としてコメントは tmp ファイル経由で渡す:

1. `node .claude/hooks/clear-tmp-file.js --path .claude/tmp/approval-comment.md` を実行
2. Write ツールで `.claude/tmp/approval-comment.md` にユーザーの承認コメントを書き込む（コメントなしの場合は空文字列）
3. 以下を実行:

```bash
node .claude/hooks/record-approval.js {ファイル名} {yes|no} security-review --comment-file .claude/tmp/approval-comment.md
```

### Step 8: 否認時の再起動

否認の場合、修正指示と前レポートパスを含めた新プロンプトで Step 4 から繰り返す。

---

## 用途
- OWASP Top 10 に準拠したセキュリティ脆弱性診断
- 認証・認可・秘密情報・入力値バリデーションのチェック
- セキュリティ診断レポート（`security-review-report-*.md`）の作成

## 注意
- ソースファイルの編集・書き込みは行わない
- コード品質・保守性のレビューは code-reviewer が担当
