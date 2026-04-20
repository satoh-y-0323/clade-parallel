# Report Output Common Rules

レポートを `.claude/reports/` に出力する全エージェント（interviewer / architect / planner / tester / code-reviewer / security-reviewer）共通のフロー。

## レポート出力フロー（共通）

> ⚠️ **必ず Write ツールで一時ファイルに本文を保存してから、`write-report.js --file <path>` で読み込ませること。**
> Bash のヒアドキュメント経由（`<<'CLADE_REPORT_EOF'`）は OS の引数文字数制限（目安 8,000 文字）や Claude Code の permissions チェッカーで拒否されることがある。`--file` 方式は Bash コマンドを 100 文字程度に保つため、この問題を回避できる。
>
> ⚠️ **必ず相対パス `node .claude/hooks/write-report.js` で呼び出すこと。絶対パス禁止。**
> 絶対パスは `permissions.allow` のパターン（`Bash(node .claude/hooks/write-report.js*)`）にマッチせず拒否される場合がある。

### Step 0: 既存の tmp ファイルを事前削除する

レポート本文を Write する前に、必ず以下を実行して `.claude/tmp/<baseName>.md` を削除する:

```
node .claude/hooks/clear-tmp-file.js --path .claude/tmp/<baseName>.md
```

- 既存ファイルがある場合: `[clear-tmp-file] .claude/tmp/<baseName>.md (removed)` が出力される
- 存在しない場合: `[clear-tmp-file] .claude/tmp/<baseName>.md (not exist)` が出力される（エラーではない）

**Why:** 2 回目以降の実行では `.claude/tmp/<baseName>.md` が残っているため、Step 1 の Write が「既存ファイルの上書き」と判定されて確認プロンプトが出る。事前削除することでこれを防ぐ。

### Step 1: レポート本文を一時ファイルに Write
Write ツールで `.claude/tmp/<baseName>.md` にレポート全文を書き込む。
- **長さ制約なし。何文字でも書ける**（heredoc を使わないため）
- baseName 例: `requirements-report` / `architecture-report` / `plan-report` / `test-report` / `code-review-report` / `security-review-report`
- `.claude/tmp/` は .gitignore 対象なので、作業中に作成されたファイルは git に含まれない

### Step 2: write-report.js --file で実レポートに保存
```
node .claude/hooks/write-report.js <baseName> new --file .claude/tmp/<baseName>.md
# → 例: [write-report] .claude/reports/<baseName>-20260401-143022.md
```

返却されたファイル名（`<baseName>-20260401-143022.md` の部分）を控えておく。後続の承認記録（`record-approval.js`）で使う。

### 追記する場合（既存レポートに追加）
Step 0 で tmp を削除してから、追記内容を Write ツールで `.claude/tmp/<baseName>.md` に保存する:
```
node .claude/hooks/clear-tmp-file.js --path .claude/tmp/<baseName>.md
# ↑ tmp を事前削除
# ↓ Write ツールで追記内容を書き込み、その後:
node .claude/hooks/write-report.js <baseName> append <fileName> --file .claude/tmp/<baseName>.md
```
`<fileName>` は Step 2 で控えたファイル名（タイムスタンプ付き）。

## レポート参照ルール（共通）

各エージェントが `.claude/reports/` から過去レポートを読み込む際は、以下のルールに従う。
レポートファイルは履歴として蓄積されるため、ファイルの有無ではなく**タイムスタンプで「現サイクル」を判定する**。

### 「現サイクル」の定義
**現サイクル** = 最新の plan-report のタイムスタンプ T_plan 以降に作成されたレポート。
T_plan より古い test/review レポートは前サイクルの遺物なので参照しない。

### 上流レポート（最新を Read）
- `requirements-report-*.md`
- `architecture-report-*.md`
- `plan-report-*.md`

これらは plan-report より古いか、plan-report 自身。Glob で検索して最新を Read すればよい。

### 下流レポート（T_plan 以降の最新を Read）
- `test-report-*.md`
- `code-review-report-*.md`
- `security-review-report-*.md`

これらは「現サイクルで作成されたもの」のみを参照する。手順:

```
Step 1. Glob で .claude/reports/plan-report-*.md を検索 → 最新のタイムスタンプ T_plan を取得
        plan-report が存在しない場合 → 下流レポートは「現サイクル未作成」として扱う

Step 2. Glob で対象レポート（例: test-report-*.md）を検索
        ファイル名のタイムスタンプ（YYYYMMDD-HHmmss 部分）が T_plan より新しいもののみフィルタ

Step 3. フィルタ後のリストから最新を Read
        フィルタ後が空なら「現サイクルでは未作成」として扱う
```

### エージェント別の参照対象

| エージェント | 上流（最新） | 下流（T_plan 以降の最新） |
|---|---|---|
| planner（初回モード） | requirements + architecture | — |
| planner（更新モード） | requirements + architecture + plan | test + code-review + security-review |
| tester | requirements + architecture + plan | — |
| developer | requirements + architecture + plan | test + code-review + security-review |
| code-reviewer | requirements + architecture + plan | — |
| security-reviewer | requirements + architecture + plan | — |

`approvals.jsonl` は履歴ファイル全体を Read する（フィルタ不要・追記専用ファイル）。
