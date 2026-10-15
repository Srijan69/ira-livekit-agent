import "@testing-library/jest-dom/vitest";

// jsdom has no layout engine, so anything measuring an element gets zeros.
// Components that need real geometry (the waveform canvas) take their size as
// a prop for exactly this reason.
if (!window.matchMedia) {
  window.matchMedia = ((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })) as typeof window.matchMedia;
}

// Canvas is unimplemented in jsdom. The waveform draws through this, so stub
// it rather than let every test that mounts the orb print an error.
if (!HTMLCanvasElement.prototype.getContext) {
  HTMLCanvasElement.prototype.getContext = (() => null) as never;
}
