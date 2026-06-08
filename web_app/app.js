document.addEventListener('DOMContentLoaded', () => {
    console.log('CowFarm IoT Web App Loaded');

    const alertsContainer = document.getElementById('alerts-container');
    const activeAlertsContainer = document.getElementById('active-alerts-container');
    
    if (alertsContainer) {
        fetchAlerts();
        setInterval(fetchAlerts, 5000);
    }
    if (activeAlertsContainer) {
        fetchActiveAlerts();
        setInterval(fetchActiveAlerts, 5000);
    }

    async function fetchActiveAlerts() {
        try {
            const response = await fetch('/api/active');
            if (!response.ok) throw new Error('Network response was not ok');
            const active = await response.json();
            
            const entries = Object.values(active);
            if (entries.length === 0) {
                activeAlertsContainer.innerHTML = '<p style="color: var(--secondary-color); font-weight: bold; margin-top: 1rem;">Все показатели в норме.</p>';
                return;
            }

            let html = '<ul style="list-style: none; margin-top: 1rem;">';
            entries.forEach(a => {
                const color = a.level === 'danger' ? '#d32f2f' : '#f57c00';
                const bg = a.level === 'danger' ? '#ffebee' : '#fff3e0';
                const icon = a.level === 'danger' ? '🚨' : '⚠️';
                html += `
                    <li style="border-left: 4px solid ${color}; background: ${bg}; padding: 1rem; margin-bottom: 0.5rem; border-radius: 0 4px 4px 0;">
                        <div style="font-weight: bold; color: #333;">${icon} Корова: ${a.cowId}</div>
                        <div style="color: #555; margin: 0.25rem 0;">${a.message}</div>
                        <div style="font-size: 0.85rem; color: #666;">Темп: ${a.temperature}°C | Акт: ${a.activity} | Вероятность болезни: ${a.illness_probability}%</div>
                        <div style="font-size: 0.8rem; color: #999;">Обновлено: ${a.updated}</div>
                    </li>
                `;
            });
            html += '</ul>';
            activeAlertsContainer.innerHTML = html;
        } catch (error) {
            console.error('Failed to fetch active alerts:', error);
        }
    }

    async function fetchAlerts() {
        try {
            const response = await fetch('/api/alerts');
            if (!response.ok) throw new Error('Network response was not ok');
            const alerts = await response.json();
            
            renderAlerts(alerts);
        } catch (error) {
            console.error('Failed to fetch alerts:', error);
            if (alertsContainer.innerHTML.includes('Загрузка')) {
                alertsContainer.innerHTML = '<p style="color: red; margin-top: 1rem;">Ошибка подключения к серверу. Убедитесь, что backend запущен.</p>';
            }
        }
    }

    function renderAlerts(alerts) {
        if (alerts.length === 0) {
            alertsContainer.innerHTML = '<p style="color: var(--secondary-color); font-weight: bold; margin-top: 1rem;">В данный момент тревог нет. Показатели стада в норме.</p>';
            return;
        }

        let html = '<ul style="list-style: none; margin-top: 1rem;">';
        alerts.forEach(alert => {
            const color = alert.level === 'danger' ? 'red' : '#EAB839';
            const icon = alert.level === 'danger' ? '🚨' : '⚠️';
            html += `
                <li style="border-left: 4px solid ${color}; background: #fdfdfd; padding: 1rem; margin-bottom: 0.5rem; border-radius: 0 4px 4px 0; box-shadow: 0 1px 3px rgba(0,0,0,0.1);">
                    <div style="font-weight: bold; color: #333;">${icon} Корова: ${alert.cowId}</div>
                    <div style="color: #555; margin: 0.25rem 0;">${alert.message}</div>
                    <div style="font-size: 0.8rem; color: #999;">Время: ${alert.timestamp}</div>
                </li>
            `;
        });
        html += '</ul>';
        alertsContainer.innerHTML = html;
    }

    // Map logic (Yandex Maps)
    const mapElement = document.getElementById('map');
    if (mapElement && typeof ymaps !== 'undefined') {
        ymaps.ready(initMap);

        function initMap() {
            // Initialize map centered roughly on a farm location (default mock coordinates)
            const map = new ymaps.Map("map", {
                center: [55.75, 37.61],
                zoom: 16,
                controls: ['zoomControl', 'typeSelector', 'fullscreenControl']
            });

            const markers = {};

            async function fetchCowsLocation() {
                try {
                    const response = await fetch('/api/cows');
                    if (!response.ok) throw new Error('Network response was not ok');
                    const cows = await response.json();
                    
                    for (const cowId in cows) {
                        const cow = cows[cowId];
                        if (cow.lat !== undefined && cow.lon !== undefined) {
                            const pos = [cow.lat, cow.lon];
                            
                            let preset = 'islands#greenIcon';
                            // Simple logic to color the marker based on temperature/activity
                            if (cow.temperature > 39.5 || cow.temperature < 37.5) {
                                preset = 'islands#redIcon';
                            } else if (cow.temperature > 39.0 || cow.activity < 30) {
                                preset = 'islands#orangeIcon';
                            }

                            const popupContent = `
                                <div style="padding: 5px;">
                                    <b>Корова: ${cowId}</b><br>
                                    Температура: ${cow.temperature}°C<br>
                                    Активность: ${cow.activity}
                                </div>
                            `;

                            if (markers[cowId]) {
                                // Update existing marker
                                markers[cowId].geometry.setCoordinates(pos);
                                markers[cowId].properties.set('balloonContent', popupContent);
                                markers[cowId].options.set('preset', preset);
                            } else {
                                // Create new marker
                                const placemark = new ymaps.Placemark(pos, {
                                    balloonContent: popupContent,
                                    hintContent: `Корова ${cowId}`
                                }, {
                                    preset: preset
                                });
                                map.geoObjects.add(placemark);
                                markers[cowId] = placemark;
                            }
                        }
                    }
                } catch (error) {
                    console.error('Failed to fetch cows location:', error);
                }
            }

            fetchCowsLocation();
            setInterval(fetchCowsLocation, 5000);
        }
    }

    // Settings page logic
    const settingsForm = document.getElementById('settings-form');
    if (settingsForm) {
        const fields = [
            'temp_warning_high', 'temp_critical_high',
            'temp_warning_low', 'temp_critical_low',
            'activity_warning_low', 'activity_critical_low',
            'activity_warning_high', 'activity_critical_high',
            'cooldown_sec'
        ];

        fetch('/api/thresholds')
            .then(r => r.json())
            .then(data => {
                fields.forEach(f => {
                    const el = document.getElementById(f);
                    if (el && data[f] !== undefined) el.value = data[f];
                });
            })
            .catch(err => console.error('Failed to load thresholds:', err));

        settingsForm.addEventListener('submit', async (e) => {
            e.preventDefault();
            const body = {};
            fields.forEach(f => {
                const el = document.getElementById(f);
                if (el) body[f] = parseFloat(el.value);
            });

            const statusEl = document.getElementById('save-status');
            try {
                const res = await fetch('/api/thresholds', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(body)
                });
                if (res.ok) {
                    statusEl.style.color = 'green';
                    statusEl.textContent = 'Настройки сохранены!';
                } else {
                    throw new Error('Server error');
                }
            } catch (err) {
                statusEl.style.color = 'red';
                statusEl.textContent = 'Ошибка сохранения';
            }
            setTimeout(() => { statusEl.textContent = ''; }, 3000);
        });
    }
});
