"""BigQuery: rules corpus with vector search, and the findings analytics sink.

The application owns table DDL (CREATE ... IF NOT EXISTS) so that `plimsoll rules ingest` works
against a dataset with no tables — the cold-start path. Terraform owns only the dataset.
"""

import io
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from google.api_core import exceptions as gexc
from google.cloud import bigquery

from sar_plimsoll.rules.csv_parser import RuleRow
from sar_plimsoll.storage.gcp_clients import Lazy, shared_credentials
from sar_plimsoll.storage.interfaces import RuleHit, SearchResult, StoredRule

log = logging.getLogger(__name__)

RULES_TABLE = "rules"
FINDINGS_TABLE = "findings"
REVIEWS_TABLE = "reviews"
VECTOR_INDEX = "rules_embedding_idx"


class _LazyClient:
    _lazy: Lazy

    @property
    def _client(self) -> bigquery.Client:
        return self._lazy.get()


class BigQueryRulesRepository(_LazyClient):
    def __init__(self, project: str, dataset: str):
        self._lazy = Lazy(
            lambda: bigquery.Client(project=project, credentials=shared_credentials())
        )
        self._ds = f"`{project}.{dataset}`"
        self._table = f"`{project}.{dataset}.{RULES_TABLE}`"
        self._table_id = f"{project}.{dataset}.{RULES_TABLE}"
        self._dataset_id = f"{project}.{dataset}"

    def _query(self, sql: str, params: list | None = None) -> bigquery.QueryJob:
        job = self._client.query(
            sql, job_config=bigquery.QueryJobConfig(query_parameters=params or [])
        )
        job.result()
        return job

    def ensure_schema(self, dimensions: int) -> None:
        self._query(
            f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
              id STRING NOT NULL,
              type STRING,
              dimension STRING,
              description STRING NOT NULL,
              content_hash STRING NOT NULL,
              embedding ARRAY<FLOAT64>,
              embedding_model STRING,
              updated_at TIMESTAMP
            )
            OPTIONS (description = 'Historical review rules; embedding dim {int(dimensions)}')
            """
        )
        for ddl in analytics_ddl(self._dataset_id):
            self._query(ddl)

    def fingerprints(self) -> dict[str, tuple[str, str | None]]:
        # Column pruning: never scans the embedding column.
        rows = self._query(f"SELECT id, content_hash, embedding_model FROM {self._table}").result()
        return {r["id"]: (r["content_hash"], r["embedding_model"]) for r in rows}

    def upsert(self, rows: list[RuleRow], embeddings: dict[str, list[float]], model: str) -> None:
        if not rows:
            return
        staging = f"{self._dataset_id}._rules_staging_{uuid.uuid4().hex[:12]}"
        payload = rules_ndjson(rows, embeddings, model, datetime.now(UTC).isoformat())
        schema = self._client.get_table(self._table_id).schema
        # Load jobs are free; streaming 30k rows would not be. MERGE then applies them atomically.
        load = self._client.load_table_from_file(
            payload,
            staging,
            job_config=bigquery.LoadJobConfig(
                schema=schema,
                source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON,
                write_disposition="WRITE_TRUNCATE",
            ),
        )
        load.result()
        try:
            self._query(
                f"""
                MERGE {self._table} T
                USING `{staging}` S ON T.id = S.id
                WHEN MATCHED AND (T.content_hash != S.content_hash
                                  OR IFNULL(T.embedding_model, '') != S.embedding_model) THEN UPDATE SET
                  type = S.type, dimension = S.dimension, description = S.description,
                  content_hash = S.content_hash, embedding = S.embedding,
                  embedding_model = S.embedding_model, updated_at = S.updated_at
                WHEN NOT MATCHED THEN INSERT ROW
                """
            )
        finally:
            self._client.delete_table(staging, not_found_ok=True)

    def refresh_index(self) -> None:
        # IVF index; BigQuery only populates it once the table is large enough, and VECTOR_SEARCH
        # falls back to exact brute-force search until then — which is correct, just unindexed.
        try:
            self._query(
                f"""
                CREATE VECTOR INDEX IF NOT EXISTS {VECTOR_INDEX}
                ON {self._table}(embedding)
                OPTIONS (index_type = 'IVF', distance_type = 'COSINE')
                """
            )
        except gexc.BadRequest as exc:
            log.warning(
                "vector index not created; brute-force search in use",
                extra={"error": str(exc)[:300]},
            )

    def search(self, vectors: list[list[float]], k: int) -> SearchResult:
        if not vectors:
            return SearchResult(hits=[])
        queries = json.dumps([{"q": i, "e": v} for i, v in enumerate(vectors)])
        sql = f"""
            SELECT query.q AS q, base.id, base.type, base.dimension, base.description, distance
            FROM VECTOR_SEARCH(
              TABLE {self._table}, 'embedding',
              (
                SELECT
                  CAST(JSON_VALUE(item, '$.q') AS INT64) AS q,
                  ARRAY(SELECT CAST(x AS FLOAT64) FROM UNNEST(JSON_VALUE_ARRAY(item, '$.e')) AS x)
                    AS embedding
                FROM UNNEST(JSON_QUERY_ARRAY(@queries)) AS item
              ),
              'embedding',
              top_k => {int(k)},
              distance_type => 'COSINE'
            )
        """
        job = self._query(sql, [bigquery.ScalarQueryParameter("queries", "STRING", queries)])
        hits: list[list[RuleHit]] = [[] for _ in vectors]
        for row in job.result():
            hits[row["q"]].append(
                RuleHit(
                    StoredRule(row["id"], row["type"], row["dimension"], row["description"]),
                    float(row["distance"]),
                )
            )
        for h in hits:
            h.sort(key=lambda x: (x.distance, x.rule.id))
        return SearchResult(hits=hits, bytes_billed=job.total_bytes_billed or 0)


def rules_ndjson(
    rows: list[RuleRow], embeddings: dict[str, list[float]], model: str, updated_at: str
) -> io.BytesIO:
    """Serialise rows one line at a time.

    Building a list of dicts first (as load_table_from_json does) holds a second full copy of every
    768-float vector as Python objects — ~25 KB per rule — which exhausted a 512 MiB instance at 5k
    rules. Writing each line straight into the buffer keeps only the compact JSON text.
    """
    buf = io.BytesIO()
    for r in rows:
        head = json.dumps(
            {
                "id": r.id,
                "type": r.type,
                "dimension": r.dimension,
                "description": r.description,
                "content_hash": r.content_hash,
                "embedding_model": model,
                "updated_at": updated_at,
            },
            ensure_ascii=False,
        )
        # 6 decimals halves the payload; cosine ranking is unaffected at 768 dimensions.
        vector = ",".join(f"{x:.6f}" for x in embeddings[r.id])
        buf.write(f'{head[:-1]}, "embedding": [{vector}]}}\n'.encode())
    buf.seek(0)
    return buf


def analytics_ddl(dataset_id: str) -> list[str]:
    return [
        f"""
        CREATE TABLE IF NOT EXISTS `{dataset_id}.{FINDINGS_TABLE}` (
          review_id STRING, uid STRING, created_at TIMESTAMP, language STRING,
          rubric_version STRING, rules_corpus_version STRING, finding_id STRING,
          dimension STRING, severity STRING, confidence FLOAT64, model_tier STRING,
          grounded_rule_ids ARRAY<STRING>, message STRING
        )
        PARTITION BY DATE(created_at)
        CLUSTER BY uid, dimension
        """,
        f"""
        CREATE TABLE IF NOT EXISTS `{dataset_id}.{REVIEWS_TABLE}` (
          review_id STRING, uid STRING, mode STRING, status STRING,
          created_at TIMESTAMP, completed_at TIMESTAMP, filename STRING, language STRING,
          line_count INT64, score FLOAT64, findings INT64, critical INT64, high INT64,
          medium INT64, low INT64, grounded_findings INT64, cache_hit BOOL, escalated BOOL,
          escalation_reasons ARRAY<STRING>, triage_calls INT64, escalation_calls INT64,
          triage_input_tokens INT64, triage_output_tokens INT64,
          escalation_input_tokens INT64, escalation_output_tokens INT64, thinking_tokens INT64,
          cost_usd FLOAT64, cost_usd_standard FLOAT64, wall_ms INT64,
          rubric_version STRING, prompt_version STRING, rules_corpus_version STRING,
          triage_model STRING, escalation_model STRING, run_id STRING
        )
        PARTITION BY DATE(created_at)
        CLUSTER BY mode, uid
        """,
        # Additive migrations for tables created by earlier versions.
        f"ALTER TABLE `{dataset_id}.{REVIEWS_TABLE}` ADD COLUMN IF NOT EXISTS run_id STRING",
    ]


class BigQueryAnalytics(_LazyClient):
    """Streams review and finding rows; answers cost and recurring-issue queries.

    Tables are created on first write if missing, so analytics never depends on a prior ingest.
    Query results are cached in-process briefly: dashboards poll, and each BigQuery query bills
    a 10 MB minimum.
    """

    def __init__(self, project: str, dataset: str, cache_s: int = 60):
        self._lazy = Lazy(
            lambda: bigquery.Client(project=project, credentials=shared_credentials())
        )
        self._dataset_id = f"{project}.{dataset}"
        self._cache_s = cache_s
        self._cache: dict[tuple, tuple[float, Any]] = {}
        self._schema_ready = False

    # ------------------------------------------------------------------ writes
    def write_review(self, row: dict[str, Any]) -> None:
        self._insert(REVIEWS_TABLE, [row])

    def write_findings(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            self._insert(FINDINGS_TABLE, rows)

    def _insert(self, table: str, rows: list[dict[str, Any]]) -> None:
        table_id = f"{self._dataset_id}.{table}"
        try:
            errors = self._client.insert_rows_json(table_id, rows)
        except gexc.NotFound:
            self.ensure_schema()
            errors = self._client.insert_rows_json(table_id, rows)
        if errors and "no such field" in str(errors):
            # A column added by a newer version: migrate once, then retry.
            self.ensure_schema(force=True)
            errors = self._client.insert_rows_json(table_id, rows)
        if errors:
            raise RuntimeError(f"{table} insert errors: {errors[:3]}")

    def ensure_schema(self, force: bool = False) -> None:
        if self._schema_ready and not force:
            return
        for ddl in analytics_ddl(self._dataset_id):
            self._client.query(ddl).result()
        self._schema_ready = True

    # ------------------------------------------------------------------ queries
    def _rows(self, key: tuple, sql: str, params: list) -> list[dict[str, Any]]:
        import time

        now = time.monotonic()
        hit = self._cache.get(key)
        if hit and now - hit[0] < self._cache_s:
            return hit[1]
        try:
            job = self._client.query(
                sql, job_config=bigquery.QueryJobConfig(query_parameters=params)
            )
            rows = [dict(r) for r in job.result()]
        except gexc.NotFound:
            rows = []  # analytics tables not created yet: nothing reviewed so far
        self._cache[key] = (now, rows)
        return rows

    def mode_aggregates(self, uid: str | None, days: int):
        from sar_plimsoll.analytics.stats import ModeAggregate

        sql = f"""
            SELECT mode,
                   COUNT(*) AS reviews,
                   COUNTIF(cache_hit) AS cache_hits,
                   COUNTIF(escalated AND NOT cache_hit) AS escalated,
                   IFNULL(SUM(cost_usd), 0) AS cost_usd,
                   IFNULL(SUM(cost_usd_standard), 0) AS cost_usd_standard,
                   APPROX_QUANTILES(IF(cache_hit, NULL, wall_ms), 2 IGNORE NULLS)[SAFE_OFFSET(1)]
                     AS p50_wall_ms,
                   IFNULL(SUM(score), 0) AS score_sum,
                   COUNT(score) AS scored
            FROM `{self._dataset_id}.{REVIEWS_TABLE}`
            WHERE status = 'done'
              AND (
                (mode = 'tiered'
                 AND created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)
                 AND (@uid = '' OR uid = @uid))
                -- Benchmark evidence: only the most recent paired run, whatever its age.
                OR (STARTS_WITH(mode, 'benchmark') AND run_id = (
                  SELECT run_id FROM `{self._dataset_id}.{REVIEWS_TABLE}`
                  WHERE STARTS_WITH(mode, 'benchmark') AND run_id IS NOT NULL
                  ORDER BY completed_at DESC LIMIT 1))
              )
            GROUP BY mode
        """
        params = [
            bigquery.ScalarQueryParameter("days", "INT64", days),
            bigquery.ScalarQueryParameter("uid", "STRING", uid or ""),
        ]
        rows = self._rows(("modes", uid, days), sql, params)
        return {
            r["mode"]: ModeAggregate(
                reviews=r["reviews"],
                cache_hits=r["cache_hits"],
                escalated=r["escalated"],
                cost_usd=r["cost_usd"],
                cost_usd_standard=r["cost_usd_standard"],
                p50_wall_ms=r["p50_wall_ms"],
                score_sum=r["score_sum"],
                scored=r["scored"],
            )
            for r in rows
        }

    def recurring(self, uid: str, days: int) -> dict[str, Any]:
        from sar_plimsoll.analytics.insights import build_recurring

        window = "created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)"
        params = [
            bigquery.ScalarQueryParameter("days", "INT64", days),
            bigquery.ScalarQueryParameter("uid", "STRING", uid),
        ]
        findings = self._rows(
            ("findings", uid, days),
            f"""SELECT review_id, uid, dimension, severity, grounded_rule_ids,
                       FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E6S+00:00', created_at) AS created_at
                FROM `{self._dataset_id}.{FINDINGS_TABLE}` WHERE uid = @uid AND {window}""",
            params,
        )
        reviews = self._rows(
            ("reviews", uid, days),
            f"""SELECT findings, FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%E6S+00:00', created_at) AS created_at
                FROM `{self._dataset_id}.{REVIEWS_TABLE}`
                WHERE uid = @uid AND mode = 'tiered' AND status = 'done' AND {window}""",
            params,
        )
        rule_ids = sorted({rid for f in findings for rid in f["grounded_rule_ids"]})
        rules = {}
        if rule_ids:
            rows = self._rows(
                ("rules", tuple(rule_ids)),
                f"SELECT id, type, description FROM `{self._dataset_id}.{RULES_TABLE}` WHERE id IN UNNEST(@ids)",
                [bigquery.ArrayQueryParameter("ids", "STRING", rule_ids)],
            )
            rules = {r["id"]: r for r in rows}
        return build_recurring(findings, reviews, rules.get)
