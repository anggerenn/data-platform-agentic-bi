import os
from openai import OpenAI as OpenAIClient
from vanna.chromadb import ChromaDB_VectorStore
from vanna.openai import OpenAI_Chat


class MyVanna(ChromaDB_VectorStore, OpenAI_Chat):
    def __init__(self, client=None, config=None):
        ChromaDB_VectorStore.__init__(self, config=config)
        OpenAI_Chat.__init__(self, client=client, config=config)


def get_vanna():
    deepseek_client = OpenAIClient(
        api_key=os.environ['DEEPSEEK_API_KEY'],
        base_url='https://api.deepseek.com',
    )
    vn = MyVanna(
        client=deepseek_client,
        config={
            'model': os.environ.get('VANNA_MODEL', 'deepseek-chat'),
            'path': os.path.expanduser(os.environ.get('CHROMADB_PATH', '~/data/vanna-chromadb')),
        },
    )
    vn.connect_to_clickhouse(
        host=os.environ.get('CLICKHOUSE_HOST', 'localhost'),
        port=int(os.environ.get('CLICKHOUSE_PORT', '8123')),
        user=os.environ.get('CLICKHOUSE_USER', 'default'),
        password=os.environ['CLICKHOUSE_PASSWORD'],
        dbname='transformed_marts',
        # Connection-level read-only: equivalent to DuckDB's access_mode=read_only.
        # Enforces SELECT-only at the HTTP session level, independent of user credentials.
        settings={'readonly': '1'},
    )
    return vn
