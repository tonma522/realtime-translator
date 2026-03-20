# Plan: レビュー指摘 v2 修正

**Generated**: 2026-03-07
**Estimated Complexity**: Low

## Overview
直前の `/review` で発見された4つの問題を修正する。1件はクリティカル（実行不能バグ）、2件は中程度（実装単純化/正確性）、1件は軽微（コード品質）。

## Prerequisites
- Python 3.11+
- `pytest` インストール済み
- 現行テスト 43件パス済み

---

## Sprint 1: クリティカル修正 + コード品質
**Goal**: import 回帰の修正と不要コードの除去
**Demo/Validation**:
- `python -c "from realtime_translator.api import ApiWorker"` が成功
- `pytest -q` 全件パス（import 回帰テスト含む）

### Task 1.0: バグ存在確認
- **Location**: `realtime_translator/api.py`
- **Description**: `python -c "from realtime_translator.api import ApiWorker"` を実行し、`IndentationError` が発生することを確認する。発生しない場合は Task 1.1 をスキップ
- **Complexity**: 1
- **Dependencies**: なし
- **Validation**: コマンド実行結果

### Task 1.1: api.py のインデント修正と import 回帰テスト追加
- **Location**: `realtime_translator/api.py:39-40`, `tests/test_api.py`（新規）
- **Description**: コメント追加時にインデントが崩れた `self._req_queue` 行を修正し、`realtime_translator.api` を import する実行経路が壊れている状態を解消する。あわせて `realtime_translator.api` の import 成功を検証する回帰テストを追加する（`realtime_translator.app` は tkinter 依存のため回帰テスト対象外）
- **Complexity**: 1
- **Dependencies**: Task 1.0
- **Acceptance Criteria**:
  - `self._req_queue` の行が他のインスタンス変数と同じインデントレベル（8スペース）
  - `python -c "from realtime_translator.api import ApiWorker"` が成功
  - `realtime_translator.api` の import 成功を検証する回帰テストが追加される
- **Validation**:
  - `python -c "from realtime_translator.api import ApiWorker"`
  - 追加した import 回帰テストの実行
  - `pytest -q`

### Task 1.2: test_config.py の未使用 MagicMock import 削除
- **Location**: `tests/test_config.py:2`
- **Description**: `from unittest.mock import patch, MagicMock` から未使用の `MagicMock` を削除し、`from unittest.mock import patch` にする。独立コミット可能な atomic task
- **Complexity**: 1
- **Dependencies**: なし
- **Acceptance Criteria**:
  - `MagicMock` が import されていない
  - `tests/test_config.py` が単独で全パスする
- **Validation**:
  - `pytest tests/test_config.py -q`

---

## Sprint 2: 実装単純化・正確性改善
**Goal**: _keyring_usable のキャッシュ化と audio_utils の不要中間構造削除
**Demo/Validation**:
- keyring プローブがプロセスライフタイムで1回だけ実行される
- RMS 計算が中間 array を構築しない
- `pytest -q` が 47件以上パス（新規テスト含む）

### Task 2.1: _keyring_usable() の結果をキャッシュ
- **Location**: `realtime_translator/config.py:18-27`, `tests/test_config.py`
- **Description**:
  - `_keyring_usable_cache: bool | None = None` をモジュールレベルに追加
  - `_keyring_usable()` 内でキャッシュをチェックし、`None` の場合のみプローブを実行。結果をキャッシュに保存
  - `functools.lru_cache` は使わず明示的なセンチネル変数で管理（テストから確実にリセットできるようにするため）
  - テスト順序依存を防ぐため、`autouse=True` fixture で各テストの setup/teardown の両方で `config_module._keyring_usable_cache = None` を実行する
  - **注意**: キャッシュはプロセスライフタイムで永続。keyring backend がセッション中に停止した場合でもキャッシュは `True` を返す（デスクトップGUIアプリとして許容）
- **Complexity**: 3
- **Dependencies**: なし
- **Acceptance Criteria**:
  - `save_config` + `load_config` の1回のフローで `service == _KEYRING_SERVICE and username == "__probe__"` の `keyring.get_password` 呼び出しが最大1回
  - keyring 利用不可時のフォールバック動作が変わらない
  - `tmp_config` を使わないテストを含め、`tests/test_config.py` 全体でキャッシュ残留による順序依存が発生しない
  - 既存テストが全パス
- **テストの注意点**:
  - `autouse` fixture で各テストの前後に `config_module._keyring_usable_cache = None` を実行すること（`tmp_config` teardown だけでは `test_keyring_save_load` 等に効かない）
  - 新規テスト3件:
    1. `_keyring_usable()` の2回呼び出しで `__probe__` プローブが1回だけ呼ばれる
    2. プローブ失敗時のキャッシュが `False` を維持する
    3. `_keyring_usable_cache = None` でリセット後に再プローブが走る
- **Validation**:
  - `pytest tests/test_config.py -q`

