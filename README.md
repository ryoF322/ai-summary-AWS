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

