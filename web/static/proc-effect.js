/** CPU Particle Effector · 通用能量核版（源自 xg.html，用于任务处理中 canvas） */
(function (global) {
  const NOISE_CHARS = "01░▒▓·∙‥…÷×+-=~≈∷⌁⌇/\\|¦".split("");
  const STAGE_METRIC = {
    parse: "activity",
    fetch_meta: "network",
    fetch_subtitle: "network",
    download: "network",
    extract_audio: "cpu",
    stt: "cpu",
    correct: "network",
  };
  const NET_FULL_KBPS = 3200;
  const INTENSITY_FLOOR = 0.16;

  const randNoiseChar = () => NOISE_CHARS[(Math.random() * NOISE_CHARS.length) | 0];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const easeOutCubic = (t) => 1 - Math.pow(1 - t, 3);

  function norm01(value, full) {
    return Math.min(1, Math.max(0, (Number(value) || 0) / full));
  }

  function computeLoad(metrics, stepKey) {
    const m = metrics && typeof metrics === "object" ? metrics : {};
    const metricKind = STAGE_METRIC[stepKey] || "activity";
    const cpuNorm = norm01(m.cpu, 100);
    const netNorm = norm01(m.network_kbps, NET_FULL_KBPS);
    const act = Math.min(1, Math.max(0, Number(m.activity) || 0.2));

    let raw = 0;
    if (metricKind === "network") raw = netNorm;
    else if (metricKind === "cpu") raw = cpuNorm;
    else raw = act * 0.5;

    if (raw < 0.04) raw = Math.max(raw, act * 0.28);

    return INTENSITY_FLOOR + Math.min(1, Math.max(0, raw)) * (1 - INTENSITY_FLOOR);
  }

  function create(canvas, options = {}) {
    if (!canvas) return null;
    const wrap = options.wrap || canvas.parentElement;
    const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const ctx = canvas.getContext("2d");
    if (!ctx) return null;

    let W = 0;
    let H = 0;
    let DPR = Math.min(window.devicePixelRatio || 1, 2);
    const CHAR_SIZE = 15;
    let cols = 0;
    let rows = 0;
    let columns = [];

    function initColumns() {
      columns = Array.from({ length: cols }, () => ({
        y: Math.random() * rows,
        speed: 0.4 + Math.random() * 0.6,
        active: Math.random() < 0.6,
        glyph: randNoiseChar(),
        flickerAt: Math.random() * 2,
      }));
    }

    function resize() {
      const rect = (wrap || canvas).getBoundingClientRect();
      W = Math.max(2, rect.width);
      H = Math.max(2, rect.height);
      canvas.width = Math.floor(W * DPR);
      canvas.height = Math.floor(H * DPR);
      ctx.setTransform(DPR, 0, 0, DPR, 0, 0);
      cols = Math.ceil(W / CHAR_SIZE);
      rows = Math.ceil(H / CHAR_SIZE);
      initColumns();
    }

    let cores = [];
    let ringPulses = [];
    let flashes = [];
    let sparks = [];
    let scanY = 0;
    let load = 0;
    let loadTarget = 0;
    let running = true;
    let raf = 0;
    let last = performance.now();
    let boxShadowTimer = 0;

    function tryTriggerCore(loadVal, dt, p) {
      if (reduceMotion) return;
      const chance = loadVal * loadVal * 1.0;
      if (Math.random() >= chance * dt) return;

      const cx = (0.15 + Math.random() * 0.7) * W;
      const cy = (0.2 + Math.random() * 0.6) * H;
      const n = 6 + Math.floor(loadVal * 7);
      const ringR = lerp(12, 24, loadVal);
      const gatherR = lerp(55, 140, loadVal);

      const dots = [];
      for (let i = 0; i < n; i++) {
        const slotAngle = (i / n) * Math.PI * 2;
        const tx = cx + Math.cos(slotAngle) * ringR;
        const ty = cy + Math.sin(slotAngle) * ringR;
        const fromAngle = Math.random() * Math.PI * 2;
        const fromR = gatherR * (0.6 + Math.random() * 0.6);
        dots.push({
          slotAngle,
          x: tx,
          y: ty,
          sx: cx + Math.cos(fromAngle) * fromR,
          sy: cy + Math.sin(fromAngle) * fromR,
          fx: 0,
          fy: 0,
        });
      }

      cores.push({
        cx,
        cy,
        dots,
        state: "gather",
        stateStart: performance.now(),
        gatherDur: 420 + Math.random() * 160,
        holdDur: 700 + Math.random() * 400,
        disperseDur: 420,
        rotation: 0,
        hue: p.hue,
      });
    }

    function updateCores(now, loadVal) {
      for (let i = cores.length - 1; i >= 0; i--) {
        const c = cores[i];
        const elapsed = now - c.stateStart;

        if (c.state === "gather") {
          const t = clamp(elapsed / c.gatherDur, 0, 1);
          const te = easeOutCubic(t);
          c.dots.forEach((d) => {
            d.fx = lerp(d.sx, d.x, te);
            d.fy = lerp(d.sy, d.y, te);
          });
          if (t >= 1) {
            ringPulses.push({
              x: c.cx,
              y: c.cy,
              start: now,
              duration: 480,
              maxR: lerp(45, 100, loadVal),
              hue: c.hue,
            });
            flashes.push({
              x: c.cx,
              y: c.cy,
              start: now,
              duration: 240,
              radius: lerp(80, 150, loadVal),
              hue: c.hue,
              peak: lerp(0.16, 0.36, loadVal),
            });
            c.state = "hold";
            c.stateStart = now;
          }
        } else if (c.state === "hold") {
          c.rotation += (0.3 + loadVal * 1.1) * (1 / 60);
          if (elapsed >= c.holdDur) {
            ringPulses.push({
              x: c.cx,
              y: c.cy,
              start: now,
              duration: 560,
              maxR: lerp(60, 130, loadVal),
              hue: c.hue,
            });
            c.dots.forEach((d) => {
              const n = 3 + Math.floor(loadVal * 4);
              for (let k = 0; k < n; k++) {
                const a = Math.random() * Math.PI * 2;
                const spd = 40 + Math.random() * 90 * (0.6 + loadVal);
                sparks.push({
                  x: c.cx + d.fx - c.cx,
                  y: c.cy + d.fy - c.cy,
                  vx: Math.cos(a) * spd,
                  vy: Math.sin(a) * spd - 20,
                  life: 1,
                  hue: c.hue,
                });
              }
            });
            c.state = "disperse";
            c.stateStart = now;
          }
        } else if (c.state === "disperse") {
          if (elapsed >= c.disperseDur) cores.splice(i, 1);
        }
      }
    }

    function drawCores() {
      cores.forEach((c) => {
        if (c.state === "gather") {
          c.dots.forEach((d) => {
            ctx.save();
            ctx.shadowColor = `hsl(${c.hue},75%,62%)`;
            ctx.shadowBlur = 9;
            ctx.fillStyle = `hsl(${c.hue},75%,66%)`;
            ctx.beginPath();
            ctx.arc(d.fx, d.fy, 1.6, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
          });
        } else if (c.state === "hold") {
          const shimmer = 0.82 + Math.sin(performance.now() / 130) * 0.18;
          ctx.save();
          ctx.strokeStyle = `hsla(${c.hue},70%,60%,${0.35 * shimmer})`;
          ctx.lineWidth = 1;
          ctx.beginPath();
          const ringR = Math.hypot(c.dots[0].x - c.cx, c.dots[0].y - c.cy);
          ctx.arc(c.cx, c.cy, ringR, 0, Math.PI * 2);
          ctx.stroke();
          ctx.restore();

          c.dots.forEach((d) => {
            const a = d.slotAngle + c.rotation;
            const r = Math.hypot(d.x - c.cx, d.y - c.cy);
            const x = c.cx + Math.cos(a) * r;
            const y = c.cy + Math.sin(a) * r;
            ctx.save();
            ctx.shadowColor = `hsl(${c.hue},85%,70%)`;
            ctx.shadowBlur = 13;
            ctx.fillStyle = `hsla(${c.hue},85%,80%,${shimmer})`;
            ctx.beginPath();
            ctx.arc(x, y, 2, 0, Math.PI * 2);
            ctx.fill();
            ctx.restore();
          });

          ctx.save();
          ctx.shadowColor = `hsl(${c.hue},90%,75%)`;
          ctx.shadowBlur = 16;
          ctx.fillStyle = `hsla(${c.hue},90%,85%,${shimmer})`;
          ctx.beginPath();
          ctx.arc(c.cx, c.cy, 2.2, 0, Math.PI * 2);
          ctx.fill();
          ctx.restore();
        }
      });
    }

    function updateAndDrawRings(now) {
      for (let i = ringPulses.length - 1; i >= 0; i--) {
        const r = ringPulses[i];
        const t = (now - r.start) / r.duration;
        if (t >= 1) {
          ringPulses.splice(i, 1);
          continue;
        }
        const te = easeOutCubic(t);
        ctx.save();
        ctx.globalCompositeOperation = "lighter";
        ctx.strokeStyle = `hsla(${r.hue},80%,65%,${(1 - t) * 0.55})`;
        ctx.lineWidth = lerp(2.5, 0.4, t);
        ctx.beginPath();
        ctx.arc(r.x, r.y, r.maxR * te, 0, Math.PI * 2);
        ctx.stroke();
        ctx.restore();
      }
    }

    function updateAndDrawFlashes(now) {
      for (let i = flashes.length - 1; i >= 0; i--) {
        const f = flashes[i];
        const t = (now - f.start) / f.duration;
        if (t >= 1) {
          flashes.splice(i, 1);
          continue;
        }
        const alpha = f.peak * (1 - t);
        const grad = ctx.createRadialGradient(f.x, f.y, 0, f.x, f.y, f.radius);
        grad.addColorStop(0, `hsla(${f.hue},90%,75%,${alpha})`);
        grad.addColorStop(1, `hsla(${f.hue},90%,60%,0)`);
        ctx.save();
        ctx.globalCompositeOperation = "lighter";
        ctx.fillStyle = grad;
        ctx.beginPath();
        ctx.arc(f.x, f.y, f.radius, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }

    function updateAndDrawSparks(dt) {
      for (let i = sparks.length - 1; i >= 0; i--) {
        const s = sparks[i];
        s.x += s.vx * dt;
        s.y += s.vy * dt;
        s.vy += 60 * dt;
        s.life -= dt * 1.4;
        if (s.life <= 0) {
          sparks.splice(i, 1);
          continue;
        }
        ctx.save();
        ctx.globalCompositeOperation = "lighter";
        ctx.globalAlpha = clamp(s.life, 0, 1);
        ctx.fillStyle = `hsl(${s.hue},85%,65%)`;
        ctx.beginPath();
        ctx.arc(s.x, s.y, 1.3, 0, Math.PI * 2);
        ctx.fill();
        ctx.restore();
      }
    }

    function paramsFor(l) {
      return {
        density: lerp(0.35, 0.95, l),
        fallSpeed: lerp(2.2, 11, l),
        trailAlpha: lerp(0.16, 0.3, l),
        hue: lerp(190, 350, l),
        sat: lerp(55, 90, l),
        light: lerp(55, 58, l),
        glowBlur: lerp(3, 12, l),
        flickerRate: lerp(0.4, 2.6, l),
        ghostTrails: l > 0.75 ? 2 : l > 0.4 ? 1 : 0,
        scanSpeed: lerp(28, 150, l),
        loadRaw: l,
      };
    }

    function drawScanline(p, dt) {
      scanY += p.scanSpeed * dt;
      if (scanY > H + 60) scanY = -60;
      const grad = ctx.createLinearGradient(0, scanY - 40, 0, scanY + 40);
      grad.addColorStop(0, `hsla(${p.hue},70%,60%,0)`);
      grad.addColorStop(0.5, `hsla(${p.hue},80%,65%,${lerp(0.05, 0.16, p.loadRaw)})`);
      grad.addColorStop(1, `hsla(${p.hue},70%,60%,0)`);
      ctx.save();
      ctx.globalCompositeOperation = "lighter";
      ctx.fillStyle = grad;
      ctx.fillRect(0, scanY - 40, W, 80);
      ctx.restore();
    }

    function frame(now) {
      raf = 0;
      if (!running) return;
      if (document.hidden) {
        raf = requestAnimationFrame(frame);
        return;
      }

      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      load = lerp(load, loadTarget, reduceMotion ? 0.03 : 0.07);
      const p = paramsFor(load);

      ctx.font = `${CHAR_SIZE - 3}px var(--mono, ui-monospace, monospace)`;
      ctx.textBaseline = "top";

      ctx.save();
      if (!reduceMotion && load > 0.85) {
        const s = (load - 0.85) / 0.15;
        ctx.translate((Math.random() - 0.5) * 2 * s, (Math.random() - 0.5) * 2 * s);
      }

      ctx.fillStyle = `rgba(5,6,8,${p.trailAlpha})`;
      ctx.fillRect(-4, -4, W + 8, H + 8);

      tryTriggerCore(load, dt, p);
      updateCores(now, load);
      drawScanline(p, dt);

      const baseColor = `hsl(${p.hue}, ${p.sat}%, ${p.light}%)`;

      for (let i = 0; i < columns.length; i++) {
        const col = columns[i];
        const x = i * CHAR_SIZE;

        if (!col.active) {
          if (Math.random() < 0.002 + load * 0.01) col.active = true;
          continue;
        }

        col.y += p.fallSpeed * dt * col.speed;
        col.flickerAt -= dt;
        if (col.flickerAt <= 0) {
          col.glyph = randNoiseChar();
          col.flickerAt = (1 / p.flickerRate) * (0.5 + Math.random());
        }

        const yy = col.y * CHAR_SIZE;
        for (let g = p.ghostTrails; g >= 1; g--) {
          ctx.save();
          ctx.globalAlpha = 0.14 / g;
          ctx.fillStyle = baseColor;
          ctx.fillText(col.glyph, x, yy - g * CHAR_SIZE * 0.55);
          ctx.restore();
        }
        ctx.save();
        ctx.shadowColor = baseColor;
        ctx.shadowBlur = p.glowBlur * 0.6;
        ctx.fillStyle = baseColor;
        ctx.fillText(col.glyph, x, yy);
        ctx.restore();

        if (col.y * CHAR_SIZE > H + CHAR_SIZE) {
          col.y = -Math.random() * 4;
          col.active = Math.random() < p.density;
          col.speed = 0.4 + Math.random() * 0.6;
        }
      }

      drawCores();
      updateAndDrawRings(now);
      updateAndDrawFlashes(now);
      updateAndDrawSparks(dt);
      ctx.restore();

      boxShadowTimer -= dt;
      if (wrap && boxShadowTimer <= 0) {
        boxShadowTimer = 0.1;
        wrap.style.boxShadow = `inset 0 0 70px hsla(${p.hue},60%,25%,${0.1 + load * 0.22})`;
      }

      raf = requestAnimationFrame(frame);
    }

    function onResize() {
      DPR = Math.min(window.devicePixelRatio || 1, 2);
      resize();
    }

    resize();
    window.addEventListener("resize", onResize);
    raf = requestAnimationFrame(frame);

    return {
      setLoad(value) {
        loadTarget = clamp(value, 0, 1);
      },
      getLoad() {
        return load;
      },
      destroy() {
        running = false;
        window.removeEventListener("resize", onResize);
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
        cores = [];
        ringPulses = [];
        flashes = [];
        sparks = [];
        columns = [];
        if (wrap) wrap.style.boxShadow = "";
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        ctx.clearRect(0, 0, canvas.width, canvas.height);
      },
    };
  }

  global.ProcEffect = { create, computeLoad };
})(typeof window !== "undefined" ? window : globalThis);
