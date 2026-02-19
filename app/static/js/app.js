/**
 * PositionMatrix — Custom JavaScript
 *
 * Handles sidebar toggle for mobile, flash message auto-dismiss,
 * and confirm dialogs for destructive actions.
 *
 * HTMX is loaded via CDN in base.html and handles most dynamic
 * server-rendered interactions. This file supplements HTMX with
 * functionality it cannot handle natively.
 */

document.addEventListener('DOMContentLoaded', function () {

    // ── Sidebar mobile toggle ───────────────────────────────────────
    // On screens < 992px the sidebar is hidden off-screen. The
    // hamburger button toggles it into view as an offcanvas panel,
    // with a backdrop behind it for click-away dismissal.
    var sidebar = document.getElementById('sidebarNav');
    var toggle = document.getElementById('sidebarToggle');
    var backdrop = document.getElementById('sidebarBackdrop');

    /**
     * Open the mobile sidebar and show the backdrop overlay.
     */
    function openSidebar() {
        if (sidebar) { sidebar.classList.add('show'); }
        if (backdrop) { backdrop.classList.add('show'); }
        // Prevent background scrolling while sidebar is open.
        document.body.style.overflow = 'hidden';
    }

    /**
     * Close the mobile sidebar and hide the backdrop overlay.
     */
    function closeSidebar() {
        if (sidebar) { sidebar.classList.remove('show'); }
        if (backdrop) { backdrop.classList.remove('show'); }
        document.body.style.overflow = '';
    }

    if (toggle) {
        toggle.addEventListener('click', function () {
            if (sidebar && sidebar.classList.contains('show')) {
                closeSidebar();
            } else {
                openSidebar();
            }
        });
    }

    // Close sidebar when user clicks the backdrop.
    if (backdrop) {
        backdrop.addEventListener('click', closeSidebar);
    }

    // Close sidebar when user clicks a navigation link (mobile UX).
    if (sidebar) {
        sidebar.querySelectorAll('.pm-sidebar-link').forEach(function (link) {
            link.addEventListener('click', function () {
                // Only close on mobile widths.
                if (window.innerWidth < 992) {
                    closeSidebar();
                }
            });
        });
    }


    // ── Auto-dismiss flash messages after 6 seconds ─────────────────
    var flashAlerts = document.querySelectorAll('#flash-messages .alert');
    flashAlerts.forEach(function (alert) {
        setTimeout(function () {
            var bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
            if (bsAlert) {
                bsAlert.close();
            }
        }, 6000);
    });


    // ── Confirm dialogs for destructive actions ─────────────────────
    // Any element with data-confirm="message" will show a confirm
    // dialog before proceeding. Works on buttons, links, and forms.
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