import boto3
import os
import json
import urllib.parse
from datetime import datetime, timezone

# AWSクライアントの初期化
# グローバルスコープで定義することでウォームスタート時に再利用され
# 毎回の接続オーバーヘッドを削減できる
s3_client         = boto3.client('s3')
transcribe_client = boto3.client('transcribe')
bedrock_client    = boto3.client('bedrock-runtime')
dynamodb          = boto3.resource('dynamodb')

# 環境変数の読み込み
# Lambda コンソールまたは IaC（CDK/Terraform等）で設定する
TRANSCRIPT_BUCKET = os.environ.get('TRANSCRIPT_BUCKET')          # 文字起こし結果が保存されているS3バケット
DYNAMODB_TABLE    = os.environ.get('DYNAMODB_TABLE')             # 要約結果の保存先DynamoDBテーブル名
BEDROCK_MODEL_ID  = os.environ.get(                              # 使用するBedrockモデルID
    'BEDROCK_MODEL_ID',
    'amazon.nova-lite-v1'               
)

# Bedrockへ送るプロンプトテンプレート
# {transcript} に文字起こしテキストが挿入される
# コールセンター向けに「対応品質」「顧客感情」なども要約させる
SUMMARY_PROMPT_TEMPLATE = """
以下はコールセンターの通話録音を文字起こしたテキストです。
話者は spk_0（オペレーター）と spk_1（顧客）の2名です。

<transcript>
{transcript}
</transcript>

上記の通話内容について、以下の項目を日本語で簡潔にまとめてください。

1. **通話目的**: 顧客が電話してきた理由・用件
2. **対応内容**: オペレーターが行った対応・案内内容
3. **解決状況**: 問題が解決したか、未解決であれば次のアクション
4. **顧客感情**: 通話全体を通じた顧客の感情（満足・不満・中立など）
5. **特記事項**: クレームやエスカレーション、フォローアップ要否など

出力はJSON形式で返してください。キーは英語、値は日本語とします。
{{
  "call_purpose"     : "...",
  "response_summary" : "...",
  "resolution_status": "...",
  "customer_sentiment": "...",
  "notes"            : "..."
}}
"""


def lambda_handler(event, context):
    """
    EventBridgeからTranscribeジョブ完了通知を受け取り
    文字起こし結果をS3から取得 → Bedrockで要約 → DynamoDBに保存する。

    EventBridgeのイベントパターン設定例:
    {
      "source": ["aws.transcribe"],
      "detail-type": ["Transcribe Job State Change"],
      "detail": { "TranscriptionJobStatus": ["COMPLETED"] }
    }

    Args:
        event   : EventBridgeイベント（Transcribeジョブの完了通知）
        context : Lambda実行コンテキスト
    """
    print(f"Received event: {json.dumps(event)}")

    # ── EventBridgeイベントからジョブ情報を取得 ──────────────────
    detail   = event.get('detail', {})
    job_name = detail.get('TranscriptionJobName')
    status   = detail.get('TranscriptionJobStatus')

    # ── ジョブが COMPLETED 以外の場合は処理しない ─────────────────
    # FAILEDイベントもEventBridgeから届く可能性があるためガード処理
    if status != 'COMPLETED':
        print(f"Job '{job_name}' status is '{status}'. Skipping.")
        return {'statusCode': 200, 'body': 'Skipped: job not completed'}

    print(f"Processing completed job: {job_name}")

    # ── STEP 1: Transcribeからジョブ詳細を取得 ───────────────────
    # 文字起こし結果JSONのS3 URIを取得するために必要
    transcript_text = fetch_transcript(job_name)

    # ── STEP 2: Bedrockで要約を生成 ──────────────────────────────
    summary = generate_summary(transcript_text)

    # ── STEP 3: 要約結果をDynamoDBに保存 ─────────────────────────
    save_to_dynamodb(job_name, transcript_text, summary)

    print(f"Successfully processed job: {job_name}")
    return {'statusCode': 200, 'body': json.dumps({'job_name': job_name, 'summary': summary})}


# STEP 1: 文字起こし結果の取得

