import "@testing-library/jest-dom/vitest";

// Guarded: the supervisor tests run in a Node environment, where there is no
// `window` to patch. Everything below is jsdom-only.
if (typeof window !== "undefined") {
  // jsdom has no layout engine, so anything measuring an element gets zeros.
  // Components that need real geometry (the waveform canvas) take their size
  // as a prop for exactly this reason.
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

  // Canvas is unimplemented in jsdom. The waveform draws through this, so
  // stub it rather than let every test that mounts the orb print an error.
  HTMLCanvasElement.prototype.getContext = (() => null) as never;
}
