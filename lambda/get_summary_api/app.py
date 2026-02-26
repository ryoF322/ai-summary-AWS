import boto3
import os
import json
from boto3.dynamodb.conditions import Key
from datetime import datetime, timezone
from decimal import Decimal

# AWSクライアントの初期化（ウォームスタート時に再利用）
dynamodb = boto3.resource('dynamodb')

# 環境変数
DYNAMODB_TABLE = os.environ.get('DYNAMODB_TABLE')  # 要約データが保存されているテーブル名

# DynamoDBのDecimal型はJSONシリアライズ不可のため
# float/intに変換するカスタムエンコーダーを定義
class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return int(obj) if obj % 1 == 0 else float(obj)
        return super().default(obj)


def lambda_handler(event, context):
    """
    API Gatewayからのリクエストを受け取り
    DynamoDBから要約データを取得して返す。

    ルーティング:
      GET /calls/{job_name}  → 1件取得
      GET /calls             → 一覧取得（クエリパラメータでフィルタ可能）
    """
    print(f"Received event: {json.dumps(event)}")

    http_method = event.get('httpMethod', '')
    path        = event.get('path', '')
    path_params = event.get('pathParameters') or {}
    query_params = event.get('queryStringParameters') or {}

    try:
        # job_nameがパスパラメータにあれば1件取得、なければ一覧取得
        if path_params.get('job_name'):
            body = get_single_item(path_params['job_name'])
        else:
            body = get_items(query_params)

        return build_response(200, body)

    except ItemNotFoundException as e:
        return build_response(404, {'message': str(e)})

    except Exception as e:
        print(f"Unexpected error: {e}")
        return build_response(500, {'message': 'Internal server error'})


# データ取得処理

def get_single_item(job_name: str) -> dict:
    """
    job_nameをキーにDynamoDBから1件取得する。
    存在しない場合はItemNotFoundExceptionを送出。
    """
    table    = dynamodb.Table(DYNAMODB_TABLE)
    response = table.get_item(Key={'job_name': job_name})
    item     = response.get('Item')

    if not item:
        raise ItemNotFoundException(f"Item not found: {job_name}")

    return item


def get_items(query_params: dict) -> dict:
    """
    DynamoDBから複数件取得する。
    クエリパラメータによるフィルタリングとページネーションに対応。

    Query params:
      limit            : 取得件数上限（デフォルト20、最大100）
      exclusive_start_key: ページネーション用の開始キー（前回レスポンスのnext_keyを使用）
      status           : processing_statusでフィルタリング
    """
    table = dynamodb.Table(DYNAMODB_TABLE)

    # 取得件数の上限（最大100件に制限してコスト・レイテンシを抑制）
    limit = min(int(query_params.get('limit', 20)), 100)

    # scanのオプションを構築
    scan_kwargs = {'Limit': limit}

    # ページネーション: 前回レスポンスのnext_keyを使って次ページを取得
    if query_params.get('exclusive_start_key'):
        scan_kwargs['ExclusiveStartKey'] = json.loads(query_params['exclusive_start_key'])

    # processing_statusによるフィルタリング
    if query_params.get('status'):
        scan_kwargs['FilterExpression'] = 'processing_status = :status'
        scan_kwargs['ExpressionAttributeValues'] = {':status': query_params['status']}

    response = table.scan(**scan_kwargs)

    # ページネーション用の次ページキー（最終ページの場合はNone）
    next_key = response.get('LastEvaluatedKey')

    return {
        'items'   : response.get('Items', []),
        'count'   : response.get('Count', 0),
        'next_key': json.dumps(next_key) if next_key else None
    }


# ユーティリティ

def build_response(status_code: int, body: dict) -> dict:
    """
    API Gatewayが期待するレスポンス形式に整形して返す。
    CORSヘッダーを付与することでブラウザからの直接呼び出しにも対応。
    """
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type' : 'application/json',
            'Access-Control-Allow-Origin' : '*',   # 必要に応じてドメインを制限
            'Access-Control-Allow-Methods': 'GET, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization'
        },
        # DecimalEncoderでDynamoDBのDecimal型を安全にシリアライズ
        'body': json.dumps(body, cls=DecimalEncoder, ensure_ascii=False)
    }


class ItemNotFoundException(Exception):
    """DynamoDBにアイテムが存在しない場合のカスタム例外"""
    pass