def fetch_transcript(job_name: str) -> str:
    """
    TranscribeジョブのAPIからS3 URIを取得し
    S3から文字起こし結果JSONをダウンロードして
    話者ラベル付きのテキストに整形して返す。

    Args:
        job_name: TranscribeジョブのジョブID（名前）

    Returns:
        話者ラベル付きの文字起こしテキスト（例: "[spk_0]: こんにちは..."）
    """

    # Transcribe APIでジョブの詳細情報（結果ファイルのS3 URI等）を取得
    job_detail = transcribe_client.get_transcription_job(
        TranscriptionJobName=job_name
    )
    transcript_uri = (
        job_detail['TranscriptionJob']['Transcript']['TranscriptFileUri']
    )
    print(f"Transcript URI: {transcript_uri}")

    # S3 URIからバケット名とキーを分解して取得
    # 例: "https://s3.ap-northeast-1.amazonaws.com/my-bucket/transcripts/job.json"
    #     → bucket="my-bucket", key="transcripts/job.json"
    parsed    = urllib.parse.urlparse(transcript_uri)
    path_parts = parsed.path.lstrip('/').split('/', 1)
    bucket    = path_parts[0]
    key       = path_parts[1]

    # S3から文字起こし結果JSONをダウンロード
    s3_response   = s3_client.get_object(Bucket=bucket, Key=key)
    transcript_json = json.loads(s3_response['Body'].read().decode('utf-8'))

    # Transcribe結果JSONから話者ラベル付きテキストを整形
    formatted_text = format_transcript_with_speakers(transcript_json)

    print(f"Transcript length: {len(formatted_text)} characters")
    return formatted_text


def format_transcript_with_speakers(transcript_json: dict) -> str:
    """
    Transcribeの出力JSONを解析し、話者ごとに発言をまとめた
    読みやすい形式のテキストに整形する。

    Transcribeの結果JSONには items（単語単位）と
    speaker_labels（話者ラベル）が別々に格納されているため
    timeで突き合わせて話者ごとに文章を再構築する。

    Args:
        transcript_json: Transcribeが出力した結果JSON

    Returns:
        話者ラベル付きフォーマット済みテキスト
    """
    results = transcript_json.get('results', {})
    items   = results.get('items', [])

    # speaker_labelsが存在しない場合（話者分離オフの場合）は
    # フォールバックとしてシンプルなテキストを返す
    if 'speaker_labels' not in results:
        print("Warning: No speaker labels found. Returning plain transcript.")
        return results.get('transcripts', [{}])[0].get('transcript', '')

    # 単語ごとの開始時刻 → 話者ラベルの対応辞書を構築
    # items の start_time と speaker_labels の start_time を突き合わせる
    speaker_map = {}
    for segment in results['speaker_labels'].get('segments', []):
        speaker = segment['speaker_label']  # 例: "spk_0", "spk_1"
        for item in segment.get('items', []):
            # start_timeをキーにして話者を記録
            speaker_map[item.get('start_time')] = speaker

    # 話者の切り替わりを検知しながら発言を結合して行を構築
    lines          = []
    current_speaker = None
    current_words  = []

    for item in items:
        # 句読点は時刻情報を持たないため前の単語に付加する
        if item['type'] == 'punctuation':
            if current_words:
                current_words[-1] += item['alternatives'][0]['content']
            continue

        start_time = item.get('start_time')
        speaker    = speaker_map.get(start_time, current_speaker)
        word       = item['alternatives'][0]['content']

        if speaker != current_speaker:
            # 話者が切り替わったら前の発言をlinesに追加
            if current_speaker is not None and current_words:
                lines.append(f"[{current_speaker}]: {' '.join(current_words)}")
            # 新しい話者で初期化
            current_speaker = speaker
            current_words   = [word]
        else:
            # 同じ話者の発言は続けて結合
            current_words.append(word)

    # 最後の話者の発言を追加（ループ終了後に残っている分）
    if current_speaker and current_words:
        lines.append(f"[{current_speaker}]: {' '.join(current_words)}")

    return '\n'.join(lines)


# STEP 2: Bedrockによる要約生成

