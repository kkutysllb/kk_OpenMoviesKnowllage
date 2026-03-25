/**
 * Slide Renderer - Server-side slide rendering using @napi-rs/canvas
 *
 * Renders slides to PNG images for video export.
 * Supports: text, image, shape, chart, table, latex elements.
 */

import { createCanvas, loadImage, GlobalFonts, type SKRSContext2D } from '@napi-rs/canvas';
import { existsSync } from 'fs';
import type {
  Slide,
  PPTElement,
  PPTTextElement,
  PPTImageElement,
  PPTShapeElement,
  PPTLineElement,
  PPTChartElement,
  PPTTableElement,
  PPTLatexElement,
  PPTVideoElement,
  PPTAudioElement,
  SlideBackground,
  Gradient,
} from '@/lib/types/slides';
import type { RenderContext } from './types';
import { RESOLUTION_DIMENSIONS, type VideoResolution } from './types';
import { createLogger } from '@/lib/logger';

const log = createLogger('SlideRenderer');

// Chinese/CJK font candidates (macOS + Linux)
const CJK_FONT_CANDIDATES = [
  // macOS
  '/System/Library/Fonts/STHeiti Medium.ttc',
  '/System/Library/Fonts/STHeiti Light.ttc',
  '/System/Library/Fonts/Arial Unicode.ttf',
  '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
  // Linux
  '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
  '/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc',
  '/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf',
  '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
];

let fontsRegistered = false;

/**
 * Register CJK fonts for server-side canvas rendering
 */
function ensureFontsRegistered(): void {
  if (fontsRegistered) return;
  fontsRegistered = true;

  for (const fontPath of CJK_FONT_CANDIDATES) {
    if (existsSync(fontPath)) {
      try {
        GlobalFonts.registerFromPath(fontPath);
        log.info(`Registered font: ${fontPath}`);
      } catch (err) {
        log.warn(`Failed to register font ${fontPath}:`, err);
      }
    }
  }
}

// Default font family (will fallback to registered CJK font)
const DEFAULT_FONT = 'STHeiti Medium, Arial Unicode MS, NotoSansCJK, sans-serif';

/**
 * Render a slide to a PNG buffer
 */
export async function renderSlideToPng(
  slide: Slide,
  resolution: VideoResolution = '1080p',
): Promise<Buffer> {
  // Ensure CJK fonts are registered
  ensureFontsRegistered();

  const { width, height } = RESOLUTION_DIMENSIONS[resolution];
  const viewportSize = slide.viewportSize || 1000;
  const viewportRatio = slide.viewportRatio || 0.5625;
  const scale = width / viewportSize;

  const canvas = createCanvas(width, height);
  const ctx = canvas.getContext('2d') as SKRSContext2D;

  const renderContext: RenderContext = {
    width,
    height,
    scale,
    viewportSize,
    viewportRatio,
  };

  // 1. Render background
  renderBackground(ctx, slide, renderContext);

  // 2. Render elements in order
  for (const element of slide.elements || []) {
    try {
      await renderElement(ctx, element, renderContext);
    } catch (err) {
      log.warn(`Failed to render element ${element.id}:`, err);
    }
  }

  // 3. Export to PNG buffer
  return canvas.toBuffer('image/png');
}

/**
 * Render slide background
 */
function renderBackground(
  ctx: SKRSContext2D,
  slide: Slide,
  rc: RenderContext,
): void {
  const { width, height } = rc;
  const bg = slide.background;

  // Default to theme background color
  const bgColor = slide.theme?.backgroundColor || '#ffffff';

  if (!bg || bg.type === 'solid') {
    // Solid color background
    ctx.fillStyle = bg?.color || bgColor;
    ctx.fillRect(0, 0, width, height);
  } else if (bg.type === 'gradient' && bg.gradient) {
    // Gradient background
    renderGradientFill(ctx, bg.gradient, 0, 0, width, height);
  } else if (bg.type === 'image' && bg.image?.src) {
    // Image background
    renderImageBackground(ctx, bg, width, height);
  } else {
    // Fallback to solid
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, width, height);
  }
}

