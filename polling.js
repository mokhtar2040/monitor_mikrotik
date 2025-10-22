let autoRefreshEnabled = true;
let refreshInterval;

// Start auto-refresh
function startAutoRefresh() {
    refreshInterval = setInterval(() => {
        if (autoRefreshEnabled) {
            fetchStats();
        }
    }, 3000);
}

// Fetch stats
function fetchStats() {
    const ip = document.getElementById('monitor_ip').value;
    // ... (same as before)
    
    fetch('/api/get-stats', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ip, username, password, interfaces })
    })
    .then(r => r.json())
    .then(data => {
        if (data.status === "success") {
            displayStats(data.stats);
            updateLastUpdated();
        }
    });
}

// Export to CSV
document.getElementById('exportBtn').addEventListener('click', function () {
    const table = document.getElementById('stats-table');
    let csv = 'الواجهة,سرعة التنزيل,سرعة الرفع,إجمالي RX,إجمالي TX,الوقت\n';
    
    Array.from(table.rows).slice(1).forEach(row => {
        const cells = Array.from(row.cells);
        const values = cells.map(cell => `"${cell.textContent}"`).join(',');
        csv += values + '\n';
    });

    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `mikrotik_report_${new Date().toISOString().slice(0,16)}.csv`;
    a.click();
});

// Auto-refresh control
document.getElementById('autoRefresh').addEventListener('change', function () {
    autoRefreshEnabled = this.checked;
});

// On load
document.getElementById('monitorForm').addEventListener('submit', function (e) {
    e.preventDefault();
    fetchStats();
    startAutoRefresh();
});