/* =============================================================================
   PositionMatrix — Custom Application JavaScript
   HTMX handles most dynamic interactions. Add custom JS here only as needed.
   ============================================================================= */

// Configure HTMX to include the CSRF token on every request.
document.addEventListener("DOMContentLoaded", function () {
    // Read the CSRF token from the meta tag (added by Flask-WTF).
    const csrfToken = document.querySelector('meta[name="csrf-token"]');
    if (csrfToken) {
        document.body.addEventListener("htmx:configRequest", function (event) {
            event.detail.headers["X-CSRFToken"] = csrfToken.content;
        });
    }
});