### Task 2.2: audio_utils.py の不要 array("q") 中間構造を削除
- **Location**: `realtime_translator/audio_utils.py:10-21`
- **Description**:
  - `array("q", (s * s for s in samples))` + `sum(sq)` → `sum(s * s for s in samples)` に戻す（メモリ・実装の単純化）
  - 削除対象コメント: L19-20 の `# sum of squares を...` と `# array("q") で...` はコードと齟齬が出るため削除
  - docstring を `"""生PCMフレームのRMS振幅がthreshold以下ならTrue"""` にシンプル化（「高速化」記述を削除）
  - **注意**: `import array` と `array.array("h", ...)` (L18) は引き続き使用するため、`import array` は残すこと
- **Complexity**: 2
- **Dependencies**: なし
- **Acceptance Criteria**:
  - 中間 `array("q")` が使われていない
  - `import array` が残っている
  - RMS 計算結果が同一（既存テスト全パス）
- **Validation**:
  - `pytest tests/test_audio.py -q`

### Task 2.3: 最終検証
- **Location**: 変更した全ファイル
- **Description**: `pytest -q` で全テストパスを確認。lint ツールが導入済みなら `ruff check` / `flake8` で確認、未導入ならスキップして記録
- **Complexity**: 1
- **Dependencies**: Task 2.1, Task 2.2
- **Acceptance Criteria**:
  - 全テストパス（47件以上）
- **Validation**: コマンド実行結果

---

## Testing Strategy
- Sprint 1: `pytest -q` 全件パス + `realtime_translator.api` を直接 import する回帰テストが追加され、単独実行でもパス
- Sprint 2: `pytest -q` 47件以上パス（import 回帰1件 + keyring キャッシュ3件）

## Potential Risks & Gotchas
- **Task 1.1 は限定的ブロッカー**: api.py のインデント崩れにより `realtime_translator.api` を import する実行経路が壊れている。既存 `pytest -q` は通るため「全テストのブロッカー」ではないが、import 回帰の修正タスクとして最優先で扱うこと
- **Task 2.1 のキャッシュリセット**: `autouse` fixture で各テストの前後に `_keyring_usable_cache = None` をリセットすること。`tmp_config` teardown だけでは `test_keyring_save_load` 等 `tmp_config` 非依存テストに効かない
- **Task 2.1 のキャッシュ永続性**: プロセスライフタイムで永続のため、keyring backend がセッション中に停止しても再プローブされない。デスクトップGUIアプリとして許容するトレードオフ
- **Task 2.2 の import array 保持**: `array.array("h", ...)` は引き続き PCM デコードに使用するため、`import array` を誤って削除しないこと
- **interval 正規化の二重実装**: `config.py:_sanitize_interval()` と `app.py:_load_config()` の両方に `(3, 5, 8)` ガードがある。将来許容値を変更する際に片方だけ更新されるリスクあり。本計画のスコープ外だが認識しておくこと
- **save_config() の破壊的 dict 変更**: `data.pop("api_key", "")` で呼び出し元の dict が変更される。現状は毎回新しい dict を組んでいるため顕在化していないが、config 周りを触る際は注意

## Rollback Plan
- 各タスクは独立した1コミットで管理。`git revert` で個別に巻き戻し可能

---
## Codex Review Notes
*(auto-appended by codex-review skill)*

### Incorporated Feedback
- **Task 1.1 のブロッカー定義修正**: 「全テストのブロッカー」→「限定的ブロッカー（import 経路が壊れている）」に修正。import 回帰テスト追加を完了条件に含めた
- **Task 1.1 の回帰テスト対象**: `realtime_translator.api` を優先（`.app` は tkinter 依存で環境ノイズが多い）
- **Task 1.2 の依存除去**: `Task 1.1` 依存を削除し、独立コミット可能な atomic task に変更
- **Task 2.1 の autouse fixture**: `tmp_config` teardown ではなく `autouse` fixture で全テストのキャッシュリセットを保証
- **Task 2.1 のプローブ判定条件明確化**: `keyring.get_password("__probe__")` → `service == _KEYRING_SERVICE and username == "__probe__"` の呼び出し回数に具体化
- **Task 2.2 の名称修正**: 「パフォーマンス改善」→「実装単純化」に変更（速度改善は保証されない）
- **interval 正規化の二重実装リスク追加**: `config.py` と `app.py` の両方にガードがある将来のドリフトリスクを Gotchas に追記
- **save_config() の破壊的 dict 変更リスク追加**: `data.pop()` による呼び出し元への副作用を Gotchas に追記

### Skipped Feedback
- `test_api_key_not_in_json` の `_KEYRING_AVAILABLE` vs `_keyring_usable` 分岐修正: 既存テストの変更はこの修正計画のスコープ外
- lint ツールの導入要否: 本計画はバグ修正が主目的。lint ツール導入は別タスクとして扱う
