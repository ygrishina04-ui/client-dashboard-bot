def get_styles():
    return r"""
<style>
:root {
    --blue:#3498db;
    --pink:#e84393;
    --violet:#6c5ce7;
    --dark:#172033;
    --muted:#667085;
    --red:#e74c3c;
}

* { box-sizing:border-box; }

body {
    margin:0;
    font-family:Inter,Arial,sans-serif;
    background:linear-gradient(135deg,#cfe8ff 0%,#d9d6ff 45%,#ffd4ea 100%);
    color:#1f2937;
}

.app-shell {
    display:grid;
    grid-template-columns:235px 1fr;
    min-height:100vh;
}

.sidebar {
    background:linear-gradient(180deg,#172033 0%,#202a44 100%);
    color:white;
    padding:26px 20px;
    position:sticky;
    top:0;
    height:100vh;
    box-shadow:14px 0 38px rgba(23,32,51,.16);
}

.side-logo {
    font-size:22px;
    font-weight:900;
    margin-bottom:34px;
    color:white;
    line-height:1.18;
    letter-spacing:-.02em;
}

.nav-section { margin-bottom:10px; }

.nav-item {
    padding:12px 14px;
    border-radius:14px;
    color:#dbeafe;
    font-weight:800;
    margin-bottom:6px;
    cursor:pointer;
    transition:.2s;
}

.nav-item:hover {
    background:rgba(255,255,255,.09);
    color:white;
}

.nav-item.active {
    background:linear-gradient(135deg,#3498db,#6c5ce7);
    color:white;
    box-shadow:0 12px 26px rgba(52,152,219,.26);
}

.nav-sub {
    padding-left:38px;
    color:#cbd5e1;
    font-size:14px;
    line-height:1.7;
    margin:4px 0 18px;
}

.main-area { min-width:0; }
.page { display:none; }
.page.active-page { display:block; }

.wrap {
    max-width:1480px;
    margin:0 auto;
    padding:28px 32px;
}

.hero {
    display:flex;
    justify-content:space-between;
    gap:16px;
    align-items:center;
    margin-bottom:20px;
}

h1 {
    margin:0;
    font-size:34px;
    letter-spacing:-.04em;
}

.sub {
    color:var(--muted);
    margin-top:8px;
}

.badge {
    padding:10px 14px;
    border-radius:999px;
    background:#fff;
    border:1px solid #e9ecf5;
    box-shadow:0 10px 30px rgba(43,55,90,.08);
}

.toolbar {
    display:flex;
    gap:12px;
    align-items:center;
    justify-content:space-between;
    margin:16px 0 20px;
    padding:14px 16px;
    border-radius:22px;
    background:rgba(255,255,255,.82);
    border:1px solid #edf0fa;
    box-shadow:0 14px 40px rgba(42,56,100,.10);
}

.toolbar label {
    font-size:13px;
    color:var(--muted);
    text-transform:uppercase;
    letter-spacing:.06em;
}

select {
    border:1px solid #dde5f3;
    border-radius:14px;
    padding:11px 14px;
    background:white;
    color:var(--dark);
    font-weight:700;
}

.filter-note {
    font-size:13px;
    color:var(--muted);
}

.grid {
    display:grid;
    gap:16px;
}

.kpi {
    grid-template-columns:repeat(4,1fr);
    margin-bottom:16px;
}

.three-kpi { grid-template-columns:repeat(3,1fr); }

.card {
    background:rgba(255,255,255,.92);
    backdrop-filter:blur(8px);
    border-radius:20px;
    padding:20px;
    box-shadow:0 12px 30px rgba(91,33,182,.12);
    border:1px solid rgba(255,255,255,.5);
}

.label {
    font-size:13px;
    color:var(--muted);
    text-transform:uppercase;
    letter-spacing:.06em;
}

.num {
    font-size:34px;
    font-weight:850;
    margin-top:8px;
}

.pink { color:var(--pink); }
.blue { color:var(--blue); }
.violet { color:var(--violet); }
.red { color:var(--red); }

.section { margin-top:24px; }
h2 { font-size:24px; margin:0 0 14px; }
.two { grid-template-columns:1.1fr .9fr; }

table {
    width:100%;
    border-collapse:collapse;
    font-size:14px;
}

th,td {
    text-align:left;
    padding:12px;
    border-bottom:1px solid #edf0f7;
}

th {
    color:var(--muted);
    font-size:12px;
    text-transform:uppercase;
    letter-spacing:.06em;
}

.stage {
    display:flex;
    justify-content:space-between;
    align-items:center;
    padding:16px;
    margin-bottom:10px;
    border-radius:18px;
    background:linear-gradient(135deg,#eef2ff,#fdf2f8);
    border:1px solid #dbeafe;
}

.stage b { font-size:26px; }
.hot { font-weight:800; color:var(--red); }

.critical td {
    background:#fff0f3!important;
    color:#9f1239;
    font-weight:700;
}

.lost td { background:#fff7f8; }
.risk td { background:#fffaf0; }

.note {
    font-size:12px;
    color:var(--muted);
    margin-top:8px;
}

.hidden-by-filter { display:none!important; }

.toggle-details {
    border:0;
    background:#eef2ff;
    border-radius:8px;
    padding:6px 9px;
    cursor:pointer;
    font-weight:800;
}

.detail-row { display:none; }

.detail-row td {
    background:#f8fafc;
    color:#475467;
    font-size:13px;
}

.attention-group.open .detail-row { display:table-row; }
.attention-group.open .toggle-details { background:#dbeafe; }

.snooze-cell { white-space:nowrap; }

.snooze-days,
.snooze-reason,
.snooze-date {
    border:1px solid #d6dcf5;
    border-radius:10px;
    padding:8px 10px;
    background:white;
    font-weight:600;
    font-size:13px;
    margin-right:6px;
    max-width:145px;
}

.snooze-days:focus,
.snooze-reason:focus,
.snooze-date:focus {
    outline:none;
    border-color:#6c5ce7;
    box-shadow:0 0 0 3px rgba(108,92,231,.15);
}

.snooze-btn {
    border:0;
    border-radius:10px;
    padding:8px 10px;
    background:linear-gradient(135deg,#3498db,#6c5ce7);
    color:white;
    cursor:pointer;
    font-weight:700;
}

.placeholder {
    padding:36px;
    border-radius:24px;
    background:rgba(255,255,255,.9);
    box-shadow:0 12px 30px rgba(91,33,182,.12);
}

@media(max-width:900px) {
    .app-shell { display:block; }
    .sidebar { position:relative; height:auto; }
    .kpi,.three-kpi,.two { grid-template-columns:1fr; }
    .hero,.toolbar { display:block; }
    select { width:100%; margin-top:8px; }
}


.dashboard-section {
    display:none;
}

.dashboard-section.active-section {
    display:block;
}

.nav-link {
    display:block;
    padding:8px 14px;
    margin:4px 0;
    border-radius:10px;
    color:#d7e3ff;
    text-decoration:none;
    transition:.2s;
    font-weight:600;
}

.nav-link:hover,
.nav-link.active-subtab {
    background:rgba(255,255,255,.14);
    color:#fff;
    padding-left:18px;
}

</style>
"""

