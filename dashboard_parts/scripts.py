def get_scripts():
    return """
<script>
const filter = document.getElementById('managerFilter');

function applyManagerFilter() {
    if (!filter) return;

    const value = filter.value;

    document.querySelectorAll('tr[data-manager]').forEach(row => {
        const manager = row.getAttribute('data-manager') || '';
        row.classList.toggle(
            'hidden-by-filter',
            value !== '__all__' && manager !== value
        );
    });
}

if (filter) {
    filter.addEventListener('change', applyManagerFilter);
}

document.querySelectorAll('.subtab').forEach(link => {
    link.addEventListener('click', function(e) {
        e.preventDefault();

        const section = this.dataset.section;

        document.querySelectorAll('.subtab').forEach(item => {
            item.classList.remove('active-subtab');
        });

        this.classList.add('active-subtab');

        document.querySelectorAll('.dashboard-section').forEach(block => {
            block.classList.remove('active-section');
        });

        const target = document.getElementById(section + '-section');

        if (target) {
            target.classList.add('active-section');
        }
    });
});

document.querySelectorAll('.toggle-details').forEach(btn => {
    btn.addEventListener('click', () => {
        const group = btn.closest('.attention-group');
        group.classList.toggle('open');
        btn.textContent = group.classList.contains('open') ? '▼' : '▶';
    });
});

document.querySelectorAll('.snooze-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
        const row = btn.closest('tr');

        const dateInput = row.querySelector('.snooze-date');
        const daysSelect = row.querySelector('.snooze-days');
        const reasonSelect = row.querySelector('.snooze-reason');

        let until = dateInput.value;
        const days = daysSelect.value;
        const reason = reasonSelect.value;

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
</script>
"""
