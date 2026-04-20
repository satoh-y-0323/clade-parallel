# /end-session コマンド

セッションの成果・失敗・残タスクをセッションファイルに記録して保存する。

## 使い方
```
/end-session              # 通常実行（昇格候補の提示あり）
/end-session --no-promote # ステップ5（昇格候補提示）をスキップして終了
```

## 実行手順
1. 本日のセッションファイルパスを決定:
   `.claude/memory/sessions/{YYYYMMDD}.tmp`
2. 今セッションで行った作業を振り返り、以下のテンプレートで内容を生成する:

```
SESSION: {YYYYMMDD}
AGENT: {使用したエージェント名}
DURATION: {作業時間の概算}

## うまくいったアプローチ（証拠付き）
- {アプローチ名}: {具体的に何をしたか}
  証拠: {コミットハッシュ / ファイルパス / テスト結果 / コマンド出力}

## 試みたが失敗したアプローチ
- {アプローチ名}: {失敗理由}
  教訓: {次回避けること}

## まだ試していないアプローチ
- {仮説}: {試す価値がある理由}

## 残タスク
- [ ] {タスク1} (優先度: 高)
- [ ] {タスク2} (優先度: 中)
- [ ] {タスク3} (優先度: 低)
```

3. ファイルへの書き込み:
   - **ファイルが存在しない場合**: Write ツールで上記テンプレートをそのまま書き込む
   - **ファイルが既に存在する場合**: Read ツールで既存内容を読み込み、各セクションに**追記**する形で Edit ツールを使用する。セクション見出しは重複させず、既存の箇条書きの末尾に新しい項目を追加すること。
     例: `## うまくいったアプローチ（証拠付き）` が既にある場合、そのセクションの末尾に新しいアプローチを追加する（セクションを二重に作らない）

3.5. JSON ブロックの書き込み:
   セッション内容を以下のスキーマで JSON 化し、ファイルの末尾に追記する。
   既に `<!-- CLADE:SESSION:JSON` ブロックが存在する場合は Edit ツールで置換する。

   ```
   <!-- CLADE:SESSION:JSON
   {
     "session": "{YYYYMMDD}",
     "successes": [
       { "title": "{アプローチ名}", "summary": "{説明}", "evidence": "{コミットハッシュ等}" }
     ],
     "failures": [
       { "title": "{アプローチ名}", "lesson": "{教訓}" }
     ],
     "todos": [
       { "done": false, "text": "{タスク説明}", "priority": "{高|中|低}" }
     ]
   }
   -->
   ```

   - `successes` / `failures` / `todos` は上の Markdown セクションと同じ内容を構造化したもの
   - `## 事実ログ（自動生成 / stop.js）` セクションが既に存在する場合は、その**後**に配置する
   - `done: true` のタスクはリストに含めなくてよい

4. 完了を報告する

## 注意
セッションファイルは次回の `/init-session` で自動的に読み込まれる。
残タスクは具体的に書くこと（「実装する」ではなく「UserServiceのcreateメソッドを実装する」）。

## ステップ5: 昇格候補の提示（インライン）

> **注:** `/end-session --no-promote` で実行された場合は、このステップ全体をスキップして完了報告へ進む。

セッションファイル保存完了後、以下を実行する:

1. `.claude/memory/pending-promotions.json` を Read する（存在しない場合はスキップ）
2. 以下のコマンドで当日の候補を取得する:
   ```
   node .claude/hooks/cluster-promote-core.js scan --since today --json
   ```
3. pending-promotions.json の候補と今日の候補をマージする
4. 候補が0件の場合はスキップして完了報告へ進む
5. 候補がある場合は AskUserQuestion で以下を提示する:
   「本日のセッションから昇格候補が見つかりました。
   （候補一覧を番号付きで表示）
   保存しますか？
     [yes] 番号を指定して保存（例: 1,3）または all
     [no]  保存しない（pending があれば削除）
     [later] 次回 /end-session 時に再提示する」

6-a. yes の場合:
   - 選択した候補ごとに:
     - ルール: Write で `.claude/rules/{name}.md` に保存
     - スキル: Write で `.claude/skills/project/{name}.md` に保存
     - ルールの場合: `node .claude/hooks/update-clade-section.js add-rule {name}` を実行
   - `.claude/memory/pending-promotions.json` を削除（存在すれば）
     ```
     Bash: rm .claude/memory/pending-promotions.json
     ```

6-b. later の場合:
   - 未処理候補を `.claude/memory/pending-promotions.json` に Write で保存
   - スキーマ: `{ "savedAt": "YYYY-MM-DD", "candidates": [...] }`

6-c. no の場合:
   - `.claude/memory/pending-promotions.json` が存在すれば削除
     ```
     Bash: rm .claude/memory/pending-promotions.json
     ```
   - ※ 削除は Bash の rm コマンドを使用可
