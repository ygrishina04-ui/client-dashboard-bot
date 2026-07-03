def get_scripts():
    return """
<script>
document.addEventListener('DOMContentLoaded', function () {

    function initManagerFilter() {
        const filter = document.getElementById('managerFilter');
        if (!filter) return;

        filter.addEventListener('change', function () {
            const value = filter.value;

            document.querySelectorAll('tr[data-manager]').forEach(row => {
                const manager = row.getAttribute('data-manager') || '';
                row.classList.toggle(
                    'hidden-by-filter',
                    value !== '__all__' && manager !== value
                );
            });
        });
    }

    function initMainNavigation() {
        document.querySelectorAll('.nav-item[data-page]').forEach(item => {
            item.addEventListener('click', function () {
                const page = item.dataset.page;

                document.querySelectorAll('.nav-item[data-page]').forEach(i => {
                    i.classList.remove('active');
                });

                item.classList.add('active');

                document.querySelectorAll('.page').forEach(p => {
                    p.classList.remove('active-page');
                });

                const target = document.getElementById('page-' + page);
                if (target) {
                    target.classList.add('active-page');
                }
            });
        });
    }

    function initClientSubtabs() {
        document.querySelectorAll('.subtab').forEach(link => {
            link.addEventListener('click', function (e) {
                e.preventDefault();

                const section = link.dataset.section;

                document.querySelectorAll('.subtab').forEach(item => {
                    item.classList.remove('active-subtab');
                });

                link.classList.add('active-subtab');

                document.querySelectorAll('.dashboard-section').forEach(block => {
                    block.classList.remove('active-section');
                });

                const target = document.getElementById(section + '-section');
                if (target) {
                    target.classList.add('active-section');
                }
            });
        });
    }

    function initRequestDetails() {
        document.querySelectorAll('.toggle-details').forEach(btn => {
            btn.addEventListener('click', function () {
                const group = btn.closest('.attention-group');
                if (!group) return;

                group.classList.toggle('open');
                btn.textContent = group.classList.contains('open') ? '▼' : '▶';
            });
        });
    }

    function initSnoozeButtons() {
        document.querySelectorAll('.snooze-btn').forEach(btn => {
            btn.addEventListener('click', async function () {
                const row = btn.closest('tr');
                if (!row) return;

                const dateInput = row.querySelector('.snooze-date');
                const daysSelect = row.querySelector('.snooze-days');
                const reasonSelect = row.querySelector('.snooze-reason');

                let until = dateInput ? dateInput.value : '';
                const days = daysSelect ? daysSelect.value : '';
                const reason = reasonSelect ? reasonSelect.value : '';

                if (!until && days) {
                    const d = new Date();
                    d.setDate(d.getDate() + parseInt(days));
                    until = d.toISOString().slice(0, 10);
                }

                if (!until) {
                    alert('Выберите срок или дату');
                    return;
                }

                if (!reason) {
                    alert('Выберите причину');
                    return;
                }

                const client = btn.dataset.client;
                const manager = btn.dataset.manager;

                const response = await fetch('/snooze', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        client: client,
                        manager: manager,
                        until: until,
                        reason: reason
                    })
                });

                const result = await response.json();

                if (result.ok) {
                    row.style.display = 'none';
                    alert('Клиент отложен до ' + until);
                } else {
                    alert('Ошибка: ' + result.error);
                }
            });
        });
    }

    initManagerFilter();
    initMainNavigation();
    initClientSubtabs();
    initRequestDetails();
    initSnoozeButtons();

});
</script>
"""
