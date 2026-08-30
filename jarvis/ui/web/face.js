/* Jarvis's face.
 *
 * A deliberately simple cartoon: a rounded frame, two eyes and a big mouth,
 * in the spirit of Eddy from Lab Rats. Everything is drawn as SVG paths that
 * get rewritten per frame, so expressions can be blended rather than swapped
 * between a handful of fixed images.
 *
 * The mouth is driven by the actual loudness of the speech being played, so it
 * moves with the words instead of flapping on a timer.
 */

const FACE_SVG = `
<svg class="jf-svg" viewBox="0 0 220 220" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <filter id="jf-glow" x="-50%" y="-50%" width="200%" height="200%">
      <feGaussianBlur stdDeviation="4" result="b"/>
      <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  <g class="jf-ink" filter="url(#jf-glow)">
    <path class="jf-frame" d="M 40 22 H 158 Q 196 22 196 62 V 158 Q 196 198 158 198 H 40 Z"/>
    <path class="jf-brow jf-brow-l"/>
    <path class="jf-brow jf-brow-r"/>
    <path class="jf-eye jf-eye-l"/>
    <path class="jf-eye jf-eye-r"/>
    <circle class="jf-pupil jf-pupil-l" r="11"/>
    <circle class="jf-pupil jf-pupil-r" r="11"/>
    <path class="jf-mouth"/>
    <path class="jf-tongue"/>
    <path class="jf-chin" d="M 96 176 L 110 190 L 124 176 M 110 190 V 198"/>
  </g>
</svg>`;

const EYE = { L: 82, R: 140, Y: 96 };

class JarvisFace {
  constructor(mount) {
    mount.innerHTML = FACE_SVG;
    this.root = mount.querySelector(".jf-svg");
    this.el = {
      eyeL: mount.querySelector(".jf-eye-l"),
      eyeR: mount.querySelector(".jf-eye-r"),
      pupilL: mount.querySelector(".jf-pupil-l"),
      pupilR: mount.querySelector(".jf-pupil-r"),
      browL: mount.querySelector(".jf-brow-l"),
      browR: mount.querySelector(".jf-brow-r"),
      mouth: mount.querySelector(".jf-mouth"),
      tongue: mount.querySelector(".jf-tongue"),
    };

    this.state = "idle";
    this.level = 0;        // microphone input
    this.speak = 0;        // playback loudness
    this.blink = 0;        // 0 open, 1 shut
    this.gaze = { x: 0, y: 0 };
    this.t = 0;

    this._scheduleBlink();
    this._tick();
  }

  setState(state) {
    if (state === this.state) return;
    this.state = state;
    this.root.dataset.state = state;
    if (state !== "speaking") this.speak = 0;
    if (state !== "listening") this.level = 0;
  }

  setLevel(v) { this.level = Math.min(1, Math.max(0, v * 9)); }
  setSpeakLevel(v) { this.speak = Math.min(1, Math.max(0, v)); }

  /* -- expression ------------------------------------------------------- */

  _scheduleBlink() {
    // Irregular timing; a metronome blink reads as broken, not alive.
    const wait = 2200 + Math.random() * 4200;
    setTimeout(() => {
      if (this.state !== "thinking") this._blinkNow();
      this._scheduleBlink();
    }, wait);
  }

  _blinkNow() {
    const start = performance.now();
    const run = (now) => {
      const p = (now - start) / 170;
      if (p >= 1) { this.blink = 0; return; }
      this.blink = Math.sin(p * Math.PI);   // shut and open again
      requestAnimationFrame(run);
    };
    requestAnimationFrame(run);
  }

  /** Closed, happy eye: an arc peaking upward. */
  _happyEye(cx) {
    const w = 24, h = 13;
    return `M ${cx - w} ${EYE.Y + 5} Q ${cx} ${EYE.Y - h} ${cx + w} ${EYE.Y + 5}`;
  }

  /** Open eye: a rounded almond whose height collapses as it blinks. */
  _openEye(cx, openness) {
    const w = 25;
    const h = Math.max(1.2, 20 * openness);
    return `M ${cx - w} ${EYE.Y} Q ${cx} ${EYE.Y - h} ${cx + w} ${EYE.Y}`
         + ` Q ${cx} ${EYE.Y + h} ${cx - w} ${EYE.Y} Z`;
  }