def generate_summary(transcript_text: str) -> dict:
    """
    整形済み文字起こしテキストをBedrockのClaudeモデルに送り
    通話内容の要約をJSON形式で生成して返す。

    Args:
        transcript_text: 話者ラベル付きの文字起こしテキスト

    Returns:
        要約結果のdictionary
        例: {
            "call_purpose": "商品未着の問い合わせ",
            "response_summary": "配送状況を確認し再送手配を案内",
            ...
        }
    """

    # プロンプトテンプレートに文字起こしテキストを埋め込む
    prompt = SUMMARY_PROMPT_TEMPLATE.format(transcript=transcript_text)

    # Bedrock（Claude）へのリクエストボディを構築
    # Anthropic Messages API形式を使用
    request_body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 1024,
        "messages": [
            {
                "role": "user",
                "content": prompt
            }
        ],
        # 出力のランダム性を抑え安定した要約を得るため温度は低めに設定
        "temperature": 0.2,
        "top_p"      : 0.9
    }

    print(f"Calling Bedrock model: {BEDROCK_MODEL_ID}")

    # Bedrockを呼び出して要約を生成
    bedrock_response = bedrock_client.invoke_model(
        modelId     = BEDROCK_MODEL_ID,
        body        = json.dumps(request_body),
        contentType = 'application/json',
        accept      = 'application/json'
    )

    # レスポンスボディをパース
    response_body = json.loads(bedrock_response['body'].read().decode('utf-8'))

    # Claudeの応答テキストを取得（content[0].textに格納されている）
    raw_text = response_body['content'][0]['text']
    print(f"Bedrock raw response: {raw_text}")

    # BedrockはJSON以外の文章を前後に付けることがあるため
    # JSON部分だけを抽出してパースする
    summary = extract_json_from_response(raw_text)

    return summary


def extract_json_from_response(text: str) -> dict:
    """
    BedrockのレスポンステキストからJSON部分のみを抽出してパースする。
    モデルが前置き文章やマークダウンのコードブロック（```json）を
    付けて返す場合があるため、安全に抽出する。

    Args:
        text: Bedrockから返ってきたテキスト全体

    Returns:
        パース済みのdictionary（パース失敗時はraw_textを含む辞書）
    """
    try:
        # まずそのままJSONパースを試みる（最もシンプルなケース）
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # ```json ... ``` のコードブロックを除去してパースを試みる
    if '```json' in text:
        try:
            json_str = text.split('```json')[1].split('```')[0].strip()
            return json.loads(json_str)
        except (IndexError, json.JSONDecodeError):
            pass

    # { } の範囲を探してJSONを抽出する
    try:
        start = text.index('{')
        end   = text.rindex('}') + 1
        return json.loads(text[start:end])
    except (ValueError, json.JSONDecodeError):
        pass

    # すべての抽出に失敗した場合はraw_textとしてそのまま格納
    print("Warning: Could not parse JSON from Bedrock response. Storing raw text.")
    return {'raw_summary': text}


# STEP 3: DynamoDBへの保存

def save_to_dynamodb(job_name: str, transcript_text: str, summary: dict):
    """
    文字起こし結果と要約をDynamoDBに保存する。

    DynamoDBテーブル設計例:
      パーティションキー: job_name (String)
      TTL属性          : ttl（任意。長期保存が不要な場合は自動削除に使う）

    Args:
        job_name       : TranscribeジョブID（DynamoDBのキーとして使用）
        transcript_text: 話者ラベル付きの文字起こしテキスト
        summary        : Bedrockが生成した要約dict
    """
    table = dynamodb.Table(DYNAMODB_TABLE)

    # 保存するアイテムを構築
    item = {
        # パーティションキー: ジョブ名で一意に識別
        'job_name'         : job_name,

        # ISO8601形式のUTC時刻（検索・ソートに利用可能）
        'created_at'       : datetime.now(timezone.utc).isoformat(),

        # Bedrockが生成した要約（ネストしたMap型として保存）
        'summary'          : summary,

        # 文字起こし全文（長文になるため必要に応じてS3 URIに変更も可）
        'transcript'       : transcript_text,

        # 要約処理のステータス（後続処理での確認用）
        'processing_status': 'SUMMARIZED',
    }

    # DynamoDBに書き込み
    # put_itemは同一キーが存在する場合は上書きされる
    table.put_item(Item=item)

    print(f"Saved to DynamoDB table '{DYNAMODB_TABLE}': job_name={job_name}")