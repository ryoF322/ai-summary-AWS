# Amazon Connect 通話自動文字起こし・要約基盤

## システム概要
Amazon Connectの通話録音データを自動で文字起こしし、
Amazon Bedrockを利用して要約を生成、
DynamoDBへ保存・API経由で参照可能にするシステムです。


## 目的
- オペレーターの後処理時間削減
- 通話内容のナレッジ化
- 応対品質分析基盤の構築

通話は最大30分を想定し、  
TranscribeおよびLLM要約処理を含めてLambdaの制限内で完結する設計としています。

## 処理フロー

### 要約生成フロー

1. Amazon Connect → 通話録音
2. 録音データをS3へ保存
3. S3イベントでLambda起動
4. Transcribeジョブ開始
5. Transcribe完了をEventBridgeで検知
6. Lambdaで文字起こし取得
7. Bedrockで要約生成
8. DynamoDBへ保存

### 参照フロー

1. Cognitoログイン
2. JWT取得
3. API呼び出し
4. API Gatewayで認証
5. Lambda実行
6. DynamoDBから取得
7. 要約表示

## 採用システム

### Amazon S3
- 通話録音保存
- SSE-KMSによる暗号化

### AWS Lambda
- S3イベントトリガーでTranscribe開始
- Transcribe完了後の要約処理
- API取得処理

責務分離した3つのLambda構成としています。

### Amazon Transcribe
- 非同期ジョブによる文字起こし
- 結果はJSON形式でS3へ保存

### Amazon EventBridge
- Transcribe完了イベントを受信
- ポーリングを回避し疎結合なイベント駆動設計を実現

### Amazon Bedrock
- LLMによる自然言語要約
- プロンプト設計で出力品質制御可能

### Amazon DynamoDB
- スケーラブルなNoSQL
- 低レイテンシで要約データ取得

### Amazon Cognito
- JWTベース認証
- API Gatewayと統合
- ユーザー単位アクセス制御

### API Gateway
- エンドポイント提供
- Cognito Authorizerで認証
- Lambdaへリクエスト転送

## セキュリティ設計

- S3: Private + SSE-KMS
- IAM: 最小権限ポリシー
- API Gateway: Cognito Authorizer
- Bedrock: IAM制御
- 音声データをログに出力しない設計

## 技術的ポイント

- イベント駆動アーキテクチャ
- 非同期処理設計
- LLM統合設計
- 認証・認可設計
- スケーラブルなNoSQL設計












