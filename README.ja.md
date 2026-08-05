# Astra

**「実際に仕事を完了させる」ための、汎用 AI ネイティブ Agent プラットフォーム。**

[English](README.md) · [简体中文](README.zh-CN.md) · [日本語](README.ja.md)

Astra は、最先端の大規模言語モデル上に構築されたオープンソースの Agent Runtime です。ユーザーの目標を永続的な Run に変換し、コンテキストの理解、作業計画、ツール選択、操作の実行、結果の検証、監査可能な履歴の保存までを一貫して行います。

Astra はコーディング専用のチャットアシスタントではなく、汎用的なタスク OS を目指しています。Web 情報、ファイル、サンドボックス計算、構造化 Artifact、Memory、スケジュールタスク、統制された Subagent を扱いながら、権限境界とエビデンスを可視化します。

> Astra の現在のバージョンは `v0.1.0` で、継続的に進化しています。リリース間でインターフェースやデプロイ仕様が変更される場合があります。

## Astra の特長

- **クイック実行と信頼実行** — 日常的な作業には軽量な Agent Loop を、複雑な作業にはバージョン管理された Plan DAG と厳格な検証・完了ゲートを利用できます。
- **能力ベースのツールシステム** — Plan は必要な能力だけを記述します。Runtime は実行時に利用可能な実装を解決し、ポリシー、権限、リスク、承認、予算を確認します。
- **設計段階からの追跡可能性** — Turn、ツール呼び出し、Artifact、Evidence、Plan の改訂、検証結果が Run のタイムラインに保存されます。
- **統制された Memory と Identity** — Agent Profile、ユーザー Memory、実行権限、ツール Authority を分離し、Run スナップショットによって過去の動作を再現できます。
- **安全な委任** — Subagent には制限された目標、予算、権限、分離された実行コンテキストが与えられ、Supervisor が Join、キャンセル、復旧を管理します。
- **拡張可能な Runtime** — 組み込みの Web・チャート機能、サンドボックス処理、Skills、プラグイン、スケジュールタスク、OpenAI-compatible モデルを利用できます。

## 仕組み

```text
ユーザーの目標
   ↓
Task / 永続的な Run
   ↓
クイック Agent Loop  または  バージョン管理された信頼 Plan DAG
   ↓
能力の解決 → ポリシーと権限ゲート → ツール実行
   ↓
Evidence と Artifact → 評価 → 完了検証
   ↓
回答 + 監査可能なタイムライン + オプションの Memory
```

両モードは同じツール、Workspace、承認、Artifact、セキュリティパイプラインを共有します。信頼モードでは、標準化された計画、Plan の改訂、依存関係に基づく実行、より厳格な完了検証が追加されますが、モデルの結論が常に正しいことを保証するものではありません。

## クイックスタート

### Release からインストール

