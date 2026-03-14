"""Route modules for the STS Situation Monitor API."""
from sts_monitor.routes.admin import router as admin_router
from sts_monitor.routes.alerts import router as alerts_router
from sts_monitor.routes.analysis import router as analysis_router
from sts_monitor.routes.dashboard import router as dashboard_router
from sts_monitor.routes.discovery import router as discovery_router
from sts_monitor.routes.export import router as export_router
from sts_monitor.routes.geo import router as geo_router
from sts_monitor.routes.ingestion import router as ingestion_router
from sts_monitor.routes.investigations import router as investigations_router
from sts_monitor.routes.jobs import router as jobs_router
from sts_monitor.routes.reports import router as reports_router
from sts_monitor.routes.research import router as research_router
from sts_monitor.routes.search import router as search_router
from sts_monitor.routes.semantic import router as semantic_router
from sts_monitor.routes.system import router as system_router

all_routers = [
    system_router,
    investigations_router,
    ingestion_router,
    reports_router,
    research_router,
    semantic_router,
    geo_router,
    dashboard_router,
    discovery_router,
    search_router,
    alerts_router,
    analysis_router,
    jobs_router,
    admin_router,
    export_router,
]

__all__ = ["all_routers"]
