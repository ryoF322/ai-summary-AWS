import json #JSON形式のデータを扱うためのライブラリ
import os #環境変数を取得するために使用
import urllib.parse #URLエンコードされた文字列をデコードするために使用
import boto3 #AWSサービスをPythonから操作するためのライブラリ
from datetime import datetime

# AWSクライアントの初期化
s3_client = boto3.client('s3')
transcribe_client = boto3.client('transcribe')

# 環境変数の読み込み
# Lambda コンソールまたは IaC（CDK/Terraform等）で設定する
OUTPUT_BUCKET = os.environ.get('TRANSCRIBE_OUTPUT_BUCKET')  # 文字起こし結果の保存先バケット
OUTPUT_PREFIX = os.environ.get('OUTPUT_PREFIX', 'transcripts/')  # 結果ファイルのS3プレフィックス

def lambda_handler(event, context):
    #S3のObjectCreatedイベントをトリガーに呼び出されるメイン関数。
    #S3にアップロードされた音声ファイルごとにTranscribeジョブを開始する。
    print(f"Received event: {json.dumps(event)}")

    for record in event['Records']:
        # S3イベントからバケット名とオブジェクトキーを取得
        bucket_name = record['s3']['bucket']['name']
        object_key = urllib.parse.unquote_plus(record['s3']['object']['key'])
        
        print(f"Processing: s3://{bucket_name}/{object_key}")
        
        # 音声ファイルのみ処理（拡張子チェック）
        supported_formats = ['.mp3', '.mp4', '.wav', '.flac', '.ogg', '.amr', '.webm']
        file_ext = os.path.splitext(object_key)[1].lower()
        
        if file_ext not in supported_formats:
            print(f"Unsupported format: {file_ext}. Skipping.")
            continue

        # Transcribeジョブ名（一意にする）
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
        safe_key = object_key.replace('/', '-').replace('.', '-')
        job_name = f"transcribe-{safe_key}-{timestamp}"[:200]  # 最大200文字制限

        # メディアフォーマットの判定
        format_map = {
            '.mp3': 'mp3',
            '.mp4': 'mp4',
            '.wav': 'wav',
            '.flac': 'flac',
            '.ogg': 'ogg',
            '.amr': 'amr',
            '.webm': 'webm'
        }
        media_format = format_map[file_ext]

        # S3 URIの生成
        media_uri = f"s3://{bucket_name}/{object_key}"

        # Transcribeジョブの開始
        response = transcribe_client.start_transcription_job(
            TranscriptionJobName=job_name,
            Media={'MediaFileUri': media_uri},
            MediaFormat=format_map[file_ext],
            LanguageCode='ja-JP',  # 日本語
            OutputBucketName=OUTPUT_BUCKET,
            OutputKey=f"{OUTPUT_PREFIX}{job_name}.json",
            Settings={
                'ShowSpeakerLabels': True,   # 話者分離（オペレーター/顧客の区別）
                'MaxSpeakerLabels': 2,        # Amazon Connectは基本2者通話
                'ShowAlternatives': False,
            },
        )

        job_info = response['TranscriptionJob']
        print(f"Started Transcription Job: {job_name}")
        print(f"Job Status: {job_info['TranscriptionJobStatus']}")
        
    return {
        'statusCode': 200,
        'body': json.dumps({'message': 'Transcription job(s) started successfully'})
    }