  _mouthPath(open, smile) {
    const left = 72, right = 148, top = 132;
    if (open < 0.05) {
      // Closed: a plain grin.
      return `M ${left} ${top - 4} Q 110 ${top + 22 * smile} ${right} ${top - 4}`;
    }
    // Open: flat on top, bowl underneath -- the wide cartoon laugh.
    const depth = top + 12 + 40 * open;
    return `M ${left} ${top} L ${right} ${top}`
         + ` Q ${right} ${depth} 110 ${depth}`
         + ` Q ${left} ${depth} ${left} ${top} Z`;
  }

  _tonguePath(open) {
    if (open < 0.45) return "";
    const top = 132 + 12 + 40 * open;
    const r = 13 * Math.min(1, (open - 0.45) / 0.55);
    return `M ${110 - r} ${top} Q 110 ${top + r * 1.4} ${110 + r} ${top} Z`;
  }

  _brow(cx, lift, tilt) {
    const w = 22;
    const y = EYE.Y - 30 - lift;
    return `M ${cx - w} ${y + tilt} Q ${cx} ${y - 6} ${cx + w} ${y - tilt}`;
  }

  /* -- frame loop ------------------------------------------------------- */

  _tick() {
    const loop = () => {
      this.t += 1 / 60;
      const s = this.state;
      let open = 0, smile = 1, lift = 0, tiltL = 0, tiltR = 0;
      let happyEyes = true, showPupils = false, eyeOpen = 1;

      if (s === "listening") {
        // Alert: eyes wide, brows up, mouth a small attentive oval that
        // breathes with what the microphone is hearing.
        happyEyes = false;
        showPupils = true;
        eyeOpen = 1;
        lift = 5 + this.level * 5;
        open = 0.2 + this.level * 0.38;
        smile = 0.5;
        this.gaze.x = Math.sin(this.t * 1.1) * 2;
        this.gaze.y = Math.cos(this.t * 0.8) * 1.5;
      } else if (s === "transcribing" || s === "thinking") {
        // Pondering: looking up and away, one brow raised, mouth a flat line.
        happyEyes = false;
        showPupils = true;
        eyeOpen = 0.62;
        open = 0;
        smile = 0.12;
        lift = 3;
        tiltL = 7;
        tiltR = -3;
        this.gaze.x = 4 + Math.sin(this.t * 0.9) * 3;
        this.gaze.y = -5;
      } else if (s === "speaking") {
        // Talking: the mouth follows the audio, with a floor so it never
        // looks frozen between syllables.
        happyEyes = true;
        open = 0.22 + this.speak * 0.78;
        smile = 1;
        lift = 2;
      } else {
        // Idle: the big open grin this face is known for, breathing gently.
        happyEyes = true;
        open = 0.42 + Math.sin(this.t * 1.3) * 0.05;
        smile = 1;
        this.gaze.x = Math.sin(this.t * 0.5) * 2;
        this.gaze.y = 0;
      }

      const shut = this.blink;
      if (happyEyes && shut < 0.5) {
        this.el.eyeL.setAttribute("d", this._happyEye(EYE.L));
        this.el.eyeR.setAttribute("d", this._happyEye(EYE.R));
        this.el.eyeL.classList.add("closed");
        this.el.eyeR.classList.add("closed");
      } else {
        const openness = Math.max(0.04, (happyEyes ? 0.55 : eyeOpen) * (1 - shut));
        this.el.eyeL.setAttribute("d", this._openEye(EYE.L, openness));
        this.el.eyeR.setAttribute("d", this._openEye(EYE.R, openness));
        this.el.eyeL.classList.remove("closed");
        this.el.eyeR.classList.remove("closed");
      }

      const pupilVisible = showPupils && shut < 0.4;
      [["pupilL", EYE.L], ["pupilR", EYE.R]].forEach(([key, cx]) => {
        const p = this.el[key];
        p.style.opacity = pupilVisible ? 1 : 0;
        p.setAttribute("cx", cx + this.gaze.x);
        p.setAttribute("cy", EYE.Y + this.gaze.y);
      });

      this.el.browL.setAttribute("d", this._brow(EYE.L, lift, tiltL));
      this.el.browR.setAttribute("d", this._brow(EYE.R, lift, -tiltR));

      this.el.mouth.setAttribute("d", this._mouthPath(open, smile));
      this.el.mouth.classList.toggle("open", open >= 0.05);

      const tongue = this._tonguePath(open);
      this.el.tongue.setAttribute("d", tongue);
      this.el.tongue.style.opacity = tongue ? 1 : 0;

      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }
}

window.JarvisFace = JarvisFace;