/**
 * Render gradient fill
 */
function renderGradientFill(
  ctx: SKRSContext2D,
  gradient: Gradient,
  x: number,
  y: number,
  w: number,
  h: number,
): void {
  let grad: CanvasGradient;

  if (gradient.type === 'linear') {
    const angle = (gradient.rotate * Math.PI) / 180;
    const cx = x + w / 2;
    const cy = y + h / 2;
    const len = Math.max(w, h);
    grad = ctx.createLinearGradient(
      cx - Math.cos(angle) * len / 2,
      cy - Math.sin(angle) * len / 2,
      cx + Math.cos(angle) * len / 2,
      cy + Math.sin(angle) * len / 2,
    );
  } else {
    grad = ctx.createRadialGradient(
      x + w / 2,
      y + h / 2,
      0,
      x + w / 2,
      y + h / 2,
      Math.max(w, h) / 2,
    );
  }

  for (const colorStop of gradient.colors) {
    grad.addColorStop(colorStop.pos / 100, colorStop.color);
  }

  ctx.fillStyle = grad;
  ctx.fillRect(x, y, w, h);
}

/**
 * Render image background
 */
function renderImageBackground(
  ctx: SKRSContext2D,
  bg: SlideBackground,
  width: number,
  height: number,
): void {
  // For now, just fill with a placeholder color
  // Image backgrounds would need async loading
  ctx.fillStyle = '#f0f0f0';
  ctx.fillRect(0, 0, width, height);
}

/**
 * Render a single element
 */
async function renderElement(
  ctx: SKRSContext2D,
  element: PPTElement,
  rc: RenderContext,
): Promise<void> {
  switch (element.type) {
    case 'text':
      renderTextElement(ctx, element as PPTTextElement, rc);
      break;
    case 'image':
      await renderImageElement(ctx, element as PPTImageElement, rc);
      break;
    case 'shape':
      renderShapeElement(ctx, element as PPTShapeElement, rc);
      break;
    case 'line':
      renderLineElement(ctx, element as PPTLineElement, rc);
      break;
    case 'chart':
      renderChartElement(ctx, element as PPTChartElement, rc);
      break;
    case 'table':
      renderTableElement(ctx, element as PPTTableElement, rc);
      break;
    case 'latex':
      renderLatexElement(ctx, element as PPTLatexElement, rc);
      break;
    case 'video':
    case 'audio':
      // Skip video/audio elements in static rendering
      break;
  }
}

/**
 * Parsed text run (inline span)
 */
interface TextRun {
  text: string;
  fontSize: number;    // pt
  color: string;
  bold: boolean;
  italic: boolean;
  fontFamily: string;
}

/**
 * Parsed paragraph
 */
interface TextParagraph {
  runs: TextRun[];
  align: CanvasTextAlign;
  spaceBefore: number; // px extra space before paragraph
}

/**
 * Parse font-size from CSS style string. Supports pt and px units.
 * Returns size in pt (canvas uses pt-based font strings).
 */
function parseFontSize(styleStr: string, fallback: number): number {
  // Try pt first
  const ptMatch = styleStr.match(/font-size:\s*([\d.]+)pt/i);
  if (ptMatch) return parseFloat(ptMatch[1]);
  // Try px (convert px -> pt: 1pt = 1.333px at 96dpi)
  const pxMatch = styleStr.match(/font-size:\s*([\d.]+)px/i);
  if (pxMatch) return parseFloat(pxMatch[1]) * 0.75; // px * (72/96)
  return fallback;
}

/**
 * Parse HTML text content into paragraphs and runs
 */
