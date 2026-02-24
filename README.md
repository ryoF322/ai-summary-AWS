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
