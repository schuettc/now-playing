export interface RGB {
  r: number;
  g: number;
  b: number;
}

export function scorePixel(r: number, g: number, b: number): number {
  const max = Math.max(r, g, b);
  const min = Math.min(r, g, b);
  const lightness = (max + min) / 2 / 255;
  if (lightness < 0.15 || lightness > 0.92) return -1;
  const saturation = max === 0 ? 0 : (max - min) / max;
  return saturation * (1 - Math.abs(lightness - 0.5));
}

export function pickDominantColor(data: Uint8ClampedArray | number[]): RGB | null {
  let best: RGB | null = null;
  let bestScore = -1;
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i];
    const g = data[i + 1];
    const b = data[i + 2];
    const score = scorePixel(r, g, b);
    if (score > bestScore) {
      bestScore = score;
      best = { r, g, b };
    }
  }
  return best;
}

export function sampleImageData(img: CanvasImageSource, size = 32): Uint8ClampedArray | null {
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.drawImage(img, 0, 0, size, size);
  return ctx.getImageData(0, 0, size, size).data;
}
