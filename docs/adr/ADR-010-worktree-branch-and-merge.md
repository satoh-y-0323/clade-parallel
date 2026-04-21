# ADR-010: git worktree ブランチ化とマージ後処理

- **Date:** 2026-04-21
- **Status:** Accepted
- **Supersedes:** ADR-009

## Context

clade-parallel v0.3 では `git worktree add --detach` を使用して書き込みタスクを並列実行していた（ADR-009）。
この方式では worktree が detached HEAD 状態になるため、タスク内でコミットを行っても、worktree 削除後にそのコミットが参照されなくなる問題があった。

具体的な問題:
- `--detach` worktree 内でのコミットは、削除時に参照が失われる（dangling commit）
- `git worktree remove --force` によって変更が強制廃棄される
- 並列実行した書き込みタスクのコミット結果がメインブランチに反映されない

v0.4 では、並列タスクがコミットを生成しても確実にメインブランチへ取り込める方式へ移行する必要がある。

## Decision

`git worktree add --detach` を廃止し、タスクごとに専用ブランチを持つ worktree を作成する方式へ移行する。
全タスク完了後に、マニフェスト宣言順でブランチをメインブランチへマージする後処理を追加する。

具体的な決定内容:

1. **ブランチ付き worktree の作成**
   - コマンド: `git worktree add -b clade-parallel/<task-id>-<uuid8> <worktree_path>`
   - ブランチ名形式: `clade-parallel/<task-id>-<uuid8>`（`uuid.uuid4().hex[:8]` で 8 文字の乱数サフィックス）
   - worktree の配置場所は ADR-009 と同様: `<git_root>/.clade-worktrees/<task_id>-<uuid8>/`

2. **全タスク完了後のマージ後処理**
   - マージ順序: マニフェストの `tasks` 宣言順（depends_on 解決後の実行順ではなく宣言順）
   - マージコマンド: `git merge --no-ff --no-edit clade-parallel/<task-id>-<uuid8>`
   - `--no-ff`: マージコミットを必ず作成し、並列実行の記録を残す
   - `--no-edit`: エディタを起動せず自動コミットメッセージを使用する

3. **コンフリクト時のフェイルファスト**
   - コンフリクト発生時: `git merge --abort` を実行してマージを中断する
   - その後 `RunnerError` を送出して処理を終了する
   - 部分マージ済みのブランチはそのまま残し、ユーザーが手動解決できる状態を維持する

4. **マージ成功後のブランチ削除**
   - コマンド: `git branch -d clade-parallel/<task-id>-<uuid8>`
   - `-d`（safe delete）を使用: マージ済みブランチのみ削除できるため安全
   - worktree 削除後に実行する（`git worktree remove` → `git branch -d` の順）

5. **detached HEAD での実行禁止**
   - `run_manifest()` 冒頭で `git symbolic-ref HEAD` を実行し、detached HEAD 状態を検出する
   - detached HEAD の場合は `RunnerError` を即座に送出（早期失敗）
   - 理由: detached HEAD 状態ではマージ先ブランチが存在しないため処理が成立しない

## Rationale

| 観点 | 理由 |
|---|---|
| コミットの永続化 | ブランチ付き worktree では `git branch -d` するまでコミットが参照され続ける。dangling commit は発生しない |
| マージによる統合 | `git merge --no-ff` はマージコミットを作成するため、並列実行の履歴が明確に記録される |
| 宣言順マージ | マニフェストの `tasks` 宣言順でマージすることで、ユーザーが意図した統合順序が再現される |
| コンフリクトのフェイルファスト | `writes` フィールドによる静的衝突チェック（ADR-006）が通過した後のコンフリクトは想定外の状態であり、フェイルファストが適切 |
| detached HEAD の禁止 | マージ先が存在しない状態での実行はコミットロストと同様の問題を引き起こすため、早期失敗が安全 |
| uuid4 サフィックスの継続 | ADR-009 と同様に予測不可能なパス・ブランチ名を維持する |

## Alternatives Considered

**`--detach` のまま維持し、cherry-pick で統合する**: cherry-pick はコミットの順序依存性が高く、
複数コミットをまたぐ場合に操作が複雑になる。マージのほうがシンプルで安全。

**rebase による統合**: rebase はコミット SHA が変わり、並列実行の履歴が直線化されて並列実行の記録が失われる。
`--no-ff` マージのほうが履歴の透明性が高い。

**コンフリクト時に自動解決を試みる**: 自動解決はデータロストのリスクがある。
`writes` フィールドによる静的チェックが通過した後のコンフリクトは人間の判断が必要であり、フェイルファストが適切。

**実行順（depends_on 解決後の順）でマージする**: 実行順はランタイムに決定されるため再現性がない。
宣言順はマニフェストに明示されており、再現可能な統合順序を保証する。

## ADR-009 との関係

本 ADR は ADR-009（`--detach` worktree 方式）を廃止し、コミットがロストしない並列開発フローを実現するものである。
ADR-009 のステータスを「Superseded by ADR-010」に更新する。

worktree の配置パス（`.clade-worktrees/<task_id>-<uuid8>/`）、uuid4 サフィックス、
`git リポジトリ外での書き込みタスクは RunnerError` とする方針は ADR-009 から継承する。

## Consequences

- `runner.py` に以下を変更・追加する:
  - `_worktree_setup()`: `git worktree add --detach` を `git worktree add -b clade-parallel/<task-id>-<uuid8>` へ変更
  - `_merge_worktree_branches(git_root: Path, tasks: list[Task], branch_map: dict[str, str]) -> None` 関数を追加
    - マニフェスト宣言順でブランチをマージ
    - コンフリクト時は `git merge --abort` → `RunnerError`
    - マージ後に `git branch -d` → `git worktree remove` の順でクリーンアップ
  - `run_manifest()` 冒頭に detached HEAD チェックを追加（`git symbolic-ref HEAD` が失敗したら `RunnerError`）
- 全タスク完了後のマージ後処理は `run_manifest()` 内で `_merge_worktree_branches()` を呼び出す形で実装する
- `.clade-worktrees/` の `.gitignore` 登録は ADR-009 と同様に維持する
- `clade-parallel/` プレフィックスのブランチがリモートに push されないよう、ドキュメントで注意喚起する
