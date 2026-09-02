document.addEventListener('DOMContentLoaded', () => {
    const canvas = document.getElementById('canvas-ambience');
    const ctx = canvas.getContext('2d');
    
    let particles = [];
    let isAshActive = false;
    let isRainActive = false;
    let isWindActive = false;
    let isCandleActive = false;
    
    // Audio Context
    let audioCtx;
    let rainNode;
    let windNode;
    
    function initAudio() {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
    }
    
    // Resize canvas
    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }
    window.addEventListener('resize', resize);
    resize();

    // Procedural Rain Noise
    function createRain() {
        const bufferSize = audioCtx.sampleRate * 2; 
        const buffer = audioCtx.createBuffer(1, bufferSize, audioCtx.sampleRate);
        const data = buffer.getChannelData(0);
        for (let i = 0; i < bufferSize; i++) {
            data[i] = Math.random() * 2 - 1;
        }
        
        const noise = audioCtx.createBufferSource();
        noise.buffer = buffer;
        noise.loop = true;
        
        const filter = audioCtx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 1000;
        
        const gain = audioCtx.createGain();
        gain.gain.value = 0.1;
        
        noise.connect(filter);
        filter.connect(gain);
        gain.connect(audioCtx.destination);
        
        noise.start();
        return { noise, gain };
    }

    // Procedural Wind Noise
    function createWind() {
        const bufferSize = audioCtx.sampleRate * 2;
        const buffer = audioCtx.createBuffer(1, bufferSize, audioCtx.sampleRate);
        const data = buffer.getChannelData(0);
        let lastOut = 0;
        for (let i = 0; i < bufferSize; i++) {
            let white = Math.random() * 2 - 1;
            data[i] = (lastOut + (0.02 * white)) / 1.02;
            lastOut = data[i];
            data[i] *= 3.5; 
        }
        
        const noise = audioCtx.createBufferSource();
        noise.buffer = buffer;
        noise.loop = true;
        
        const filter = audioCtx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.value = 400;
        
        const lfo = audioCtx.createOscillator();
        lfo.type = 'sine';
        lfo.frequency.value = 0.1;
        
        const lfoGain = audioCtx.createGain();
        lfoGain.gain.value = 200;
        
        lfo.connect(lfoGain);
        lfoGain.connect(filter.frequency);
        
        const gain = audioCtx.createGain();
        gain.gain.value = 0.2;
        
        noise.connect(filter);
        filter.connect(gain);
        gain.connect(audioCtx.destination);
        
        noise.start();
        lfo.start();
        return { noise, lfo, gain };
    }

    // Particles (Ash)
    class Ash {
        constructor() {
            this.reset();
        }
        reset() {
            this.x = Math.random() * canvas.width;
            this.y = canvas.height + 10;
            this.size = Math.random() * 2 + 1;
            this.speedY = Math.random() * -1 - 0.5;
            this.speedX = Math.random() * 2 - 1;
            this.opacity = Math.random() * 0.5 + 0.1;
        }
        update() {
            this.y += this.speedY;
            this.x += this.speedX;
            if (this.y < 0) this.reset();
        }
        draw() {
            ctx.fillStyle = `rgba(180, 76, 47, ${this.opacity})`;
            ctx.beginPath();
            ctx.arc(this.x, this.y, this.size, 0, Math.PI * 2);
            ctx.fill();
        }
    }

    for (let i = 0; i < 50; i++) {
        particles.push(new Ash());
    }

    function animate() {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        if (isAshActive) {
            particles.forEach(p => {
                p.update();
                p.draw();
            });
        }
        requestAnimationFrame(animate);
    }
    animate();

    const btnRain = document.getElementById('btn-rain');
    const btnWind = document.getElementById('btn-wind');
    const btnCandle = document.getElementById('btn-candle');
    const btnAsh = document.getElementById('btn-ash');
    const candleFlicker = document.getElementById('candle-flicker');

    btnRain.addEventListener('click', () => {
        initAudio();
        isRainActive = !isRainActive;
        btnRain.classList.toggle('active');
        if (isRainActive) {
            if(audioCtx.state === 'suspended') audioCtx.resume();
            rainNode = createRain();
        } else if (rainNode) {
            rainNode.noise.stop();
        }
    });

    btnWind.addEventListener('click', () => {
        initAudio();
        isWindActive = !isWindActive;
        btnWind.classList.toggle('active');
        if (isWindActive) {
            if(audioCtx.state === 'suspended') audioCtx.resume();
            windNode = createWind();
        } else if (windNode) {
            windNode.noise.stop();
            windNode.lfo.stop();
        }
    });

    btnCandle.addEventListener('click', () => {
        isCandleActive = !isCandleActive;
        btnCandle.classList.toggle('active');
        candleFlicker.style.display = isCandleActive ? 'block' : 'none';
    });

    btnAsh.addEventListener('click', () => {
        isAshActive = !isAshActive;
        btnAsh.classList.toggle('active');
    });
});
