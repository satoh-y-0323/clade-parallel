# /agent-code-reviewer コマンド

コードレビューエージェント（code-reviewer）を起動する。親 Claude がコンテキストを整理し、サブエージェントを一発起動してレポートを生成する。

## 親 Claude の責務

このコマンドは親 Claude が直接実行する。サブエージェントはコンテキスト整理完了後に一発起動する。

## 実行フロー

### Step 1: 上流レポートの読み込み

Glob で以下を検索し、存在すれば最新を Read する:
- `.claude/reports/requirements-report-*.md`（要件・完了条件の確認）
- `.claude/reports/architecture-report-*.md`（設計意図・インターフェース仕様の確認）
- `.claude/reports/plan-report-*.md`（担当タスクの確認）

### Step 2: レビュー対象の確認

ユーザーの依頼内容からレビュー対象（ファイル・PR・コミット範囲等）を特定する。
不明な場合は git status / git log で最新の変更を確認する。

### Step 3: コンテキストの整理

以下を整理する:
- レビュー対象ファイル・変更範囲
- 上流レポートのパス（存在する場合）
- ユーザーからの特記事項（重点確認箇所など）

### Step 4: サブエージェントの一発起動

Agent ツールで `subagent_type: code-reviewer` を指定して起動する。プロンプトに以下を含める:

```
## 作業依頼
コードレビューレポートの作成

## 上流レポートのパス（存在する場合）
- requirements-report: {パス または「なし」}
- architecture-report: {パス または「なし」}
- plan-report: {パス または「なし」}

## レビュー対象
{対象ファイル・PR・コミット範囲}

## 特記事項
{ユーザーからの重点確認箇所など、または「なし」}

## 出力指示
- 出力先: `.claude/reports/code-review-report-*.md`（write-report.js 経由）
- 最終メッセージにレポートファイルパスを必ず含めること（形式: `ファイル: .claude/reports/code-review-report-YYYYMMDD-HHmmss.md`）
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

サブエージェントの最終出力から正規表現 `.claude/reports/code-review-report-\d{8}-\d{6}\.md` でレポートファイルパスを抽出する。

### Step 6: 承認確認

ユーザーに以下をテキストで提示する:

```
コードレビューレポートを `{ファイルパス}` に保存しました。内容を確認して、このレポートを承認しますか？（yes / no）
修正が必要な場合はその内容もお知らせください。
```

### Step 7: 承認記録

シェルインジェクション対策としてコメントは tmp ファイル経由で渡す:

1. `node .claude/hooks/clear-tmp-file.js --path .claude/tmp/approval-comment.md` を実行
2. Write ツールで `.claude/tmp/approval-comment.md` にユーザーの承認コメントを書き込む（コメントなしの場合は空文字列）
3. 以下を実行:

```bash
node .claude/hooks/record-approval.js {ファイル名} {yes|no} code-review --comment-file .claude/tmp/approval-comment.md
```

### Step 8: 否認時の再起動

否認の場合、修正指示と前レポートパスを含めた新プロンプトで Step 4 から繰り返す。

---

## 用途
- コード品質・保守性・パフォーマンスのレビュー
- 要件・設計との整合性確認
- コードレビューレポート（`code-review-report-*.md`）の作成

## 注意
- ソースファイルの編集・書き込みは行わない
- セキュリティ脆弱性診断は security-reviewer が担当
