import { expect, type Page } from "@playwright/test";

type DrawnText = { text: string; x: number; y: number; font: string; left: number; top: number; right: number; bottom: number };
type Drawing = { texts: DrawnText[]; iconStrokes: number };
declare global {
  interface Window {
    cardDrawings: WeakMap<HTMLCanvasElement, Drawing>;
    cardEncodingCount: number;
    cardRenderCount: number;
  }
}

// 只在測試攔截實際 Canvas 呼叫；產品不輸出文字副本或 renderer 診斷資料。
export async function recordCardDrawing(page: Page) {
  await page.addInitScript(() => {
    const drawings = new WeakMap<HTMLCanvasElement, Drawing>();
    window.cardDrawings = drawings;
    window.cardEncodingCount = 0;
    window.cardRenderCount = 0;
    const drawing = (canvas: HTMLCanvasElement) => {
      let result = drawings.get(canvas);
      if (!result) { result = { texts: [], iconStrokes: 0 }; drawings.set(canvas, result); window.cardRenderCount++; }
      return result;
    };
    const fillText = CanvasRenderingContext2D.prototype.fillText;
    CanvasRenderingContext2D.prototype.fillText = function (text, x, y, maxWidth) {
      if (this.canvas.width === 1200 && this.canvas.height >= 760) {
        const m = this.measureText(text), t = this.getTransform();
        drawing(this.canvas).texts.push({
          text, x: x * t.a + t.e, y: y * t.d + t.f, font: this.font,
          left: (x - m.actualBoundingBoxLeft) * t.a + t.e,
          right: (x + m.actualBoundingBoxRight) * t.a + t.e,
          top: (y - m.actualBoundingBoxAscent) * t.d + t.f,
          bottom: (y + m.actualBoundingBoxDescent) * t.d + t.f,
        });
      }
      if (maxWidth === undefined) fillText.call(this, text, x, y);
      else fillText.call(this, text, x, y, maxWidth);
    };
    const stroke = CanvasRenderingContext2D.prototype.stroke;
    CanvasRenderingContext2D.prototype.stroke = function (path?: Path2D) {
      if (this.canvas.width === 1200 && this.getTransform().a !== 1) drawing(this.canvas).iconStrokes++;
      if (path) stroke.call(this, path); else stroke.call(this);
    };
    const drawImage = CanvasRenderingContext2D.prototype.drawImage;
    CanvasRenderingContext2D.prototype.drawImage = function (...args: unknown[]) {
      const source = args[0];
      if (source instanceof HTMLCanvasElement && drawings.has(source)) drawings.set(this.canvas, drawings.get(source)!);
      Reflect.apply(drawImage, this, args);
    };
    const encode = HTMLCanvasElement.prototype.toBlob;
    HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
      if (this.width === 1200 && this.height >= 760) window.cardEncodingCount++;
      encode.call(this, callback, type, quality);
    };
  });
}

export async function cardDrawing(page: Page) {
  const image = page.locator(".visual-card > canvas.visual-concept-card");
  await expect(image).toBeVisible();
  return image.evaluate((image: HTMLCanvasElement) => window.cardDrawings.get(image)!);
}

export async function drawnText(page: Page) {
  return (await cardDrawing(page)).texts.map(line => line.text).join("");
}

export async function expectCardTextFits(page: Page) {
  const { texts } = await cardDrawing(page);
  const canvasHeight = await page.locator(".visual-card > canvas.visual-concept-card").evaluate((canvas: HTMLCanvasElement) => canvas.height);
  const visible = texts.filter(text => text.text.trim());
  expect(visible.length).toBeGreaterThan(0);
  expect(visible.filter(t => t.left < 0 || t.top < 0 || t.right > 1200 || t.bottom > canvasHeight)).toEqual([]);
  for (let i = 0; i < visible.length; i++) for (let j = i + 1; j < visible.length; j++) {
    const a = visible[i], b = visible[j];
    expect(a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top,
      `overlapping text: ${a.text} / ${b.text}`).toBe(true);
  }
}
