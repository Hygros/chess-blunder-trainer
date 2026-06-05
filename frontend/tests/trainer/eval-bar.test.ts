import { describe, it, expect } from 'vitest';
import { playerPovToWhitePov, updateEvalBar } from '../../src/shared/eval-bar';

function makeMockEl(): HTMLElement {
  return { style: { height: '' }, textContent: '', className: '' } as unknown as HTMLElement;
}

describe('updateEvalBar', () => {
  it('shows +0.0 at equal position', () => {
    const fill = makeMockEl();
    const value = makeMockEl();
    updateEvalBar(0, fill, value);
    expect(value.textContent).toBe('+0.0');
    expect(fill.style.height).toBe('50%');
    expect(value.className).toBe('eval-value positive');
  });

  it('shows positive eval for white advantage as white', () => {
    const fill = makeMockEl();
    const value = makeMockEl();
    updateEvalBar(200, fill, value);
    expect(value.textContent).toBe('+2.0');
    expect(parseFloat(fill.style.height)).toBeGreaterThan(50);
    expect(value.className).toBe('eval-value positive');
  });

  it('uses white POV directly for negative evals', () => {
    const fill = makeMockEl();
    const value = makeMockEl();
    updateEvalBar(-200, fill, value);
    expect(parseFloat(fill.style.height)).toBeLessThan(50);
    expect(value.className).toBe('eval-value negative');
  });

  it('clamps extreme values', () => {
    const fill = makeMockEl();
    const value = makeMockEl();
    updateEvalBar(9999, fill, value);
    expect(fill.style.height).toBe('100%');
  });

  it('displays mate symbol for very large values', () => {
    const fill = makeMockEl();
    const value = makeMockEl();
    updateEvalBar(10001, fill, value);
    expect(value.textContent).toBe('+M');
  });

  it('displays negative mate symbol', () => {
    const fill = makeMockEl();
    const value = makeMockEl();
    updateEvalBar(-10001, fill, value);
    expect(value.textContent).toBe('-M');
  });

  it('handles negative eval', () => {
    const fill = makeMockEl();
    const value = makeMockEl();
    updateEvalBar(-150, fill, value);
    expect(value.textContent).toBe('-1.5');
    expect(parseFloat(fill.style.height)).toBeLessThan(50);
    expect(value.className).toBe('eval-value negative');
  });

  it('converts player POV scores to white POV', () => {
    expect(playerPovToWhitePov(200, 'white')).toBe(200);
    expect(playerPovToWhitePov(200, 'black')).toBe(-200);
  });
});
