/* Quiz interactivo — click en opciones, feedback + confetti al acertar */
(function () {
  'use strict';

  const FEEDBACK = {
    correct: [
      '¡Bien! 🎯',
      'GG 🔥',
      'Máster de la UE confirmado.',
      'Crack. Apúntatelo en el currículum.',
      '💯'
    ],
    wrong: [
      'Cerca, pero no. La verde es la buena.',
      'Oof. Pero ahora ya lo sabes.',
      'Casi. Respira, mira de nuevo.',
      '🤏 Te ha faltado.',
      'No pasa nada, se aprende así.'
    ]
  };

  function pick(arr) { return arr[Math.floor(Math.random() * arr.length)]; }

  function handleClick(e) {
    const option = e.currentTarget;
    const container = option.closest('[data-quiz]');
    if (!container) return;
    if (container.dataset.answered === 'true') return;

    const options = container.querySelectorAll('.option');
    const feedback = container.querySelector('.feedback');
    const isCorrect = option.hasAttribute('data-correct');

    options.forEach(o => {
      o.classList.add('locked');
      if (o.hasAttribute('data-correct')) o.classList.add('correct');
    });
    if (!isCorrect) option.classList.add('wrong');

    if (feedback) {
      feedback.textContent = isCorrect ? pick(FEEDBACK.correct) : pick(FEEDBACK.wrong);
      feedback.classList.add('show');
    }

    container.dataset.answered = 'true';
    if (isCorrect) fireConfetti();
  }

  function init() {
    document.querySelectorAll('[data-quiz] .option').forEach(btn => {
      btn.addEventListener('click', handleClick);
    });
  }

  /* ---------- Mini confetti (canvas 2D, sin deps) ---------- */
  let confettiCtx = null;
  let confettiCanvas = null;
  let particles = [];
  let animationId = null;

  function setupCanvas() {
    confettiCanvas = document.getElementById('confetti-canvas');
    if (!confettiCanvas) return;
    const dpr = window.devicePixelRatio || 1;
    confettiCanvas.width = window.innerWidth * dpr;
    confettiCanvas.height = window.innerHeight * dpr;
    confettiCanvas.style.width = window.innerWidth + 'px';
    confettiCanvas.style.height = window.innerHeight + 'px';
    confettiCtx = confettiCanvas.getContext('2d');
    confettiCtx.scale(dpr, dpr);
  }

  function fireConfetti() {
    if (!confettiCanvas) setupCanvas();
    if (!confettiCtx) return;

    const colors = ['#FFCC00', '#003399', '#00e0ff', '#ff4ecd', '#00ff9c'];
    const w = window.innerWidth;
    for (let i = 0; i < 120; i++) {
      particles.push({
        x: w / 2 + (Math.random() - 0.5) * 100,
        y: window.innerHeight / 2,
        vx: (Math.random() - 0.5) * 16,
        vy: Math.random() * -18 - 4,
        g: 0.5,
        size: Math.random() * 8 + 4,
        color: colors[Math.floor(Math.random() * colors.length)],
        rot: Math.random() * Math.PI * 2,
        vr: (Math.random() - 0.5) * 0.3,
        life: 120
      });
    }
    if (!animationId) loop();
  }

  function loop() {
    confettiCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    particles = particles.filter(p => p.life > 0);
    particles.forEach(p => {
      p.vy += p.g;
      p.x += p.vx;
      p.y += p.vy;
      p.rot += p.vr;
      p.life -= 1;
      confettiCtx.save();
      confettiCtx.translate(p.x, p.y);
      confettiCtx.rotate(p.rot);
      confettiCtx.fillStyle = p.color;
      confettiCtx.globalAlpha = Math.min(1, p.life / 40);
      confettiCtx.fillRect(-p.size / 2, -p.size / 2, p.size, p.size * 0.4);
      confettiCtx.restore();
    });
    if (particles.length > 0) {
      animationId = requestAnimationFrame(loop);
    } else {
      cancelAnimationFrame(animationId);
      animationId = null;
      confettiCtx.clearRect(0, 0, window.innerWidth, window.innerHeight);
    }
  }

  window.addEventListener('resize', () => { if (confettiCanvas) setupCanvas(); });

  window.UEQuiz = { init };
})();
