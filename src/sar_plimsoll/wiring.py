"""Dependency wiring. GCP clients are imported lazily so local runs need no credentials."""

from dataclasses import dataclass

from sar_plimsoll.api.auth import DevVerifier, TokenVerifier
from sar_plimsoll.api.submission import SubmissionService
from sar_plimsoll.config import Settings
from sar_plimsoll.llm.backends.fake import FakeEmbedding, FakeGenerative
from sar_plimsoll.llm.gateway import LLMGateway
from sar_plimsoll.llm.pricing import PriceTable
from sar_plimsoll.rules.ingest import RulesIngestor
from sar_plimsoll.rules.ingest_jobs import IngestJobRunner, IngestJobService
from sar_plimsoll.rules.retrieval import RuleRetriever
from sar_plimsoll.scoring.rubric import Rubric, load_rubric
from sar_plimsoll.storage.gcp_clients import LazyProxy
from sar_plimsoll.storage.interfaces import (
    AnalyticsQueries,
    AnalyticsSink,
    BlobStore,
    IngestLauncher,
    ReviewStore,
    RulesRepository,
    TaskQueue,
)
from sar_plimsoll.storage.memory import (
    MemoryAnalytics,
    MemoryBlobStore,
    MemoryReviewStore,
    MemoryRulesRepository,
)
from sar_plimsoll.storage.queues import InlineIngestLauncher, InlineQueue
from sar_plimsoll.worker.jobs import ReviewJobRunner
from sar_plimsoll.worker.orchestrator import ReviewOrchestrator


@dataclass
class Container:
    settings: Settings
    rubric: Rubric
    prices: PriceTable
    gateway: LLMGateway
    rules_repo: RulesRepository
    store: ReviewStore
    blobs: BlobStore
    analytics: AnalyticsSink
    analytics_queries: AnalyticsQueries
    queue: TaskQueue
    verifier: TokenVerifier
    ingestor: RulesIngestor
    ingest_jobs: IngestJobService
    ingest_runner: IngestJobRunner
    orchestrator: ReviewOrchestrator
    runner: ReviewJobRunner
    submissions: SubmissionService


def build_container(settings: Settings, *, synchronous_queue: bool = False) -> Container:
    rubric = load_rubric(settings.rubric_path)
    prices = PriceTable.load(settings.pricing_path)

    # GCP adapters are LazyProxy stand-ins: their SDKs load on first use, not at container start.
    if settings.llm_backend == "vertex":

        def _generative():
            from sar_plimsoll.llm.backends.vertex import VertexGenerative

            return VertexGenerative(settings.gcp_project, settings.vertex_location)

        def _embedding():
            from sar_plimsoll.llm.backends.vertex import VertexEmbedding

            return VertexEmbedding(settings.gcp_project, settings.embedding_location)

        generative = LazyProxy(_generative)
        embedding = LazyProxy(_embedding)
    else:
        generative = FakeGenerative(escalation_model=settings.escalation_model)
        embedding = FakeEmbedding()
    gateway = LLMGateway(
        generative=generative, embedding=embedding, prices=prices, settings=settings
    )

    if settings.store_backend == "gcp":

        def _rules():
            from sar_plimsoll.storage.bigquery import BigQueryRulesRepository

            return BigQueryRulesRepository(settings.gcp_project, settings.bq_dataset)

        def _store():
            from sar_plimsoll.storage.firestore import FirestoreReviewStore

            return FirestoreReviewStore(settings.gcp_project)

        def _blobs():
            from sar_plimsoll.storage.gcs import GcsBlobStore

            return GcsBlobStore(settings.gcp_project, settings.gcs_bucket)

        def _analytics():
            from sar_plimsoll.storage.bigquery import BigQueryAnalytics

            return BigQueryAnalytics(
                settings.gcp_project, settings.bq_dataset, cache_s=settings.analytics_cache_s
            )

        if not settings.gcs_bucket:
            raise ValueError("PLIMSOLL_GCS_BUCKET is required when store_backend=gcp")
        rules_repo = LazyProxy(_rules)
        store = LazyProxy(_store)
        blobs = LazyProxy(_blobs)
        analytics = LazyProxy(_analytics)
    else:
        rules_repo = MemoryRulesRepository()
        store = MemoryReviewStore()
        blobs = MemoryBlobStore()
        analytics = MemoryAnalytics(rule_lookup=rules_repo.get_rule)

    retriever = RuleRetriever(repo=rules_repo, gateway=gateway, prices=prices)
    orchestrator = ReviewOrchestrator(
        gateway=gateway, retriever=retriever, rubric=rubric, settings=settings
    )
    runner = ReviewJobRunner(
        store=store,
        blobs=blobs,
        analytics=analytics,
        orchestrator=orchestrator,
        prices=prices,
        settings=settings,
    )

    if settings.queue_backend == "cloudtasks":

        def _queue():
            from sar_plimsoll.storage.queues import CloudTasksQueue

            return CloudTasksQueue(
                project=settings.gcp_project,
                region=settings.gcp_region,
                queue=settings.tasks_queue,
                worker_url=settings.worker_url,
                service_account=settings.tasks_service_account,
            )

        queue: TaskQueue = LazyProxy(_queue)
    else:
        inline = InlineQueue(synchronous=synchronous_queue, max_attempts=settings.task_max_attempts)
        inline.bind(runner.process)
        queue = inline

    def _firebase():
        from sar_plimsoll.api.auth import FirebaseVerifier

        return FirebaseVerifier(settings.gcp_project, settings.admin_uids)

    verifier: TokenVerifier = (
        LazyProxy(_firebase) if settings.auth_mode == "firebase" else DevVerifier()
    )

    ingestor = RulesIngestor(repo=rules_repo, store=store, gateway=gateway, settings=settings)
    ingest_runner = IngestJobRunner(store=store, blobs=blobs, ingestor=ingestor)
    if settings.ingest_backend == "cloudrun_job":

        def _launcher():
            from sar_plimsoll.storage.queues import CloudRunJobLauncher

            return CloudRunJobLauncher(
                project=settings.gcp_project,
                region=settings.gcp_region,
                job_name=settings.ingest_job_name,
            )

        launcher: IngestLauncher = LazyProxy(_launcher)
    else:
        inline_launcher = InlineIngestLauncher(max_retries=settings.ingest_max_retries)
        inline_launcher.bind(ingest_runner.run)
        launcher = inline_launcher

    return Container(
        settings=settings,
        rubric=rubric,
        prices=prices,
        gateway=gateway,
        rules_repo=rules_repo,
        store=store,
        blobs=blobs,
        analytics=analytics,
        analytics_queries=analytics,
        queue=queue,
        verifier=verifier,
        ingestor=ingestor,
        ingest_jobs=IngestJobService(store=store, blobs=blobs, launcher=launcher),
        ingest_runner=ingest_runner,
        orchestrator=orchestrator,
        runner=runner,
        submissions=SubmissionService(
            store=store, blobs=blobs, queue=queue, runner=runner, rubric=rubric, settings=settings
        ),
    )
