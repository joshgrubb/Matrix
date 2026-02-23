"""
NeoGov API client — handles all communication with the NeoGov HR API.

Encapsulates authentication, SSL configuration, paginated fetching,
and data transformation so that ``hr_sync_service`` receives clean,
normalized dicts ready for database upsert.

The NeoGov API exposes separate paginated endpoints for each entity
type (departments, divisions, positions).  Position detail requires
an additional per-record fetch.  This client handles all of that
complexity behind a single ``fetch_all_organization_data()`` method.

Configuration is read from Flask ``current_app.config``:
    - ``NEOGOV_API_BASE_URL``:  e.g. ``https://api.neogov.com/v1``
    - ``NEOGOV_API_KEY``:       Base64-encoded Basic-auth token.
    - ``NEOGOV_EXCLUDED_DEPARTMENTS``: List of department codes to skip.

Author: Josh Grubb
"""

import json
import logging
import ssl
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import urllib3
from flask import current_app

logger = logging.getLogger(__name__)

# Maximum records per page supported by the NeoGov API.
_MAX_PAGE_SIZE = 50


class NeoGovApiClient:
    """
    Client for the NeoGov REST API (v1).

    Manages SSL context, authentication headers, and paginated
    fetching for all organizational endpoints.

    Usage inside a Flask request or app context::

        client = NeoGovApiClient()
        data = client.fetch_all_organization_data()
    """

    def __init__(self) -> None:
        """
        Initialize the client by reading config from Flask app context.

        Raises:
            RuntimeError: If called outside a Flask application context.
        """
        # Read configuration from Flask app config.
        self.base_url: str = current_app.config["NEOGOV_API_BASE_URL"].rstrip("/")
        self.api_key: str = current_app.config.get("NEOGOV_API_KEY", "")
        self.excluded_departments: list[str] = current_app.config.get(
            "NEOGOV_EXCLUDED_DEPARTMENTS", []
        )
        # Maximum concurrent HTTP requests for employee detail fetching.
        self.max_concurrent_requests: int = current_app.config.get(
            "NEOGOV_MAX_CONCURRENT_REQUESTS", 5
        )

        # Build the authorization header (NeoGov uses HTTP Basic auth).
        self.headers: dict[str, str] = {
            "Authorization": f"Basic {self.api_key}",
        }

        # Create a reusable SSL context with the legacy server workaround.
        # NeoGov's servers require OP_LEGACY_SERVER_CONNECT (OpenSSL 0x4).
        self._ssl_ctx = urllib3.util.ssl_.create_urllib3_context()
        self._ssl_ctx.load_default_certs()
        self._ssl_ctx.options |= 0x4  # ssl.OP_LEGACY_SERVER_CONNECT

        logger.debug(
            "NeoGovApiClient initialized — base_url=%s, excluded_depts=%s",
            self.base_url,
            self.excluded_departments,
        )

    # =================================================================
    # Public API
    # =================================================================

    def fetch_all_organization_data(self) -> dict[str, list[dict[str, Any]]]:
        """
        Fetch all organizational data from NeoGov in dependency order.

        Returns a dict with keys matching what ``hr_sync_service``
        expects::

            {
                "departments": [{"department_code": ..., "department_name": ...}, ...],
                "divisions":   [{"division_code": ..., "division_name": ...,
                                 "department_code": ...}, ...],
                "positions":   [{"position_code": ..., "position_title": ...,
                                 "division_code": ..., "authorized_count": ...}, ...],
                "employees":   [{"employee_id": ..., "first_name": ...,
                                 "last_name": ..., "email": ...,
                                 "position_code": ...}, ...],
            }

        Returns:
            Normalized organizational data ready for sync.

        Raises:
            ConnectionError: If the API is unreachable or returns errors.
        """
        if not self.api_key:
            logger.warning(
                "NEOGOV_API_KEY not configured — returning empty data. "
                "Set the key in .env for production."
            )
            return {
                "departments": [],
                "divisions": [],
                "positions": [],
                "employees": [],
            }

        # Fetch each entity type.  Order matters: departments must
        # come first so we can filter divisions/positions by excluded
        # department codes.
        raw_departments = self._fetch_all_pages("departments")
        raw_divisions = self._fetch_all_pages("divisions")
        raw_positions = self._fetch_position_details()
        raw_employees = self._fetch_employee_details()

        # Transform raw API JSON into the normalized shape that
        # hr_sync_service expects.
        departments = self._transform_departments(raw_departments)
        divisions = self._transform_divisions(raw_divisions)
        positions = self._transform_positions(raw_positions)
        employees = self._transform_employees(raw_employees)

        logger.info(
            "NeoGov fetch complete: %d departments, %d divisions, "
            "%d positions, %d employees",
            len(departments),
            len(divisions),
            len(positions),
            len(employees),
        )

        return {
            "departments": departments,
            "divisions": divisions,
            "positions": positions,
            "employees": employees,
        }

    # =================================================================
    # HTTP transport
    # =================================================================

    def _make_request(
        self,
        endpoint: str,
        page: int = 1,
        page_size: int = _MAX_PAGE_SIZE,
    ) -> dict[str, Any] | None:
        """
        Send an HTTP GET request to a NeoGov API endpoint.

        Args:
            endpoint:  Relative path appended to ``base_url``
                       (e.g. ``departments`` or ``positions/ABC123``).
            page:      Page number for paginated endpoints.
            page_size: Records per page (max 50).

        Returns:
            Parsed JSON response as a dict, or None on failure.
        """
        url = f"{self.base_url}/{endpoint}"
        params = {"pageNumber": page, "pageSize": page_size}

        try:
            with urllib3.PoolManager(ssl_context=self._ssl_ctx) as http:
                response = http.request(
                    "GET",
                    url,
                    headers=self.headers,
                    fields=params,
                )

                if response.status == 200:
                    return json.loads(response.data)

                logger.error(
                    "NeoGov API %s (page %d) returned status %d",
                    endpoint,
                    page,
                    response.status,
                )
                return None

        except urllib3.exceptions.RequestError as exc:
            logger.error("RequestError calling NeoGov %s: %s", endpoint, exc)
            return None
        except urllib3.exceptions.HTTPError as exc:
            logger.error("HTTPError calling NeoGov %s: %s", endpoint, exc)
            return None
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON from NeoGov %s: %s", endpoint, exc)
            return None

    def _fetch_all_pages(self, endpoint: str) -> list[dict[str, Any]]:
        """
        Fetch all pages from a paginated NeoGov endpoint.

        Iterates through pages until ``totalPages`` is reached or no
        more data is returned.

        Args:
            endpoint: The API endpoint name (e.g. ``departments``).

        Returns:
            Flat list of all record dicts across all pages.
        """
        all_records: list[dict[str, Any]] = []
        page = 1

        while True:
            response_data = self._make_request(endpoint, page=page)

            if response_data is None:
                logger.error(
                    "Failed to fetch page %d of %s — stopping pagination",
                    page,
                    endpoint,
                )
                break

            # Extract the data array from the response envelope.
            records = response_data.get("data", [])
            all_records.extend(records)

            # Check if more pages are available.
            total_pages = response_data.get("totalPages", 1)
            if page >= total_pages:
                break

            page += 1

        logger.debug(
            "Fetched %d total records from %s across %d page(s)",
            len(all_records),
            endpoint,
            page,
        )
        return all_records

    def _fetch_position_details(self) -> list[dict[str, Any]]:
        """
        Fetch detailed data for every position.

        The NeoGov ``/positions`` list endpoint returns only code,
        name, and status.  The detail endpoint ``/positions/{code}``
        returns the full record including division and department
        references needed for sync.

        Returns:
            List of detailed position dicts.
        """
        # Step 1: Get all position codes from the paginated list.
        position_codes = self._fetch_all_position_codes()
        logger.info("Found %d position codes to fetch details for", len(position_codes))

        # Step 2: Fetch detail for each position code.
        detailed_positions: list[dict[str, Any]] = []

        for code in position_codes:
            detail = self._make_request(f"positions/{code}")

            if detail is None:
                logger.warning("Failed to fetch detail for position %s", code)
                continue

            # Check if this position belongs to an excluded department.
            # Use 'or {}' to handle JSON null values in nested objects.
            department_code = (
                (detail.get("details") or {}).get("department") or {}
            ).get("code", "")
            if department_code in self.excluded_departments:
                logger.debug(
                    "Skipping position %s — excluded department %s",
                    code,
                    department_code,
                )
                continue

            detailed_positions.append(detail)

        logger.debug(
            "Fetched details for %d positions (after exclusions)",
            len(detailed_positions),
        )
        return detailed_positions

    def _fetch_all_position_codes(self) -> list[str]:
        """
        Collect all position codes from the paginated list endpoint.

        Returns:
            List of position code strings.
        """
        codes: list[str] = []
        page = 1

        while True:
            response_data = self._make_request("positions", page=page)

            if response_data is None:
                break

            records = response_data.get("data", [])
            codes.extend(record.get("code", "") for record in records)

            # Check for more pages.
            total_pages = response_data.get("totalPages", 1)
            if page >= total_pages:
                break

            page += 1

        return codes

    def _fetch_employee_details(self) -> list[dict[str, Any]]:
        """
        Fetch detailed data for every employee using concurrent requests.

        Employee data follows a two-step pattern similar to positions:
        1. Fetch all person codes from the paginated ``/persons`` endpoint.
        2. Fetch individual employee detail from ``/employees/{code}``
           concurrently using a thread pool.

        Returns:
            List of raw employee detail dicts.
        """
        # Step 1: Get all employee codes from the paginated persons list.
        person_codes = self._fetch_all_person_codes()
        logger.info(
            "Found %d person codes to fetch employee details for",
            len(person_codes),
        )

        if not person_codes:
            return []

        # Step 2: Fetch detail for each person concurrently.
        detailed_employees: list[dict[str, Any]] = []
        failed_count = 0

        with ThreadPoolExecutor(
            max_workers=self.max_concurrent_requests,
        ) as executor:
            # Submit all detail requests to the thread pool.
            future_to_code = {
                executor.submit(
                    self._fetch_single_employee_detail,
                    code,
                ): code
                for code in person_codes
            }

            # Collect results as they complete.
            for future in as_completed(future_to_code):
                code = future_to_code[future]

                try:
                    result = future.result()
                    if result is not None:
                        detailed_employees.append(result)
                    else:
                        failed_count += 1
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    logger.error(
                        "Exception fetching employee %s: %s",
                        code,
                        exc,
                    )
                    failed_count += 1

        if failed_count > 0:
            logger.warning(
                "Failed to fetch details for %d of %d employees",
                failed_count,
                len(person_codes),
            )

        logger.debug(
            "Fetched details for %d employees",
            len(detailed_employees),
        )
        return detailed_employees

    def _fetch_single_employee_detail(
        self,
        employee_code: str,
    ) -> dict[str, Any] | None:
        """
        Fetch detail for a single employee by code.

        Called from within the thread pool by
        ``_fetch_employee_details()``.  Each invocation creates its
        own ``PoolManager`` to ensure thread safety.

        Args:
            employee_code: The person/employee code from ``/persons``.

        Returns:
            Parsed JSON dict for the employee, or None on failure.
        """
        endpoint = f"employees/{employee_code}"
        url = f"{self.base_url}/{endpoint}"
        params = {"pageNumber": 1, "pageSize": _MAX_PAGE_SIZE}

        try:
            # Each thread gets its own PoolManager for thread safety.
            with urllib3.PoolManager(ssl_context=self._ssl_ctx) as http:
                response = http.request(
                    "GET",
                    url,
                    headers=self.headers,
                    fields=params,
                )

                if response.status == 200:
                    return json.loads(response.data)

                logger.error(
                    "NeoGov API %s returned status %d",
                    endpoint,
                    response.status,
                )
                return None

        except urllib3.exceptions.RequestError as exc:
            logger.error("RequestError calling NeoGov %s: %s", endpoint, exc)
            return None
        except urllib3.exceptions.HTTPError as exc:
            logger.error("HTTPError calling NeoGov %s: %s", endpoint, exc)
            return None
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON from NeoGov %s: %s", endpoint, exc)
            return None

    def _fetch_all_person_codes(self) -> list[str]:
        """
        Collect all person codes from the paginated ``/persons`` endpoint.

        These codes are used as identifiers to fetch individual employee
        detail records from ``/employees/{code}``.

        Returns:
            List of person code strings.
        """
        codes: list[str] = []
        page = 1

        while True:
            response_data = self._make_request("persons", page=page)

            if response_data is None:
                logger.error(
                    "Failed to fetch page %d of persons — stopping pagination",
                    page,
                )
                break

            records = response_data.get("data", [])

            for record in records:
                code = record.get("code", "")
                if code:
                    codes.append(code)
                else:
                    logger.warning("Person entry missing 'code' field")

            # Check for more pages.
            total_pages = response_data.get("totalPages", 1)
            if page >= total_pages:
                break

            page += 1

        logger.debug(
            "Fetched %d person codes across %d page(s)",
            len(codes),
            page,
        )
        return codes

    # =================================================================
    # Data transformation
    # =================================================================

    def _transform_departments(
        self,
        raw_departments: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Transform raw NeoGov department records into the normalized
        shape expected by ``hr_sync_service._sync_departments()``.

        Filters out excluded departments.

        Args:
            raw_departments: Raw dicts from the NeoGov API.

        Returns:
            List of normalized department dicts with keys:
            ``department_code``, ``department_name``.
        """
        normalized: list[dict[str, Any]] = []

        for dept in raw_departments:
            code = dept.get("code", "")

            # Skip excluded departments.
            if code in self.excluded_departments:
                logger.debug("Excluding department: %s", code)
                continue

            normalized.append(
                {
                    "department_code": code,
                    "department_name": dept.get("name", code),
                }
            )

        return normalized

    def _transform_divisions(
        self,
        raw_divisions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Transform raw NeoGov division records into the normalized
        shape expected by ``hr_sync_service._sync_divisions()``.

        Extracts the nested ``department.code`` field and filters
        out divisions belonging to excluded departments.

        Args:
            raw_divisions: Raw dicts from the NeoGov API.

        Returns:
            List of normalized division dicts with keys:
            ``division_code``, ``division_name``, ``department_code``.
        """
        normalized: list[dict[str, Any]] = []

        for div in raw_divisions:
            # Use 'or {}' to safely handle JSON null values.
            department_obj = div.get("department") or {}

            dept_code = department_obj.get("code", "")

            # Skip divisions belonging to excluded departments.
            if dept_code in self.excluded_departments:
                logger.debug(
                    "Excluding division %s — parent department %s is excluded",
                    div.get("code", ""),
                    dept_code,
                )
                continue

            normalized.append(
                {
                    "division_code": div.get("code", ""),
                    "division_name": div.get("name", ""),
                    "department_code": dept_code,
                }
            )

        return normalized

    def _transform_positions(
        self,
        raw_positions: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Transform raw NeoGov position detail records into the normalized
        shape expected by ``hr_sync_service._sync_positions()``.

        Position detail responses have a nested structure::

            {
                "code": "POS001",
                "status": "Active",
                "details": {
                    "positionTitle": "...",
                    "division": {"code": "DIV001"},
                    "department": {"code": "DEPT01"},
                    "authorizedFte": 3.0
                }
            }

        Args:
            raw_positions: Raw detail dicts from individual position fetches.

        Returns:
            List of normalized position dicts with keys:
            ``position_code``, ``position_title``, ``division_code``,
            ``status``, ``authorized_count``.
        """
        normalized: list[dict[str, Any]] = []

        for pos in raw_positions:
            # Use 'or {}' instead of a default param because NeoGov
            # may return JSON null for these fields.  dict.get() only
            # uses the default when the key is absent — a present key
            # with a null value returns None.
            details = pos.get("details") or {}
            division_obj = details.get("division") or {}

            # authorizedFte is exposed in the /v1/positions/{code}
            # detail endpoint, nested inside the "details" object.
            # The API returns a float (FTE); convert to int for the
            # Position.authorized_count column.  Default to 1 if the
            # field is missing or null.
            raw_fte = details.get("authorizedFte")
            authorized_count = int(raw_fte) if raw_fte is not None else 1

            normalized.append(
                {
                    "position_code": pos.get("code", ""),
                    "position_title": details.get("positionTitle", ""),
                    "division_code": division_obj.get("code", ""),
                    "status": pos.get("status", ""),
                    "authorized_count": authorized_count,
                }
            )

        return normalized

    def _transform_employees(
        self,
        raw_employees: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Transform raw NeoGov employee detail records into the normalized
        shape expected by ``hr_sync_service._sync_employees()``.

        The ``/employees/{personCode}`` endpoint returns a flat
        PascalCase structure::

            {
                "EmployeeNumber": "12345",
                "FirstName": "Jane",
                "MiddleName": "M",
                "LastName": "Doe",
                "WorkEmail": "jdoe@example.gov",
                "PositionCode": "23001",
                ...
            }

        Args:
            raw_employees: Raw dicts from individual employee fetches.

        Returns:
            List of normalized employee dicts with keys:
            ``employee_id``, ``first_name``, ``last_name``,
            ``email``, ``position_code``.
        """
        normalized: list[dict[str, Any]] = []

        for emp in raw_employees:
            # -- Employee ID -------------------------------------------
            # EmployeeNumber is the canonical identifier.
            employee_id = emp.get("EmployeeNumber", "")

            # -- Name fields -------------------------------------------
            first_name = emp.get("FirstName", "")
            last_name = emp.get("LastName", "")

            # -- Email -------------------------------------------------
            # Prefer work email; fall back to personal email.
            email = emp.get("WorkEmail") or emp.get("PersonalEmail")

            # -- Position code -----------------------------------------
            position_code = emp.get("PositionCode", "")

            # Skip employees missing critical identifiers.
            if not employee_id:
                logger.warning("Skipping employee with missing EmployeeNumber")
                continue

            normalized.append(
                {
                    "employee_id": employee_id,
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": email,
                    "position_code": position_code,
                }
            )

        return normalized
