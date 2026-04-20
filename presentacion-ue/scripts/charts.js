/* Chart.js configs — se renderizan lazy al cambiar de slide */
(function () {
  'use strict';

  const charts = {};

  function commonOpts() {
    return {
      responsive: true,
      maintainAspectRatio: true,
      plugins: {
        legend: {
          position: 'bottom',
          labels: {
            color: '#c9d1e5',
            font: { family: 'Inter', size: 14, weight: '600' },
            padding: 16,
            usePointStyle: true,
            pointStyle: 'circle'
          }
        },
        tooltip: {
          backgroundColor: '#111730',
          titleColor: '#FFCC00',
          bodyColor: '#f7f9ff',
          borderColor: 'rgba(255, 204, 0, 0.3)',
          borderWidth: 1,
          padding: 12
        }
      }
    };
  }

  function renderBudget(canvas) {
    if (charts.budget) return;
    const ctx = canvas.getContext('2d');
    charts.budget = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: ['Aportación países (PIB)', 'IVA recaudado', 'Aduanas y aranceles'],
        datasets: [{
          data: [70, 15, 15],
          backgroundColor: ['#FFCC00', '#2855e8', '#00e0ff'],
          borderColor: '#0B0F1A',
          borderWidth: 4,
          hoverOffset: 12
        }]
      },
      options: {
        ...commonOpts(),
        cutout: '62%',
        plugins: {
          ...commonOpts().plugins,
          tooltip: {
            ...commonOpts().plugins.tooltip,
            callbacks: {
              label: (ctx) => ` ${ctx.label}: ${ctx.parsed}%`
            }
          }
        }
      }
    });
  }

  function onSlideChanged(slide) {
    if (!slide) return;
    const canvases = slide.querySelectorAll('canvas[data-chart]');
    canvases.forEach(cv => {
      const kind = cv.dataset.chart;
      if (kind === 'budget') renderBudget(cv);
    });
  }

  function init() {
    const current = document.querySelector('.reveal .slides section.present');
    if (current) onSlideChanged(current);
  }

  window.UECharts = { init, onSlideChanged };
})();