安定版の [GitHub Releases](https://github.com/tommyCheese/Astra/releases) には、Compose バンドル、SHA-256 チェックサム、SPDX SBOM、ビルド来歴を証明した `linux/amd64` / `linux/arm64` イメージが含まれます。

```bash
tar -xzf astra-v0.1.0.tar.gz
cd astra-v0.1.0
./install.sh
```

<http://127.0.0.1:8080> を開きます。デフォルトでは localhost のみにバインドし、決定論的な mock モデルを使用するため、API キーなしで動作確認できます。

### ソースから実行

必要環境：Python 3.10+ および Node.js/npm。

```bash
git clone https://github.com/tommyCheese/Astra.git
cd Astra
./start.sh
```

Windows では `start.bat` を実行してください。初回起動時に不足している依存関係のインストール、データベース移行、`backend/.env` がない場合のローカル mock 設定作成が行われます。

フロントエンドは <http://localhost:5173> で起動し、API リクエストを <http://localhost:8000/api> にプロキシします。

## 実モデルを接続する

Astra はデフォルトで mock provider を使用します。

```dotenv
MODEL_PROVIDER=mock
MODEL_NAME=mock-web-query
```

OpenAI-compatible API を接続するには、`backend/.env` を更新します。

```dotenv
MODEL_PROVIDER=openai
MODEL_NAME=<model-name>
MODEL_API_KEY=<your-api-key>
MODEL_BASE_URL=https://api.openai.com/v1
```

ツール機能は「設定 → ツール」から動的に設定できます。また、identity ベースの
`/api/tools/{tool_name}/state` と `/api/tool-providers/{provider_id}/state` API も利用できます。
固定の `TOOL_<NAME>_ENABLED` 環境変数はサポートされません。

チャート実行はデフォルトで無効です。明示的に有効にした場合も、任意コードは API プロセス内ではなく Docker 経由で実行されます。信頼できるローカル環境の外部に Astra を公開する前に、Runtime のセキュリティ境界を確認してください。

## アーキテクチャ

| レイヤー | 主な役割 |
| --- | --- |
| React + TypeScript フロントエンド | チャット、Run のストリーミング状態、Plan グラフ、Artifact、Memory、Skills、スケジュール、監査画面 |
| FastAPI バックエンド | API、モデルクライアント、計画、Run ライフサイクル、永続化、スケジューリング、ストリーミングイベント |
| Agent Runtime | 能力解決、ポリシー、権限、承認、リフレクション、完了ゲート、Subagent の監督 |
| ツールとサンドボックス | Web 操作、ファイル・Artifact ワークフロー、チャート、分離計算、プラグイン機能 |
| 永続化 | 単一バックエンドのローカル環境では SQLite、複数レプリカ構成では PostgreSQL |

## ドキュメント

- [ドキュメントセンター](docs/README.md)
- [システム詳細設計](docs/astra-system-detailed-design.md)
- [信頼実行グラフ](docs/trusted-execution-graph.md)
- [統制された Subagent Runtime](docs/governed-subagent-runtime.md)
- [Agent Skills](docs/agent-skills.md)
- [Deep Memory、AutoDream、Agent Evolution](docs/deep-memory-autodream-evolution.md)
- [Token 使用量とパフォーマンス](docs/token-performance.md)
- [リリースガイド](docs/releasing.md)
- [デプロイガイド](deploy/README.md)

エンジニアリングドキュメントの多くは、現在簡体字中国語で記述されています。改善や翻訳へのコントリビューションを歓迎します。

## 開発

バックエンドのテストを実行します。

```bash
cd backend
pip install -e ".[dev]"
pytest -q
```

フロントエンドを検証します。

```bash
cd frontend
npm ci
npm run lint
npm test
npm run build
```

エンドツーエンドの回答レイテンシを計測するか、同一条件のケースでクイックモードと信頼モードを比較します。

```bash
cd backend
python -m benchmarks.qa_latency --runs 20 --warmup 2
python -m benchmarks.mode_performance --runs-per-case 3 --warmup 1
```

比較ベンチマークは provider が報告した usage を読み取り、不明な Token 数をゼロとして扱いません。provider のネットワークや推論の変動を分離するには、`python -m benchmarks.model_stub` で決定論的な OpenAI-compatible ストリーミングエンドポイントを起動できます。詳細な方法は [Token 使用量とパフォーマンス](docs/token-performance.md)を参照してください。

## セキュリティとデプロイに関する注意

- Compose のエンドポイントはデフォルトで `127.0.0.1` のみにバインドします。ネットワークへ公開する前に、認証付き TLS リバースプロキシを設定してください。
- Docker socket はホストレベルの Docker 管理権限を持ちます。信頼できるローカルの Astra バックエンドだけにマウントし、有効なサンドボックス機能を確認してください。
- 組み込みの SQLite 構成は単一バックエンドプロセス向けです。複数のバックエンドレプリカを実行する前に PostgreSQL を設定してください。
- アップグレードやデータ保持ポリシーの変更前に、デプロイ先のデータディレクトリをバックアップしてください。

## ライセンス

Astra は [Apache License 2.0](LICENSE) の下で公開されています。
