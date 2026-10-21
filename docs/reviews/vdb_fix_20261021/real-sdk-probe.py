"""SDK/Lite ORM compatibility probe; isolated synthetic vector only."""
from pathlib import Path

from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections

Path('/acceptance/sdk').mkdir(parents=True, exist_ok=True)
connections.connect(alias='default', uri='/acceptance/sdk/lite.db')
schema = CollectionSchema([
    FieldSchema(name='id', dtype=DataType.VARCHAR, is_primary=True, max_length=64),
    FieldSchema(name='tenant_id', dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name='document_id', dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name='index_id', dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name='embedding', dtype=DataType.FLOAT_VECTOR, dim=8),
])
c = Collection('vdb_sdk_compatibility_probe', schema)
c.create_index('embedding', {'index_type': 'AUTOINDEX', 'metric_type': 'COSINE'})
c.load()
c.upsert([{'id': 'synthetic', 'tenant_id': 'synthetic', 'document_id': 'synthetic',
           'index_id': 'synthetic', 'embedding': [1.0] + [0.0] * 7}])
c.flush()
hits = c.search([[1.0] + [0.0] * 7], 'embedding', {'metric_type': 'COSINE', 'params': {}},
                limit=1, expr='tenant_id == "synthetic"', output_fields=['id'])
assert hits and hits[0] and hits[0][0].id == 'synthetic'
print('Lite 2.5.1 + pymilvus 2.5.11 ORM connect/schema/AUTOINDEX/upsert/search: PASS')
connections.disconnect('default')
