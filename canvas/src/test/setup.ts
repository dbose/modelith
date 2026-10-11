import "@testing-library/jest-dom/vitest";

// jsdom lacks APIs React Flow touches at mount. Shim the minimum so an edge/canvas
// component can render in a test (the geometry itself is unit-tested separately).
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
if (!("ResizeObserver" in globalThis)) {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = ResizeObserverStub;
}

if (!(Element.prototype as unknown as { hasPointerCapture?: unknown }).hasPointerCapture) {
  Element.prototype.hasPointerCapture = () => false;
  Element.prototype.setPointerCapture = () => {};
  Element.prototype.releasePointerCapture = () => {};
}

// React Flow reads getBoundingClientRect for the flow<->screen transform; jsdom
// returns zeros, which is fine for our assertions (we test callback wiring, not pixels).
if (!(globalThis as unknown as { DOMMatrixReadOnly?: unknown }).DOMMatrixReadOnly) {
  class DOMMatrixReadOnlyStub {
    m22 = 1;
    constructor(_t?: string) {}
  }
  (globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly =
    DOMMatrixReadOnlyStub;
}
