# /cluster-promote コマンド

Bash 実行ログとセッション振り返りを分析して、
プロジェクト固有のスキルまたはルールに昇格させる。

## 実行手順

### ステップ1: 候補抽出

以下を実行して候補 JSON を取得する:
```
Bash: node .claude/hooks/cluster-promote-core.js scan --json
```

スクリプトが exit 1 で失敗した場合は stderr の内容をユーザーに表示して終了する。

### ステップ2: 候補をユーザーに提示

取得した JSON を整形して以下の形式で提示する:

```
## 昇格候補

### スキル候補（繰り返し成功した手順）
1. {スキル名}: {概要} ({N}セッションで言及)
2. ...

### ルール候補（Bash ログの失敗パターン）
1. {ルール名}: {何が起きたか・どう避けるか} ({N}回発生)
2. ...

### ルール候補（セッション振り返りの失敗パターン）
1. {ルール名}: {何が失敗したか・教訓}
2. ...

昇格するものを番号で選んでください（例: 1,3）/ all / none
```

### ステップ4: 承認されたものを保存

**スキル** → Write で `.claude/skills/project/{name}.md` に保存:
- フロントマターなし・Markdown 形式で手順を記述
- 「いつ使うか」「手順」「注意点」を記載

**ルール** → Write で `.claude/rules/{name}.md` に保存:
- 「〜してはいけない」「〜の場合は〜する」形式で記述
- 違反した場合の影響も記載

**ルールを保存した場合、必ず続けて以下を実行する:**
```
Bash: node .claude/hooks/update-clade-section.js add-rule {name}
```
- exit 0: 正常追記（または CLADE マーカーが見つからず no-op）
- exit 2: 既に CLAUDE.md に存在するため no-op（問題なし）
- exit 1: 書き込みエラー → ユーザーに警告を表示し、手動で `.claude/CLAUDE.md` の `## User Rules` セクションに `@rules/{name}.md` を追記するよう案内する

**クラスタ情報** → Write で `.claude/instincts/clusters/{YYYYMMDD}-{name}.json` に保存:
```json
{
  "type": "skill | rule",
  "name": "{name}",
  "summary": "{一行概要}",
  "promotedAt": "{YYYY-MM-DD}",
  "source": "bash-log | session-tmp"
}
```

### ステップ5: bash-log.jsonl をアーカイブ

bash-log.jsonl が存在し内容がある場合のみ実施:
1. Read で内容を確認
2. Write で `.claude/instincts/raw/bash-log_{YYYYMMDD}_archived.jsonl` に書き込む
3. Write で元ファイル（bash-log.jsonl）を `""` (空文字) でリセットする

※ シェルコマンドは使用しない（クロスプラットフォーム対応）
