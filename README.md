# Amazon Connect 通話自動文字起こし・要約基盤

## システム概要
Amazon Connectの通話録音データを自動で文字起こしし、
Amazon Bedrockを利用して要約を生成、
DynamoDBへ保存・API経由で参照可能にするシステムです。
