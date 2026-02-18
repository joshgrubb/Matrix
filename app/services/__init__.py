"""
Service layer package.

Each service module encapsulates one domain of business logic.
Services are the only layer that interacts with models; routes
never access the database directly.

Import services in route modules as needed::

    from app.services.organization_service import get_departments
"""
