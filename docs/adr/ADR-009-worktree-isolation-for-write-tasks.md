# ADR-009: `read_only: false` タスクの worktree 隔離方針

- **Date:** 2026-04-21
- **Status:** Accepted

## Context

clade-parallel v0.3 では `read_only: false` タスクを安全に並列実行するため、ファイルシステムレベルの隔離が必要である。
同一の作業ディレクトリで複数の書き込みタスクが並列実行されると、ファイル衝突・データ破損が起きる可能性がある。
ADR-003 が「worktree 隔離は v0.3 以降に延期する」と決定したのはこの設計が未完成だったためであり、本 ADR で方針を確定させる。

隔離の実装方式として以下を検討した:

1. **`git worktree add --detach`**: 同一リポジトリ内に独立した作業ツリーを作成する
2. **`shutil.copytree` によるディレクトリコピー**: リポジトリを丸ごとコピーして分離する
3. **`tmpfs` / OS の一時ディレクトリ**: `/tmp` 等に作業ディレクトリを作成する

## Decision

`git worktree add --detach` を使用して worktree 隔離を実装する。

具体的な方針:

- **worktree の配置場所**: `<git_root>/.clade-worktrees/<task_id>-<uuid8>/`（`uuid.uuid4().hex[:8]` で 8 文字の乱数サフィックスを付与）
- **作成コマンド**: `git worktree add --detach <worktree_path>`（shell=False, 引数はリスト渡し）
- **削除コマンド**: `git worktree remove --force <worktree_path>`（タイムアウト 30 秒）
- **cleanup の失敗**: 例外を握り潰す（best-effort）。タスクの結果には影響しない
- **git リポジトリ外での書き込みタスク**: `RunnerError` を送出して処理を中断する（後述）
- **全タスクが `read_only: true` の場合**: git リポジトリ外でも従来どおり動作する（後方互換）
- **`.clade-worktrees/` の `.gitignore` 登録**: worktree ディレクトリ全体を `.gitignore` に追加する

## Rationale

| 観点 | 理由 |
|---|---|
| git worktree の完全な分離 | `git worktree add` は同一リポジトリの任意コミット/HEAD を独立した作業ツリーとして展開する。ファイルシステムレベルで分離されるため衝突しない |
| コピーコストが不要 | `shutil.copytree` はリポジトリサイズに比例して時間がかかる。`git worktree` はハードリンク / シンボリックリンクを内部で使い、高速に作成できる |
| `--detach` によるブランチ汚染防止 | `--detach` を使用することで worktree が既存ブランチに紐付かず、cleanup 時にブランチが残存しない |
| `--force` cleanup による確実な削除 | 変更が残っていても `--force` で強制削除できる。タスク完了後の worktree はキャッシュ不要 |
| uuid4 による予測不可能なパス | `uuid4()` は暗号学的ランダム性を持ち、パスの衝突・予測が困難。`uuid1` はMAC アドレスを含むため採用しない |
| 条件付き git root 要求 | 全タスクが `read_only: true` の場合は git 外でも動作し、後方互換を維持する |

## git リポジトリ外での書き込みタスクに関する方針

`run_manifest()` の冒頭で、書き込みタスク（`read_only: false`）が 1 件でも存在する場合は `_require_git_root(cwd)` を呼び出す。
`git rev-parse --show-toplevel` が失敗した場合（git 管理外のディレクトリ）は `RunnerError` を送出して処理を中断する。

全タスクが `read_only: true` の場合は `_require_git_root()` を呼ばず、git 外でも従来どおり動作する。

## Alternatives Considered

**`shutil.copytree` によるコピー**: リポジトリが大きい場合（数 GB 等）に著しく遅くなる。git worktree のほうが効率的。

**`/tmp` への配置**: git 管理外のディレクトリに置くことになり、`git worktree remove` での追跡・クリーンアップが困難になる。git リポジトリ配下（`.clade-worktrees/`）に置くほうが明示的で追跡しやすい。

**cleanup 失敗時に `RunnerError` を送出する**: タスク自体は完了しているため、後始末の失敗をタスクエラーとして扱うのは過剰。best-effort での黙殺が適切。

**`uuid1` の使用**: MAC アドレスと時刻から生成されるため、情報漏洩リスクがある。`uuid4` を採用する。

## ADR-003 との関係

本 ADR は ADR-003（「worktree 隔離は v0.3 以降に延期する」）が計画していた実装を完遂するものである。
ADR-003 のステータスを「Superseded (by ADR-007/009)」に更新し、v0.3 で worktree 隔離が実現したことを記録する。

## Consequences

- `runner.py` に以下を追加する:
  - `_WORKTREE_ROOT_NAME = ".clade-worktrees"` 定数
  - `_require_git_root(cwd: Path) -> Path` 関数
  - `_worktree_setup(git_root: Path, task: Task) -> Path` 関数
  - `_worktree_cleanup(git_root: Path, worktree_path: Path) -> None` 関数（best-effort）
  - `_execute_task()` に `git_root: Path | None` 引数を追加し、`read_only=False` のとき worktree 経路を選択
- `.gitignore` に `.clade-worktrees/` を追加する（T10）
- `git worktree` コマンドは `subprocess` 経由で `shell=False`・引数リスト渡しで呼び出す（ADR-001 のセキュリティ方針と整合）
- `task_id` に特殊文字が含まれる場合の考慮は、マニフェストバリデーション（`id` フィールドの型チェック）で str であることを確認しているためリスクは低い。パス traversal 防止のため worktree_path は `git_root` 配下に限定する