function parseHtmlContent(
  html: string,
  defaultColor: string,
  defaultFontName: string,
): TextParagraph[] {
  const paragraphs: TextParagraph[] = [];

  // Split by <p> tags (non-greedy, handles empty paragraphs)
  const pRegex = /<p([^>]*)>([\s\S]*?)<\/p>/gi;
  const pMatches: Array<{ attrs: string; inner: string }> = [];
  let pm: RegExpExecArray | null;
  while ((pm = pRegex.exec(html)) !== null) {
    pMatches.push({ attrs: pm[1], inner: pm[2] });
  }

  // Fallback: no <p> tags, treat whole html as one paragraph
  if (pMatches.length === 0) {
    pMatches.push({ attrs: '', inner: html });
  }

  for (const { attrs, inner } of pMatches) {
    // Extract paragraph-level align and font-size from style attribute
    const pStyle = attrs.match(/style="([^"]*)"/i)?.[1] || '';
    const alignMatch = pStyle.match(/text-align:\s*(left|center|right|justify)/i)
      || inner.match(/text-align:\s*(left|center|right|justify)/i);
    let align: CanvasTextAlign = 'left';
    if (alignMatch) {
      align = alignMatch[1] === 'justify' ? 'left' : (alignMatch[1] as CanvasTextAlign);
    }

    // Inherit font-size from <p> style if present
    const pFontSize = parseFontSize(pStyle, 18);
    const pColorMatch = pStyle.match(/color:\s*(#[0-9a-fA-F]{3,8}|rgb\([^)]+\))/i);
    const pColor = pColorMatch ? pColorMatch[1].trim() : defaultColor;

    // Flatten nested spans: extract all leaf-level text with their styles
    // We walk the inner HTML and collect runs
    const runs: TextRun[] = [];
    extractRuns(inner, pColor, defaultFontName, runs, pFontSize, false, false);

    paragraphs.push({ runs, align, spaceBefore: 0 });
  }

  return paragraphs;
}

/**
 * Recursively extract styled text runs from HTML fragment
 */
function extractRuns(
  html: string,
  inheritColor: string,
  inheritFont: string,
  out: TextRun[],
  inheritFontSize: number = 18,
  inheritBold: boolean = false,
  inheritItalic: boolean = false,
): void {
  if (!html.trim()) return;

  // Tokenize: split html into text nodes and tags
  const tokenReg = /(<[^>]+>)|([^<]+)/g;
  let m: RegExpExecArray | null;
  const tokens: Array<{ tag: string | null; text: string | null }> = [];
  while ((m = tokenReg.exec(html)) !== null) {
    if (m[1]) tokens.push({ tag: m[1], text: null });
    else if (m[2]) tokens.push({ tag: null, text: m[2] });
  }

  // Stack-based parser
  interface StackFrame {
    fontSize: number;
    color: string;
    bold: boolean;
    italic: boolean;
    fontFamily: string;
  }

  const stack: StackFrame[] = [{
    fontSize: inheritFontSize,
    color: inheritColor,
    bold: inheritBold,
    italic: inheritItalic,
    fontFamily: inheritFont,
  }];

  for (const token of tokens) {
    const top = stack[stack.length - 1];

    if (token.text !== null) {
      // Text node
      const text = token.text
        .replace(/&nbsp;/g, ' ')
        .replace(/&amp;/g, '&')
        .replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>')
        .replace(/&quot;/g, '"');
      if (text) {
        out.push({
          text,
          fontSize: top.fontSize,
          color: top.color,
          bold: top.bold,
          italic: top.italic,
          fontFamily: top.fontFamily,
        });
      }
      continue;
    }

    const tag = token.tag!;

    // Self-closing or br
    if (/^<br/i.test(tag)) {
      out.push({ text: '\n', fontSize: top.fontSize, color: top.color,
        bold: top.bold, italic: top.italic, fontFamily: top.fontFamily });
      continue;
    }

    // Closing tag
    if (/^<\//.test(tag)) {
      if (stack.length > 1) stack.pop();
      continue;
    }

    // Opening tag - parse attributes
    const tagNameMatch = tag.match(/^<(\w+)/i);
    if (!tagNameMatch) continue;
    const tagName = tagNameMatch[1].toLowerCase();

    const styleStr = tag.match(/style="([^"]*)"/i)?.[1] || '';
    const colorMatch = styleStr.match(/color:\s*(#[0-9a-fA-F]{3,8}|rgb\([^)]+\))/i);
    const boldMatch = styleStr.match(/font-weight:\s*(bold|[6-9]\d\d)/i)
      || tagName === 'b' || tagName === 'strong';
    const italicMatch = styleStr.match(/font-style:\s*italic/i)
      || tagName === 'i' || tagName === 'em';
    const fontFamilyMatch = styleStr.match(/font-family:\s*([^;,"']+)/i);

    stack.push({
      fontSize: parseFontSize(styleStr, top.fontSize),
      color: colorMatch ? colorMatch[1].trim() : top.color,
      bold: !!(boldMatch) || top.bold,
      italic: !!(italicMatch) || top.italic,
      fontFamily: fontFamilyMatch ? fontFamilyMatch[1].trim() : top.fontFamily,
    });
  }
}

/**
 * Render text element
 */
function renderTextElement(
  ctx: SKRSContext2D,
  element: PPTTextElement,
  rc: RenderContext,
): void {
  const { scale } = rc;
  const x = element.left * scale;
  const y = element.top * scale;
  const width = element.width * scale;
  const height = element.height * scale;

  ctx.save();

  // Apply rotation
  if (element.rotate) {
    const cx = x + width / 2;
    const cy = y + height / 2;
    ctx.translate(cx, cy);
    ctx.rotate((element.rotate * Math.PI) / 180);
    ctx.translate(-cx, -cy);
  }

  // Fill background if specified
  if (element.fill) {
    ctx.fillStyle = element.fill;
    ctx.fillRect(x, y, width, height);
  }

  // Parse HTML content into paragraphs
  const paragraphs = parseHtmlContent(
    element.content,
    element.defaultColor || '#333333',
    element.defaultFontName || DEFAULT_FONT,
  );

  const lineHeightMultiplier = element.lineHeight || 1.5;
  const paragraphSpace = (element.paragraphSpace || 5) * scale;

  let curY = y;

  for (const para of paragraphs) {
    if (curY >= y + height) break;

    // Calculate max font size in this paragraph for line height
    const maxFontPt = para.runs.reduce((m, r) => Math.max(m, r.fontSize), 18);
    const fontPx = maxFontPt * scale * (96 / 72); // pt → px at 96dpi
    const lineH = fontPx * lineHeightMultiplier;

    // Compute line layout: wrap runs into lines
    const paraLines = wrapRuns(ctx, para.runs, scale, width);

    for (const lineRuns of paraLines) {
      if (curY + lineH > y + height + lineH * 0.5) break;

      // Compute line width for alignment
      let lineWidth = 0;
      for (const lr of lineRuns) {
        const fPx = lr.run.fontSize * scale * (96 / 72);
        const weight = lr.run.bold ? 'bold' : 'normal';
        const style = lr.run.italic ? 'italic' : 'normal';
        ctx.font = `${style} ${weight} ${fPx}px ${DEFAULT_FONT}`;
        lineWidth += ctx.measureText(lr.text).width;
      }

      let lineX = x;
      if (para.align === 'center') lineX = x + (width - lineWidth) / 2;
      else if (para.align === 'right') lineX = x + width - lineWidth;

      let runX = lineX;
      ctx.textBaseline = 'top';

      for (const lr of lineRuns) {
        const fPx = lr.run.fontSize * scale * (96 / 72);
        const weight = lr.run.bold ? 'bold' : 'normal';
        const fontStyle = lr.run.italic ? 'italic' : 'normal';
        ctx.font = `${fontStyle} ${weight} ${fPx}px ${DEFAULT_FONT}`;
        ctx.fillStyle = lr.run.color;
        ctx.fillText(lr.text, runX, curY);
        runX += ctx.measureText(lr.text).width;
      }

      curY += lineH;
    }

    curY += paragraphSpace;
  }

  // Render outline if specified
  if (element.outline) {
    ctx.strokeStyle = element.outline.color || '#000000';
    ctx.lineWidth = (element.outline.width || 1) * scale;
    ctx.strokeRect(x, y, width, height);
  }

  ctx.restore();
}

/**
 * Render image element
 */
async function renderImageElement(
  ctx: SKRSContext2D,
  element: PPTImageElement,
  rc: RenderContext,
): Promise<void> {
  const { scale } = rc;
  const x = element.left * scale;
  const y = element.top * scale;
  const width = element.width * scale;
  const height = element.height * scale;

  ctx.save();

  // Apply rotation
  if (element.rotate) {
    const cx = x + width / 2;
    const cy = y + height / 2;
    ctx.translate(cx, cy);
    ctx.rotate((element.rotate * Math.PI) / 180);
    ctx.translate(-cx, -cy);
  }

  try {
    // Load and draw image
    if (element.src && !element.src.startsWith('placeholder:')) {
      const img = await loadImage(element.src);
      
      // Apply radius if specified
      if (element.radius) {
        ctx.beginPath();
        roundedRect(ctx, x, y, width, height, element.radius * scale);
        ctx.clip();
      }

      ctx.drawImage(img, x, y, width, height);
    }
  } catch (err) {
    // Draw placeholder on error
    ctx.fillStyle = '#e0e0e0';
    ctx.fillRect(x, y, width, height);
    ctx.strokeStyle = '#999999';
    ctx.strokeRect(x, y, width, height);
  }

  // Render outline if specified
  if (element.outline) {
    ctx.strokeStyle = element.outline.color || '#000000';
    ctx.lineWidth = (element.outline.width || 1) * scale;
    ctx.strokeRect(x, y, width, height);
  }

  ctx.restore();
}

/**
 * Render shape element
 */
function renderShapeElement(
  ctx: SKRSContext2D,
  element: PPTShapeElement,
  rc: RenderContext,
): void {
  const { scale } = rc;
  const x = element.left * scale;
  const y = element.top * scale;
  const width = element.width * scale;
  const height = element.height * scale;

  ctx.save();

  // Apply rotation
  if (element.rotate) {
    const cx = x + width / 2;
    const cy = y + height / 2;
    ctx.translate(cx, cy);
    ctx.rotate((element.rotate * Math.PI) / 180);
    ctx.translate(-cx, -cy);
  }

  // Create path from SVG path string
  ctx.beginPath();
  const path = element.path;
  const viewBox = element.viewBox || [1000, 1000];
  const scaleX = width / viewBox[0];
  const scaleY = height / viewBox[1];

  // Transform SVG path to canvas coordinates
  const commands = parseSvgPath(path);
  for (const cmd of commands) {
    const [type, ...args] = cmd;
    switch (type) {
      case 'M':
        ctx.moveTo(x + (args[0] ?? 0) * scaleX, y + (args[1] ?? 0) * scaleY);
        break;
      case 'L':
        ctx.lineTo(x + (args[0] ?? 0) * scaleX, y + (args[1] ?? 0) * scaleY);
        break;
      case 'C':
        ctx.bezierCurveTo(
          x + (args[0] ?? 0) * scaleX, y + (args[1] ?? 0) * scaleY,
          x + (args[2] ?? 0) * scaleX, y + (args[3] ?? 0) * scaleY,
          x + (args[4] ?? 0) * scaleX, y + (args[5] ?? 0) * scaleY,
        );
        break;
      case 'Q':
        ctx.quadraticCurveTo(
          x + (args[0] ?? 0) * scaleX, y + (args[1] ?? 0) * scaleY,
          x + (args[2] ?? 0) * scaleX, y + (args[3] ?? 0) * scaleY,
        );
        break;
      case 'A':
        // Approximate arc with bezier curves (simplified)
        ctx.lineTo(x + (args[5] ?? 0) * scaleX, y + (args[6] ?? 0) * scaleY);
        break;
      case 'Z':
        ctx.closePath();
        break;
    }
  }

  // Fill
  if (element.gradient) {
    renderGradientFill(ctx, element.gradient, x, y, width, height);
  } else {
    ctx.fillStyle = element.fill || '#5b9bd5';
  }
  ctx.fill();

  // Outline
  if (element.outline) {
    ctx.strokeStyle = element.outline.color || '#000000';
    ctx.lineWidth = (element.outline.width || 1) * scale;
    ctx.stroke();
  }

  // Render text inside shape if present
  if (element.text) {
    const fontSize = 16 * scale;
    ctx.font = `${fontSize}px ${DEFAULT_FONT}`;
    ctx.fillStyle = element.text.defaultColor || '#333333';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'center';
    
    const text = stripHtml(element.text.content);
    const textY = y + height / 2;
    ctx.fillText(text, x + width / 2, textY);
  }

  ctx.restore();
}

/**
 * Render line element
 */
function renderLineElement(
  ctx: SKRSContext2D,
  element: PPTLineElement,
  rc: RenderContext,
): void {
  const { scale } = rc;
  const startX = element.start[0] * scale;
  const startY = element.start[1] * scale;
  const endX = element.end[0] * scale;
  const endY = element.end[1] * scale;

  ctx.save();

  ctx.beginPath();
  ctx.moveTo(startX, startY);

  if (element.broken) {
    // Broken line (one control point)
    const midX = element.broken[0] * scale;
    const midY = element.broken[1] * scale;
    ctx.lineTo(midX, midY);
    ctx.lineTo(endX, endY);
  } else if (element.curve) {
    // Quadratic curve
    const cpX = element.curve[0] * scale;
    const cpY = element.curve[1] * scale;
    ctx.quadraticCurveTo(cpX, cpY, endX, endY);
  } else if (element.cubic) {
    // Cubic bezier
    const cp1X = element.cubic[0][0] * scale;
    const cp1Y = element.cubic[0][1] * scale;
    const cp2X = element.cubic[1][0] * scale;
    const cp2Y = element.cubic[1][1] * scale;
    ctx.bezierCurveTo(cp1X, cp1Y, cp2X, cp2Y, endX, endY);
  } else {
    ctx.lineTo(endX, endY);
  }

  ctx.strokeStyle = element.color || '#333333';
  ctx.lineWidth = element.width || 2;

  if (element.style === 'dashed') {
    ctx.setLineDash([10, 5]);
  } else if (element.style === 'dotted') {
    ctx.setLineDash([3, 3]);
  }

  ctx.stroke();

  // Draw arrowheads if specified
  if (element.points) {
    if (element.points[0] === 'arrow') {
      drawArrowhead(ctx, endX, endY, startX, startY, 10 * scale);
    }
    if (element.points[1] === 'arrow') {
      drawArrowhead(ctx, startX, startY, endX, endY, 10 * scale);
    }
  }

  ctx.restore();
}

/**
 * Draw an arrowhead
 */
function drawArrowhead(
  ctx: SKRSContext2D,
  x: number,
  y: number,
  fromX: number,
  fromY: number,
  size: number,
): void {
  const angle = Math.atan2(y - fromY, x - fromX);
  ctx.beginPath();
  ctx.moveTo(x, y);
  ctx.lineTo(
    x - size * Math.cos(angle - Math.PI / 6),
    y - size * Math.sin(angle - Math.PI / 6),
  );
  ctx.lineTo(
    x - size * Math.cos(angle + Math.PI / 6),
    y - size * Math.sin(angle + Math.PI / 6),
  );
  ctx.closePath();
  ctx.fill();
}

/**
 * Render chart element (simplified - render as placeholder)
 */
function renderChartElement(
  ctx: SKRSContext2D,
  element: PPTChartElement,
  rc: RenderContext,
): void {
  const { scale } = rc;
  const x = element.left * scale;
  const y = element.top * scale;
  const width = element.width * scale;
  const height = element.height * scale;

  ctx.save();

  // Fill background
  if (element.fill) {
    ctx.fillStyle = element.fill;
    ctx.fillRect(x, y, width, height);
  }

  // Render simple bar chart as placeholder
  const data = element.data;
  const themeColors = element.themeColors || ['#5b9bd5', '#ed7d31', '#a5a5a5'];
  const padding = 40 * scale;
  const chartWidth = width - padding * 2;
  const chartHeight = height - padding * 2;

  if (data && data.series && data.series.length > 0) {
    const barWidth = chartWidth / data.labels.length / (data.series.length + 1);
    const maxVal = Math.max(...data.series.flat()) || 1;

    for (let i = 0; i < data.series.length; i++) {
      const series = data.series[i];
      const color = themeColors[i % themeColors.length];

      for (let j = 0; j < series.length; j++) {
        const barHeight = (series[j] / maxVal) * chartHeight;
        const barX = x + padding + j * (chartWidth / data.labels.length) + i * barWidth;
        const barY = y + padding + chartHeight - barHeight;

        ctx.fillStyle = color;
        ctx.fillRect(barX, barY, barWidth * 0.8, barHeight);
      }
    }
  }

  // Outline
  if (element.outline) {
    ctx.strokeStyle = element.outline.color || '#000000';
    ctx.lineWidth = (element.outline.width || 1) * scale;
    ctx.strokeRect(x, y, width, height);
  }

  ctx.restore();
}

/**
 * Render table element
 */
function renderTableElement(
  ctx: SKRSContext2D,
  element: PPTTableElement,
  rc: RenderContext,
): void {
  const { scale } = rc;
  const x = element.left * scale;
  const y = element.top * scale;
  const width = element.width * scale;
  const height = element.height * scale;

  ctx.save();

  const data = element.data;
  const rows = data.length;
  const cols = rows > 0 ? data[0].length : 0;

  if (rows === 0 || cols === 0) {
    ctx.restore();
    return;
  }

  const cellWidth = width / cols;
  const cellHeight = height / rows;

  // Draw cells
  for (let i = 0; i < rows; i++) {
    for (let j = 0; j < cols; j++) {
      const cell = data[i][j];
      const cellX = x + j * cellWidth;
      const cellY = y + i * cellHeight;

      // Cell background
      if (cell.style?.backcolor) {
        ctx.fillStyle = cell.style.backcolor;
        ctx.fillRect(cellX, cellY, cellWidth, cellHeight);
      }

      // Cell text
      const fontSize = 14 * scale;
      ctx.font = `${cell.style?.bold ? 'bold ' : ''}${fontSize}px ${DEFAULT_FONT}`;
      ctx.fillStyle = cell.style?.color || '#333333';
      ctx.textBaseline = 'middle';
      ctx.textAlign = (cell.style?.align === 'justify' ? 'left' : cell.style?.align) || 'left';
      
      const textX = cellX + 8 * scale;
      const textY = cellY + cellHeight / 2;
      ctx.fillText(cell.text.slice(0, 30), textX, textY);
    }
  }

  // Draw grid
  ctx.strokeStyle = element.outline?.color || '#cccccc';
  ctx.lineWidth = (element.outline?.width || 1) * scale;

  for (let i = 0; i <= rows; i++) {
    ctx.beginPath();
    ctx.moveTo(x, y + i * cellHeight);
    ctx.lineTo(x + width, y + i * cellHeight);
    ctx.stroke();
  }

  for (let j = 0; j <= cols; j++) {
    ctx.beginPath();
    ctx.moveTo(x + j * cellWidth, y);
    ctx.lineTo(x + j * cellWidth, y + height);
    ctx.stroke();
  }

  ctx.restore();
}

/**
 * Render LaTeX element (simplified - render as text)
 */
function renderLatexElement(
  ctx: SKRSContext2D,
  element: PPTLatexElement,
  rc: RenderContext,
): void {
  const { scale } = rc;
  const x = element.left * scale;
  const y = element.top * scale;
  const width = element.width * scale;
  const height = (element.height || 80) * scale;

  ctx.save();

  // For server-side rendering, we display the raw LaTeX as text
  // A full implementation would use MathJax or KaTeX to render
  const fontSize = 18 * scale;
  ctx.font = `${fontSize}px ${DEFAULT_FONT}`;
  ctx.fillStyle = element.color || '#000000';
  ctx.textBaseline = 'middle';
  ctx.textAlign = element.align || 'center';

  const textY = y + height / 2;
  const textX = x + width / 2;
  ctx.fillText(element.latex.slice(0, 50), textX, textY);

  ctx.restore();
}

// ==================== Helper Functions ====================

/**
 * Wrap text runs into lines that fit within maxWidth
 */
interface LayoutRun {
  run: TextRun;
  text: string;
}

function wrapRuns(
  ctx: SKRSContext2D,
  runs: TextRun[],
  scale: number,
  maxWidth: number,
): LayoutRun[][] {
  const lines: LayoutRun[][] = [];
  let currentLine: LayoutRun[] = [];
  // Track actual rendered width of current line using measureText on accumulated text
  let currentLineWidth = 0;

  const flushLine = () => {
    lines.push(currentLine);
    currentLine = [];
    currentLineWidth = 0;
  };

  for (const run of runs) {
    // Handle explicit newline
    if (run.text === '\n') {
      flushLine();
      continue;
    }

    const fPx = run.fontSize * scale * (96 / 72);
    const weight = run.bold ? 'bold' : 'normal';
    const fontStyle = run.italic ? 'italic' : 'normal';
    ctx.font = `${fontStyle} ${weight} ${fPx}px ${DEFAULT_FONT}`;

    const chars = [...run.text];
    let buffer = '';

    for (const ch of chars) {
      const bufferWithCh = buffer + ch;
      const widthWithCh = ctx.measureText(bufferWithCh).width;

      if (currentLineWidth + widthWithCh > maxWidth && (buffer.length > 0 || currentLine.length > 0)) {
        // Need to wrap: flush buffer first
        if (buffer.length > 0) {
          currentLineWidth += ctx.measureText(buffer).width;
          currentLine.push({ run, text: buffer });
        }
        flushLine();
        buffer = ch;
      } else {
        buffer = bufferWithCh;
      }
    }

    if (buffer.length > 0) {
      currentLineWidth += ctx.measureText(buffer).width;
      currentLine.push({ run, text: buffer });
    }
  }

  if (currentLine.length > 0) {
    lines.push(currentLine);
  }

  return lines.length > 0 ? lines : [[]];
}

/**
 * Strip HTML tags from content
 */
function stripHtml(html: string): string {
  return html
    .replace(/<[^>]*>/g, '')
    .replace(/&nbsp;/g, ' ')
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"');
}

/**
 * Word wrap text to fit within a width
 */
function wrapText(ctx: SKRSContext2D, text: string, maxWidth: number): string[] {
  const words = text.split('');
  const lines: string[] = [];
  let currentLine = '';

  for (const char of words) {
    const testLine = currentLine + char;
    const metrics = ctx.measureText(testLine);

    if (metrics.width > maxWidth && currentLine.length > 0) {
      lines.push(currentLine);
      currentLine = char;
    } else {
      currentLine = testLine;
    }
  }

  if (currentLine) {
    lines.push(currentLine);
  }

  return lines;
}

/**
 * Parse SVG path string into commands
 */
function parseSvgPath(path: string): [string, ...number[]][] {
  const commands: [string, ...number[]][] = [];
  const regex = /([MLHVCSQTAZ])([^MLHVCSQTAZ]*)/gi;
  let match;

  while ((match = regex.exec(path)) !== null) {
    const type = match[1].toUpperCase();
    const args = match[2]
      .trim()
      .split(/[\s,]+/)
      .map(parseFloat)
      .filter((n) => !isNaN(n));
    commands.push([type, ...args]);
  }

  return commands;
}

/**
 * Draw a rounded rectangle
 */
function roundedRect(
  ctx: SKRSContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number,
): void {
  ctx.moveTo(x + radius, y);
  ctx.lineTo(x + width - radius, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
  ctx.lineTo(x + width, y + height - radius);
  ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
  ctx.lineTo(x + radius, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
  ctx.lineTo(x, y + radius);
  ctx.quadraticCurveTo(x, y, x + radius, y);
}


