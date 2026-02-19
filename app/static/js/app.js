/**
 * PositionMatrix — Custom JavaScript
 *
 * HTMX is loaded via CDN in base.html and handles most dynamic
 * interactions.  This file contains only supplemental JS that
 * HTMX cannot handle natively.
 */

document.addEventListener('DOMContentLoaded', function () {

    // -- Auto-dismiss flash messages after 5 seconds ----------------------
    var flashAlerts = document.querySelectorAll('#flash-messages .alert');
    flashAlerts.forEach(function (alert) {
        setTimeout(function () {
            var bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            if (bsAlert) {
                bsAlert.close();
            }
        }, 5000);
    });

    // -- Confirm dialogs for destructive actions --------------------------
    // Any element with data-confirm="message" will show a confirm dialog.
    document.addEventListener('click', function (event) {
        var target = event.target.closest('[data-confirm]');
        if (target) {
            var message = target.getAttribute('data-confirm');
            if (!confirm(message)) {
                event.preventDefault();
                event.stopPropagation();
            }
        }
    });

});
